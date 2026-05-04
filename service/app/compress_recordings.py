from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .audio import compressed_audio_path
from .config import Settings
from .sessions import compress_recording_audio


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    root = args.root or settings.recordings_dir
    count = 0
    skipped = 0
    failed = 0

    for wav_path in root.rglob("audio.wav"):
        if "_tmp" in wav_path.parts:
            continue

        output_path = compressed_audio_path(wav_path, output_format=settings.compressed_audio_format)
        if output_path.exists() and output_path.stat().st_size > 0 and not args.force:
            skipped += 1
            continue

        error = compress_recording_audio(
            wav_path,
            output_path,
            output_format=settings.compressed_audio_format,
            bitrate=settings.opus_bitrate,
        )
        transcript_path = wav_path.with_name("transcript.json")
        session_path = wav_path.with_name("session.json")
        update_payload_file(transcript_path, output_path, error)
        update_payload_file(session_path, output_path, error)

        if error:
            failed += 1
            print(f"Failed {wav_path}: {error}")
        else:
            count += 1
            print(f"Wrote {output_path}")

    print(f"Compressed {count} recording(s), skipped {skipped}, failed {failed}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create missing compressed playback audio for existing recordings.")
    parser.add_argument("--root", type=Path, help="Recording root to scan. Defaults to GV_RECORDINGS_DIR.")
    parser.add_argument("--force", action="store_true", help="Recreate audio.opus even when it already exists.")
    return parser.parse_args()


def update_payload_file(path: Path, output_path: Path, error: str | None) -> None:
    if not path.exists():
        return

    payload = read_json(path)
    if not payload:
        return

    payload["compressed_audio_path"] = str(output_path)
    payload["compressed_audio_error"] = error
    capture = payload.get("capture")
    if isinstance(capture, dict):
        capture["compressed_audio_format"] = "opus"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


if __name__ == "__main__":
    main()
