from __future__ import annotations

from app.config import Settings


def test_reference_transcription_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("GV_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.delenv("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", raising=False)
    monkeypatch.delenv("GV_REFERENCE_MAX_NEW_TOKENS", raising=False)

    settings = Settings.from_env()

    assert settings.incremental_reference_transcription is True
    assert settings.reference_max_new_tokens == 128


def test_reference_transcription_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GV_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", "0")
    monkeypatch.setenv("GV_REFERENCE_MAX_NEW_TOKENS", "64")

    settings = Settings.from_env()

    assert settings.incremental_reference_transcription is False
    assert settings.reference_max_new_tokens == 64
