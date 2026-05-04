from __future__ import annotations

from app.config import Settings


def test_reference_transcription_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("GV_CONFIG_FILE", str(tmp_path / "config.env"))
    monkeypatch.setenv("GV_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.delenv("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", raising=False)
    monkeypatch.delenv("GV_REFERENCE_MAX_NEW_TOKENS", raising=False)
    monkeypatch.delenv("GV_KEEP_WAV_FILES", raising=False)

    settings = Settings.from_env()

    assert settings.incremental_reference_transcription is True
    assert settings.reference_max_new_tokens == 128
    assert settings.keep_wav_files is False


def test_reference_transcription_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GV_CONFIG_FILE", str(tmp_path / "config.env"))
    monkeypatch.setenv("GV_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", "0")
    monkeypatch.setenv("GV_REFERENCE_MAX_NEW_TOKENS", "64")
    monkeypatch.setenv("GV_KEEP_WAV_FILES", "1")

    settings = Settings.from_env()

    assert settings.incremental_reference_transcription is False
    assert settings.reference_max_new_tokens == 64
    assert settings.keep_wav_files is True


def test_config_file_values_are_loaded_and_os_env_wins(monkeypatch, tmp_path):
    config_path = tmp_path / "config.env"
    config_path.write_text("GV_SERVICE_PORT=9999\nGV_KEEP_WAV_FILES=1\n", encoding="utf-8")
    monkeypatch.setenv("GV_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("GV_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("GV_SERVICE_PORT", "8765")

    settings = Settings.from_env()

    assert settings.config_file_path == config_path
    assert settings.port == 8765
    assert settings.keep_wav_files is True
