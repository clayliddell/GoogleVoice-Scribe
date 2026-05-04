from __future__ import annotations

from pathlib import Path

from app.sessions import (
    MAX_FOLDER_NAME_LENGTH,
    SessionRecord,
    append_incremental_reference_text,
    unique_final_path,
)


def test_unique_final_path_keeps_windows_component_under_limit(tmp_path):
    timestamp = "20260504T113056-0500"
    subject = "a" * 400

    path = unique_final_path(tmp_path, timestamp, subject)

    assert path.parent == tmp_path
    assert len(path.name) <= MAX_FOLDER_NAME_LENGTH
    assert path.name.startswith(timestamp)


def test_append_incremental_reference_text_trims_repeated_prefix(tmp_path):
    record = minimal_record(tmp_path)
    append_incremental_reference_text(record, "caller", "hello there thanks for calling")
    append_incremental_reference_text(record, "caller", "thanks for calling how can I help")

    assert record.incremental_reference_text["caller"] == "hello there thanks for calling how can I help"


def minimal_record(tmp_path: Path) -> SessionRecord:
    return SessionRecord(
        session_id="test",
        session_dir=tmp_path,
        pcm_path=tmp_path / "audio.pcm",
        wav_path=tmp_path / "audio.wav",
        mic_pcm_path=tmp_path / "you.pcm",
        mic_wav_path=tmp_path / "you.wav",
        caller_pcm_path=tmp_path / "caller.pcm",
        caller_wav_path=tmp_path / "caller.wav",
        compressed_audio_path=tmp_path / "audio.opus",
        transcript_path=tmp_path / "transcript.json",
        conversation_path=tmp_path / "conversation.txt",
        metadata_path=tmp_path / "session.json",
        started_at="2026-05-04T00:00:00+00:00",
        source="test",
        tab_id=None,
        tab_url="",
        page_title="",
        trigger_label="",
        callee_label="",
        transcript_mode="speaker_attributed_asr",
        audio_mode="test",
        incremental_reference_text={"mic": "", "caller": ""},
        incremental_reference_transcribe_seconds={"mic": 0.0, "caller": 0.0},
        incremental_reference_audio_seconds={"mic": 0.0, "caller": 0.0},
        incremental_reference_tasks_completed={"mic": 0, "caller": 0},
    )
