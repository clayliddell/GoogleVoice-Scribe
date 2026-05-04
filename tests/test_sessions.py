from __future__ import annotations

from pathlib import Path

from app.sessions import (
    MAX_FOLDER_NAME_LENGTH,
    SessionRecord,
    append_incremental_reference_text,
    apply_wav_retention,
    exposed_wav_path,
    normalize_track,
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
    append_incremental_reference_text(record, "callee", "hello there thanks for calling")
    append_incremental_reference_text(record, "callee", "thanks for calling how can I help")

    assert record.incremental_reference_text["callee"] == "hello there thanks for calling how can I help"


def test_apply_wav_retention_removes_track_wavs_when_disabled(tmp_path):
    record = minimal_record(tmp_path)
    for wav_path in (record.wav_path, record.mic_wav_path, record.callee_wav_path):
        wav_path.write_bytes(b"RIFFfake")

    apply_wav_retention(record, keep_wav_files=False)

    assert record.wav_files_retained is False
    assert not record.wav_path.exists()
    assert not record.mic_wav_path.exists()
    assert not record.callee_wav_path.exists()
    assert exposed_wav_path(record, record.wav_path) is None


def test_apply_wav_retention_keeps_track_wavs_when_enabled(tmp_path):
    record = minimal_record(tmp_path)
    for wav_path in (record.wav_path, record.mic_wav_path, record.callee_wav_path):
        wav_path.write_bytes(b"RIFFfake")

    apply_wav_retention(record, keep_wav_files=True)

    assert record.wav_files_retained is True
    assert record.wav_path.exists()
    assert record.mic_wav_path.exists()
    assert record.callee_wav_path.exists()
    assert exposed_wav_path(record, record.wav_path) == str(record.wav_path)


def test_normalize_track_accepts_legacy_caller_alias():
    assert normalize_track("callee") == "callee"
    assert normalize_track("caller") == "callee"
    assert normalize_track("tab") == "callee"


def minimal_record(tmp_path: Path) -> SessionRecord:
    return SessionRecord(
        session_id="test",
        session_dir=tmp_path,
        pcm_path=tmp_path / "audio.pcm",
        wav_path=tmp_path / "audio.wav",
        mic_pcm_path=tmp_path / "you.pcm",
        mic_wav_path=tmp_path / "you.wav",
        callee_pcm_path=tmp_path / "callee.pcm",
        callee_wav_path=tmp_path / "callee.wav",
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
        incremental_reference_text={"mic": "", "callee": ""},
        incremental_reference_transcribe_seconds={"mic": 0.0, "callee": 0.0},
        incremental_reference_audio_seconds={"mic": 0.0, "callee": 0.0},
        incremental_reference_tasks_completed={"mic": 0, "callee": 0},
    )
