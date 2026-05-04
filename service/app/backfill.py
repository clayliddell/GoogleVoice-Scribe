from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .conversation import build_conversation


def main() -> None:
    settings = Settings.from_env()
    count = 0

    for transcript_path in settings.recordings_dir.rglob("transcript.json"):
        if "_tmp" in transcript_path.parts:
            continue

        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        if payload.get("status") != "transcribed":
            continue

        callee = "Callee"
        speaker_map = payload.get("speaker_map") or {}
        segments = payload.get("segments") or []

        if speaker_map:
            for segment in segments:
                for turn in segment.get("speaker_turns") or []:
                    mapped = speaker_map.get(turn.get("speaker"), turn.get("speaker"))
                    turn["speaker"] = "You" if mapped == "You" else "Callee"

        conversation_text, _ = build_conversation(segments, callee_name=callee)
        if not conversation_text:
            continue

        conversation_path = transcript_path.with_name("conversation.txt")
        conversation_path.write_text(conversation_text, encoding="utf-8")
        payload["conversation_path"] = str(conversation_path)
        transcript_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        count += 1

    print(f"Backfilled {count} conversation.txt file(s).")


if __name__ == "__main__":
    main()
