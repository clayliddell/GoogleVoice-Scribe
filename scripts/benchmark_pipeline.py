from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "service"
sys.path.insert(0, str(SERVICE_DIR))

from app.audio import load_wav_mono_float32, resample_linear  # noqa: E402


TERMINAL_STATUSES = {"transcribed", "transcription_failed", "aborted"}
TRACKS = ("mixed", "callee", "mic")
TRACK_FILES = {
    "mixed": "audio.wav",
    "callee": "callee.wav",
    "mic": "you.wav",
}
LEGACY_TRACK_FILES = {
    "callee": "caller.wav",
}
COMPRESSED_TRACK_FILES = {
    "mixed": "audio.opus",
}
TIMING_FIELDS = (
    "mixed_transcribe_seconds",
    "mixed_transcription_source",
    "incremental_transcribe_seconds",
    "incremental_mixed_transcribe_seconds",
    "incremental_audio_seconds",
    "incremental_boundary_decision_count",
    "incremental_boundary_reasons",
    "incremental_reference_transcription",
    "incremental_reference_transcribe_seconds",
    "incremental_reference_audio_seconds",
    "incremental_reference_tasks_completed",
    "you_reference_transcribe_seconds",
    "you_reference_audio_seconds",
    "you_reference_source",
    "callee_reference_transcribe_seconds",
    "callee_reference_audio_seconds",
    "callee_reference_source",
    "speaker_reference_mode",
    "title_generation_seconds",
    "conversation_build_seconds",
    "transcription_total_seconds",
)


def main() -> None:
    args = parse_args()
    service_url = args.service_url.rstrip("/")
    health = http_json("GET", f"{service_url}/health")

    source_session = resolve_source_session(args.source_session, health, args.allow_benchmark_source)
    print(f"source_session={source_session}", flush=True)
    print(f"service_url={service_url}", flush=True)

    reports: list[Path] = []
    for run_index in range(1, args.runs + 1):
        report = run_benchmark(args, service_url, health, source_session, run_index)
        reports.append(report)
        print(f"report_path={report}", flush=True)

    if len(reports) > 1:
        print("reports:", flush=True)
        for report in reports:
            print(f"  {report}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay an existing call through the local service API and measure the full transcription pipeline."
    )
    parser.add_argument("--service-url", default="http://127.0.0.1:8765")
    parser.add_argument("--source-session", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=900.0)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--chunk-frames", type=int, default=4096)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--allow-benchmark-source", action="store_true")
    parser.add_argument("--realtime-upload", action="store_true", help="Pace chunk upload to match the source audio duration.")
    return parser.parse_args()


def run_benchmark(
    args: argparse.Namespace,
    service_url: str,
    health: dict[str, Any],
    source_session: Path,
    run_index: int,
) -> Path:
    prepared = prepare_audio_fixture(source_session, args.duration_seconds, args.sample_rate)
    chunk_count = math.ceil(prepared["target_samples"] / args.chunk_frames)
    run_started = datetime.now(timezone.utc)
    report_dir = REPO_ROOT / "benchmarks"
    report_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "benchmark": "google_voice_transcriber_service_pipeline",
        "run_index": run_index,
        "started_at": run_started.isoformat(),
        "service_url": service_url,
        "health": health,
        "source_session": str(source_session),
        "duration_seconds": args.duration_seconds,
        "sample_rate": args.sample_rate,
        "chunk_frames": args.chunk_frames,
        "chunk_count_per_track": chunk_count,
        "realtime_upload": args.realtime_upload,
        "upload_audio_seconds_per_sequence": round(args.chunk_frames / float(args.sample_rate), 6)
        if args.sample_rate
        else None,
        "upload_target_wall_seconds": round(prepared["target_samples"] / float(args.sample_rate), 3)
        if args.sample_rate
        else None,
        "target_samples": prepared["target_samples"],
        "tracks": prepared["track_metadata"],
        "gpu_before": nvidia_smi_snapshot(),
    }

    start_response = start_session(service_url, run_started)
    session_id = start_response["session_id"]
    report["session_id"] = session_id
    report["initial_session_dir"] = start_response.get("session_dir")
    print(f"run={run_index} session_id={session_id}", flush=True)

    upload_started = time.perf_counter()
    bytes_uploaded = upload_tracks(
        service_url,
        session_id,
        prepared["tracks"],
        sample_rate=args.sample_rate,
        chunk_frames=args.chunk_frames,
        progress_every=args.progress_every,
        realtime_upload=args.realtime_upload,
        report=report,
    )
    report["upload_wall_seconds"] = elapsed(upload_started)
    report["bytes_uploaded"] = bytes_uploaded
    report["chunks_uploaded"] = {track: chunk_count for track in TRACKS}

    finish_started = time.perf_counter()
    finish_response = finish_session(
        service_url,
        session_id,
        sample_rate=args.sample_rate,
        chunk_count=chunk_count,
        peaks=prepared["peaks"],
        mic_captured=prepared["mic_source_exists"],
    )
    report["finish_wall_seconds"] = elapsed(finish_started)
    report["finish_response"] = finish_response

    transcribe_started = time.perf_counter()
    final_status = poll_session(
        service_url,
        session_id,
        interval_seconds=args.poll_interval_seconds,
        timeout_seconds=args.poll_timeout_seconds,
    )
    post_call_seconds = elapsed(transcribe_started)
    report["post_call_transcription_wall_seconds"] = post_call_seconds
    report["status_response"] = final_status
    report["final_status"] = final_status.get("status")
    report["final_session_dir"] = final_status.get("session_dir")
    report["audio_duration_seconds"] = args.duration_seconds
    report["post_call_real_time_factor"] = round(post_call_seconds / args.duration_seconds, 4) if args.duration_seconds else None
    report["total_replay_wall_seconds"] = round(
        report["upload_wall_seconds"] + report["finish_wall_seconds"] + post_call_seconds,
        3,
    )

    transcript = read_json_path(final_status.get("transcript_path"))
    report["transcript"] = summarize_transcript(transcript)
    report["timings"] = transcript_timings(transcript)
    report["conversation"] = summarize_text_path(final_status.get("conversation_path"))
    report["session_json"] = read_json_path(final_status.get("session_dir"), "session.json")
    report["gpu_after"] = nvidia_smi_snapshot()
    report["ended_at"] = datetime.now(timezone.utc).isoformat()

    report_path = report_dir / f"{run_started.strftime('%Y%m%dT%H%M%SZ')}_run{run_index}_{session_id}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print_summary(report)
    return report_path


def prepare_audio_fixture(source_session: Path, duration_seconds: float, sample_rate: int) -> dict[str, Any]:
    target_samples = max(1, int(round(duration_seconds * sample_rate)))
    tracks: dict[str, np.ndarray] = {}
    track_metadata: dict[str, dict[str, Any]] = {}
    peaks: dict[str, float] = {}

    for track in TRACKS:
        source = resolve_track_source(source_session, track)
        required = track == "mixed"
        source_exists = source is not None
        if not source_exists and required:
            raise SystemExit(f"Missing required source audio: {source_session / TRACK_FILES[track]}")

        if source is not None:
            audio, source_rate = load_wav_mono_float32(source["load_path"])
            audio = resample_linear(audio, source_rate, sample_rate)
            audio = fit_duration(audio, target_samples)
            pcm = float_audio_to_pcm16(audio)
            source_duration = source_path_duration(source["load_path"])
        else:
            pcm = np.zeros(target_samples, dtype=np.int16)
            source_rate = sample_rate
            source_duration = 0.0

        tracks[track] = pcm
        peaks[track] = round(float(np.max(np.abs(pcm.astype(np.float32))) / 32768.0), 6) if pcm.size else 0.0
        track_metadata[track] = {
            "source_path": str(source["source_path"]) if source else str(source_session / TRACK_FILES[track]),
            "load_path": str(source["load_path"]) if source else "",
            "source_format": source["source_format"] if source else "missing",
            "source_exists": source_exists,
            "source_sample_rate": source_rate,
            "source_duration_seconds": source_duration,
            "target_samples": int(pcm.size),
            "target_duration_seconds": round(pcm.size / float(sample_rate), 3),
            "peak": peaks[track],
        }

    return {
        "target_samples": target_samples,
        "tracks": tracks,
        "track_metadata": track_metadata,
        "peaks": peaks,
        "mic_source_exists": track_metadata["mic"]["source_exists"],
    }


def resolve_track_source(source_session: Path, track: str) -> dict[str, Any] | None:
    source_path = source_session / TRACK_FILES[track]
    if is_usable_audio_file(source_path):
        return {"source_path": source_path, "load_path": source_path, "source_format": "wav"}

    legacy_filename = LEGACY_TRACK_FILES.get(track)
    if legacy_filename:
        legacy_path = source_session / legacy_filename
        if is_usable_audio_file(legacy_path):
            return {"source_path": legacy_path, "load_path": legacy_path, "source_format": "legacy_wav"}

    compressed_filename = COMPRESSED_TRACK_FILES.get(track)
    if compressed_filename:
        compressed_path = source_session / compressed_filename
        if is_usable_audio_file(compressed_path):
            wav_path = decode_compressed_audio_to_wav(compressed_path)
            return {"source_path": compressed_path, "load_path": wav_path, "source_format": "opus"}

    return None


def is_usable_audio_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 44


def decode_compressed_audio_to_wav(source_path: Path) -> Path:
    from imageio_ffmpeg import get_ffmpeg_exe

    fixtures_dir = REPO_ROOT / "benchmarks" / "_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(str(source_path.resolve()).encode("utf-8")).hexdigest()[:12]
    source_stat = source_path.stat()
    wav_path = fixtures_dir / f"{source_path.stem}_{fingerprint}_{int(source_stat.st_mtime)}.wav"
    if is_usable_audio_file(wav_path) and wav_path.stat().st_mtime >= source_stat.st_mtime:
        return wav_path

    command = [
        get_ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        raise SystemExit(error or f"ffmpeg exited with status {result.returncode} while decoding {source_path}")
    if not is_usable_audio_file(wav_path):
        raise SystemExit(f"ffmpeg did not create a usable WAV fixture: {wav_path}")
    return wav_path


def fit_duration(audio: np.ndarray, target_samples: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros(target_samples, dtype=np.float32)
    if audio.size >= target_samples:
        return audio[:target_samples].astype(np.float32, copy=False)

    repeats = math.ceil(target_samples / audio.size)
    return np.tile(audio, repeats)[:target_samples].astype(np.float32, copy=False)


def float_audio_to_pcm16(audio: np.ndarray) -> np.ndarray:
    clipped = np.clip(audio, -1.0, 1.0)
    scaled = np.where(clipped < 0, clipped * 0x8000, clipped * 0x7FFF)
    return scaled.astype("<i2", copy=False)


def source_path_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() <= 0:
            return 0.0
        return round(wav.getnframes() / float(wav.getframerate()), 3)


def start_session(service_url: str, started_at: datetime) -> dict[str, Any]:
    return http_json(
        "POST",
        f"{service_url}/sessions/start",
        {
            "source": "benchmark",
            "tab_id": None,
            "tab_url": "",
            "page_title": "15-minute pipeline benchmark",
            "started_at": started_at.isoformat(),
            "trigger_label": "benchmark",
            "callee_label": "",
            "transcript_mode": "speaker_attributed_asr",
            "audio_mode": "benchmark_replay_pcm",
            "mic_required": False,
            "mic_device_id": "",
        },
    )


def upload_tracks(
    service_url: str,
    session_id: str,
    tracks: dict[str, np.ndarray],
    *,
    sample_rate: int,
    chunk_frames: int,
    progress_every: int,
    realtime_upload: bool,
    report: dict[str, Any],
) -> int:
    chunk_count = max(math.ceil(len(tracks["mixed"]) / chunk_frames), 1)
    bytes_uploaded = 0
    pacing_sleep_seconds = 0.0
    upload_started = time.perf_counter()

    for sequence in range(chunk_count):
        start = sequence * chunk_frames
        end = start + chunk_frames
        for track in TRACKS:
            chunk = tracks[track][start:end]
            if chunk.size == 0:
                continue
            body = chunk.astype("<i2", copy=False).tobytes()
            upload_chunk(
                service_url,
                session_id,
                track=track,
                sequence=sequence,
                sample_rate=sample_rate,
                body=body,
            )
            bytes_uploaded += len(body)

        target_elapsed = min((sequence + 1) * chunk_frames, len(tracks["mixed"])) / float(sample_rate)
        if realtime_upload:
            sleep_seconds = target_elapsed - (time.perf_counter() - upload_started)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
                pacing_sleep_seconds += sleep_seconds

        if progress_every > 0 and (sequence + 1) % progress_every == 0:
            if realtime_upload:
                elapsed_seconds = time.perf_counter() - upload_started
                print(
                    f"uploaded_sequence={sequence + 1}/{chunk_count} "
                    f"realtime_elapsed={elapsed_seconds:.1f}s target={target_elapsed:.1f}s",
                    flush=True,
                )
            else:
                print(f"uploaded_sequence={sequence + 1}/{chunk_count}", flush=True)

    report["upload_pacing_sleep_seconds"] = round(pacing_sleep_seconds, 3)
    return bytes_uploaded


def upload_chunk(
    service_url: str,
    session_id: str,
    *,
    track: str,
    sequence: int,
    sample_rate: int,
    body: bytes,
) -> None:
    query = urllib.parse.urlencode(
        {
            "track": track,
            "sequence": sequence,
            "sample_rate": sample_rate,
            "channels": 1,
        }
    )
    http_bytes(
        "POST",
        f"{service_url}/sessions/{session_id}/chunk?{query}",
        body,
        content_type="application/octet-stream",
    )


def finish_session(
    service_url: str,
    session_id: str,
    *,
    sample_rate: int,
    chunk_count: int,
    peaks: dict[str, float],
    mic_captured: bool,
) -> dict[str, Any]:
    track_chunks = {track: chunk_count for track in TRACKS}
    return http_json(
        "POST",
        f"{service_url}/sessions/{session_id}/finish",
        {
            "reason": "benchmark_finished",
            "chunks": chunk_count,
            "dropped_chunks": 0,
            "upload_errors": [],
            "track_chunks": track_chunks,
            "track_dropped_chunks": {track: 0 for track in TRACKS},
            "track_upload_errors": {track: [] for track in TRACKS},
            "sample_rate": sample_rate,
            "channels": 1,
            "mic_captured": mic_captured,
            "mic_error": None if mic_captured else "No source you.wav; benchmark used silence.",
            "mic_track_label": "benchmark source you.wav" if mic_captured else "",
            "mic_peak": peaks["mic"],
            "tab_peak": peaks["callee"],
            "mixed_peak": peaks["mixed"],
            "ended_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def poll_session(
    service_url: str,
    session_id: str,
    *,
    interval_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while True:
        payload = http_json("GET", f"{service_url}/sessions/{session_id}")
        status = str(payload.get("status") or "")
        if status != last_status:
            print(f"status={status}", flush=True)
            last_status = status
        if status in TERMINAL_STATUSES:
            return payload
        if time.monotonic() >= deadline:
            raise SystemExit(f"Timed out waiting for session {session_id}; last status was {status!r}.")
        time.sleep(interval_seconds)


def resolve_source_session(source_session: Path | None, health: dict[str, Any], allow_benchmark_source: bool) -> Path:
    if source_session is not None:
        path = source_session.expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"Source session is not a directory: {path}")
        return path

    recordings_dir = Path(str(health.get("recordings_dir") or "")).expanduser()
    if not recordings_dir.exists():
        raise SystemExit(f"Recordings directory does not exist: {recordings_dir}")

    candidates: list[tuple[float, Path]] = []
    for transcript_path in recordings_dir.rglob("transcript.json"):
        session_dir = transcript_path.parent
        if "_tmp" in session_dir.parts:
            continue
        if not (session_dir / "audio.wav").exists() and not (session_dir / "audio.opus").exists():
            continue
        payload = read_json_path(transcript_path)
        if payload.get("status") != "transcribed":
            continue
        session_payload = read_json_path(session_dir / "session.json")
        if not allow_benchmark_source and session_payload.get("source") == "benchmark":
            continue
        candidates.append((transcript_path.stat().st_mtime, session_dir))

    if not candidates:
        raise SystemExit("No completed source session found. Pass --source-session <session-folder>.")

    return max(candidates, key=lambda item: item[0])[1]


def summarize_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    full_text = str(payload.get("full_text") or "")
    incremental = payload.get("incremental_transcription") or {}
    boundary_decisions = incremental.get("boundary_decisions") or []
    return {
        "status": payload.get("status"),
        "subject": payload.get("subject"),
        "callee": payload.get("callee"),
        "error": payload.get("error"),
        "full_text_chars": len(full_text),
        "segment_count": len(payload.get("segments") or []),
        "boundary_decision_count": len(boundary_decisions),
        "model": payload.get("model"),
        "mode": payload.get("mode"),
    }


def transcript_timings(payload: dict[str, Any]) -> dict[str, Any]:
    timings = dict(payload.get("timings") or {})
    for field in TIMING_FIELDS:
        if field in payload:
            timings[field] = payload[field]
    return timings


def summarize_text_path(path_value: Any) -> dict[str, Any]:
    if not path_value:
        return {"exists": False}
    path = Path(str(path_value))
    if not path.exists():
        return {"exists": False, "path": str(path)}
    text = path.read_text(encoding="utf-8")
    return {
        "exists": True,
        "path": str(path),
        "chars": len(text),
        "lines": len(text.splitlines()),
    }


def read_json_path(path_value: Any, child: str | None = None) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(str(path_value))
    if child is not None:
        path = path / child
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    body = http_bytes(method, url, data, content_type=headers.get("Content-Type"))
    return json.loads(body.decode("utf-8")) if body else {}


def http_bytes(method: str, url: str, data: bytes | None, *, content_type: str | None = None) -> bytes:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {error.code} for {method} {url}: {detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"Could not connect to {url}: {error}") from error


def nvidia_smi_snapshot() -> dict[str, Any] | None:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).strip()
    except Exception:
        return None

    if not output:
        return None
    values = [part.strip() for part in output.splitlines()[0].split(",")]
    if len(values) != 5:
        return {"raw": output}
    return {
        "name": values[0],
        "memory_used_mib": parse_int(values[1]),
        "memory_total_mib": parse_int(values[2]),
        "utilization_gpu_percent": parse_int(values[3]),
        "temperature_c": parse_int(values[4]),
    }


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def elapsed(started: float) -> float:
    return round(time.perf_counter() - started, 3)


def print_summary(report: dict[str, Any]) -> None:
    print("benchmark_summary:", flush=True)
    print(f"  status={report.get('final_status')}", flush=True)
    print(f"  final_session_dir={report.get('final_session_dir')}", flush=True)
    print(f"  upload_wall_seconds={report.get('upload_wall_seconds')}", flush=True)
    print(f"  post_call_transcription_wall_seconds={report.get('post_call_transcription_wall_seconds')}", flush=True)
    print(f"  post_call_real_time_factor={report.get('post_call_real_time_factor')}", flush=True)
    print(f"  timings={report.get('timings')}", flush=True)


if __name__ == "__main__":
    main()
