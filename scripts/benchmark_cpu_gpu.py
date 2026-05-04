from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from script_common import REPO_ROOT, SERVICE_ROOT, repo_python, windows_creationflags


def main() -> int:
    args = parse_args()
    python = repo_python()
    service_url = f"http://127.0.0.1:{args.port}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary_root = REPO_ROOT / "benchmarks" / "cpu-gpu"
    log_root = summary_root / "logs"
    summary_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    processes: list[subprocess.Popen] = []
    saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        for mode in args.mode_order:
            if is_port_open(args.port):
                raise SystemExit(f"Port {args.port} is already accepting connections. Stop that service or pass --port <free-port>.")

            process = start_server(mode, args.port, timestamp, log_root, python)
            processes.append(process)
            expected_force_cpu = mode == "cpu"
            health = wait_for_health(service_url, expected_force_cpu, process, log_root)
            print(f"server_mode={mode} pid={process.pid} force_cpu={health.get('force_cpu')}", flush=True)

            try:
                if args.warmup_runs > 0:
                    print(f"warmup_mode={mode} runs={args.warmup_runs}", flush=True)
                    for report in invoke_pipeline(args, service_url, mode, "warmup", args.warmup_runs, python):
                        records.append(read_run_record(mode, "warmup", report))

                print(f"measured_mode={mode} runs={args.measured_runs}", flush=True)
                for report in invoke_pipeline(args, service_url, mode, "measured", args.measured_runs, python):
                    records.append(read_run_record(mode, "measured", report))
            finally:
                stop_server(process)
                processes.remove(process)
                time.sleep(3)

        summary = build_summary(args, service_url, records)
        json_path = summary_root / f"{timestamp}_cpu_gpu_summary.json"
        csv_path = summary_root / f"{timestamp}_cpu_gpu_summary.csv"
        json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
        write_csv(csv_path, records)
        print_summary(summary)
        print(f"summary_json={json_path}")
        print(f"summary_csv={csv_path}")
        return 0
    finally:
        for process in list(processes):
            stop_server(process)
        restore_env(saved_env)


ENV_KEYS = ("GV_SERVICE_HOST", "GV_SERVICE_PORT", "GV_FORCE_CPU", "GV_WARM_GRANITE_ON_CALL_START")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare warm GoogleVoice Scribe pipeline performance on GPU vs CPU.")
    parser.add_argument("--source-session", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=900.0)
    parser.add_argument("--warmup-duration-seconds", type=float, default=30.0)
    parser.add_argument("--measured-runs", type=int, default=2)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--chunk-frames", type=int, default=4096)
    parser.add_argument("--poll-timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--mode-order", nargs="+", choices=("gpu", "cpu"), default=["gpu", "cpu"])
    return parser.parse_args()


def start_server(mode: str, port: int, timestamp: str, log_root: Path, python: Path) -> subprocess.Popen:
    force_cpu = "1" if mode == "cpu" else "0"
    os.environ["GV_SERVICE_HOST"] = "127.0.0.1"
    os.environ["GV_SERVICE_PORT"] = str(port)
    os.environ["GV_FORCE_CPU"] = force_cpu
    os.environ["GV_WARM_GRANITE_ON_CALL_START"] = "1"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVICE_ROOT)
    stdout_path = log_root / f"{timestamp}_{mode}_server.out.log"
    stderr_path = log_root / f"{timestamp}_{mode}_server.err.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    try:
        process = subprocess.Popen(
            [str(python), "-m", "app.cli", "--host", "127.0.0.1", "--port", str(port)],
            cwd=SERVICE_ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=windows_creationflags(),
        )
    finally:
        stdout.close()
        stderr.close()
    return process


def stop_server(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)


def wait_for_health(service_url: str, expected_force_cpu: bool, process: subprocess.Popen, log_root: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"Benchmark server exited early with code {process.returncode}. Check logs under {log_root}.")
        try:
            payload = http_json(f"{service_url}/health", timeout=5)
            if bool(payload.get("force_cpu")) != expected_force_cpu:
                raise RuntimeError(f"Health check returned force_cpu={payload.get('force_cpu')}, expected {expected_force_cpu}.")
            return payload
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {service_url}/health. Last error: {last_error}")


def invoke_pipeline(args: argparse.Namespace, service_url: str, mode: str, phase: str, runs: int, python: Path) -> list[Path]:
    if runs <= 0:
        return []

    duration = args.warmup_duration_seconds if phase == "warmup" else args.duration_seconds
    command = [
        str(python),
        str(REPO_ROOT / "scripts" / "benchmark_pipeline.py"),
        "--service-url",
        service_url,
        "--duration-seconds",
        str(duration),
        "--sample-rate",
        str(args.sample_rate),
        "--chunk-frames",
        str(args.chunk_frames),
        "--runs",
        str(runs),
        "--poll-timeout-seconds",
        str(args.poll_timeout_seconds),
        "--realtime-upload",
        "--progress-every",
        "1000",
    ]
    if args.source_session:
        command.extend(["--source-session", str(args.source_session)])

    process = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert process.stdout is not None
    reports: list[Path] = []
    for line in process.stdout:
        text = line.rstrip()
        print(f"[{mode}/{phase}] {text}", flush=True)
        if text.startswith("report_path="):
            reports.append(Path(text.removeprefix("report_path=").strip()))
    exit_code = process.wait()
    if exit_code != 0:
        raise SystemExit(f"benchmark_pipeline.py failed for mode={mode} phase={phase} with exit code {exit_code}.")
    if len(reports) != runs:
        raise SystemExit(f"Expected {runs} report_path lines for mode={mode} phase={phase}, found {len(reports)}.")
    return reports


def read_run_record(mode: str, phase: str, report_path: Path) -> dict[str, Any]:
    payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    timings = payload.get("timings") or {}
    gpu_before = payload.get("gpu_before") or {}
    gpu_after = payload.get("gpu_after") or {}
    health = payload.get("health") or {}
    return {
        "mode": mode,
        "phase": phase,
        "run_index": payload.get("run_index"),
        "report_path": str(report_path),
        "final_status": payload.get("final_status"),
        "force_cpu": bool(health.get("force_cpu")),
        "duration_seconds": number(payload.get("duration_seconds")),
        "realtime_upload": bool(payload.get("realtime_upload")),
        "upload_wall_seconds": number(payload.get("upload_wall_seconds")),
        "post_call_transcription_wall_seconds": number(payload.get("post_call_transcription_wall_seconds")),
        "post_call_real_time_factor": number(payload.get("post_call_real_time_factor")),
        "total_replay_wall_seconds": number(payload.get("total_replay_wall_seconds")),
        "transcription_total_seconds": number(timings.get("transcription_total_seconds")),
        "mixed_transcribe_seconds": number(timings.get("mixed_transcribe_seconds")),
        "incremental_mixed_transcribe_seconds": number(timings.get("incremental_mixed_transcribe_seconds")),
        "you_reference_transcribe_seconds": number(timings.get("you_reference_transcribe_seconds")),
        "callee_reference_transcribe_seconds": number(timings.get("callee_reference_transcribe_seconds")),
        "title_generation_seconds": number(timings.get("title_generation_seconds")),
        "conversation_build_seconds": number(timings.get("conversation_build_seconds")),
        "gpu_before_memory_used_mib": number(gpu_before.get("memory_used_mib")),
        "gpu_after_memory_used_mib": number(gpu_after.get("memory_used_mib")),
        "gpu_after_utilization_percent": number(gpu_after.get("utilization_gpu_percent")),
        "final_session_dir": payload.get("final_session_dir"),
    }


def build_summary(args: argparse.Namespace, service_url: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    aggregates = []
    for mode in args.mode_order:
        items = [item for item in records if item["mode"] == mode and item["phase"] == "measured"]
        if not items:
            continue
        aggregates.append(
            {
                "mode": mode,
                "measured_runs": len(items),
                "avg_post_call_transcription_wall_seconds": average(items, "post_call_transcription_wall_seconds"),
                "avg_post_call_real_time_factor": average(items, "post_call_real_time_factor"),
                "avg_transcription_total_seconds": average(items, "transcription_total_seconds"),
                "avg_mixed_transcribe_seconds": average(items, "mixed_transcribe_seconds"),
                "avg_incremental_mixed_transcribe_seconds": average(items, "incremental_mixed_transcribe_seconds"),
                "avg_you_reference_transcribe_seconds": average(items, "you_reference_transcribe_seconds"),
                "avg_callee_reference_transcribe_seconds": average(items, "callee_reference_transcribe_seconds"),
                "avg_title_generation_seconds": average(items, "title_generation_seconds"),
                "avg_gpu_after_memory_used_mib": average(items, "gpu_after_memory_used_mib"),
                "avg_gpu_after_utilization_percent": average(items, "gpu_after_utilization_percent"),
            }
        )

    by_mode = {item["mode"]: item for item in aggregates}
    speedups = {}
    if "gpu" in by_mode and "cpu" in by_mode:
        speedups["post_call_transcription_wall_seconds_cpu_over_gpu"] = divide(
            by_mode["cpu"]["avg_post_call_transcription_wall_seconds"],
            by_mode["gpu"]["avg_post_call_transcription_wall_seconds"],
        )
        speedups["transcription_total_seconds_cpu_over_gpu"] = divide(
            by_mode["cpu"]["avg_transcription_total_seconds"],
            by_mode["gpu"]["avg_transcription_total_seconds"],
        )

    return {
        "benchmark": "google_voice_cpu_gpu_comparison",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "service_url": service_url,
        "duration_seconds": args.duration_seconds,
        "warmup_duration_seconds": args.warmup_duration_seconds,
        "sample_rate": args.sample_rate,
        "chunk_frames": args.chunk_frames,
        "measured_runs_per_mode": args.measured_runs,
        "warmup_runs_per_mode": args.warmup_runs,
        "mode_order": args.mode_order,
        "source_session": str(args.source_session or ""),
        "aggregates": aggregates,
        "speedups": speedups,
        "reports": records,
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for record in records for key in record})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def print_summary(summary: dict[str, Any]) -> None:
    print("")
    print("cpu_gpu_summary:")
    for item in summary["aggregates"]:
        print(
            f"{item['mode']}: runs={item['measured_runs']} "
            f"post_call={item['avg_post_call_transcription_wall_seconds']}s "
            f"rtf={item['avg_post_call_real_time_factor']} "
            f"transcribe_total={item['avg_transcription_total_seconds']}s",
            flush=True,
        )


def http_json(url: str, *, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def restore_env(values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def average(items: list[dict[str, Any]], key: str) -> float | None:
    values = [float(item[key]) for item in items if item.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 3)


if __name__ == "__main__":
    raise SystemExit(main())
