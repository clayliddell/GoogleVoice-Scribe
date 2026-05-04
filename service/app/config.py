from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "ibm-granite/granite-speech-4.1-2b-plus"
DEFAULT_TITLE_MODEL = "unsloth/gemma-4-E4B-it-GGUF"
DEFAULT_TITLE_GGUF_FILENAME = "gemma-4-E4B-it-Q4_K_M.gguf"
CONFIG_DIR_NAME = "GoogleVoiceScribe"
CONFIG_FILE_NAME = "config.env"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    config_file_path: Path
    recordings_dir: Path
    model_name: str
    title_backend: str
    title_model_name: str
    title_gguf_filename: str
    title_gguf_path: Path | None
    title_context_tokens: int
    title_batch_tokens: int
    title_gpu_layers: int
    title_threads: int
    title_cache_type_k: str
    title_cache_type_v: str
    title_flash_attn: bool
    title_max_tokens: int
    keep_title_model: bool
    hf_local_first: bool
    hf_local_files_only: bool
    compress_audio: bool
    keep_wav_files: bool
    compressed_audio_format: str
    opus_bitrate: str
    speaker_reference_mode: str
    speaker_reference_seconds: int
    speaker_reference_window_seconds: int
    speaker_reference_min_rms: float
    incremental_transcription: bool
    incremental_reference_transcription: bool
    incremental_segment_seconds: int
    incremental_boundary_search_seconds: float
    incremental_max_segment_seconds: float
    incremental_overlap_seconds: float
    incremental_silence_rms: float
    incremental_silence_window_ms: int
    warm_granite_on_call_start: bool
    transcribe: bool
    segment_seconds: int
    sample_rate: int
    max_new_tokens: int
    reference_max_new_tokens: int
    force_cpu: bool
    torch_dtype: str

    @classmethod
    def from_env(cls) -> "Settings":
        config_file_path = default_config_file_path()
        config_values = load_config_file(config_file_path)
        env = merged_env(config_values)
        default_dir = Path.home() / "Documents" / "Google Voice Transcripts"
        title_model_name = env_value(env, "GV_TITLE_MODEL_NAME", DEFAULT_TITLE_MODEL)
        title_backend = env.get("GV_TITLE_BACKEND")
        if title_backend is None:
            title_backend = "llama_cpp" if "gguf" in title_model_name.lower() else "transformers"

        title_gguf_path = parse_optional_path(env.get("GV_TITLE_GGUF_PATH"))
        return cls(
            host=env_value(env, "GV_SERVICE_HOST", "127.0.0.1"),
            port=int(env_value(env, "GV_SERVICE_PORT", "8765")),
            config_file_path=config_file_path,
            recordings_dir=Path(env_value(env, "GV_RECORDINGS_DIR", str(default_dir))).expanduser(),
            model_name=env_value(env, "GV_MODEL_NAME", DEFAULT_MODEL),
            title_backend=title_backend.strip().lower(),
            title_model_name=title_model_name,
            title_gguf_filename=env_value(env, "GV_TITLE_GGUF_FILENAME", DEFAULT_TITLE_GGUF_FILENAME),
            title_gguf_path=title_gguf_path,
            title_context_tokens=int(env_value(env, "GV_TITLE_CONTEXT_TOKENS", "2048")),
            title_batch_tokens=int(env_value(env, "GV_TITLE_BATCH_TOKENS", "512")),
            title_gpu_layers=int(env_value(env, "GV_TITLE_GPU_LAYERS", "-1")),
            title_threads=int(env_value(env, "GV_TITLE_THREADS", "0")),
            title_cache_type_k=env_value(env, "GV_TITLE_CACHE_TYPE_K", "q8_0").strip().lower(),
            title_cache_type_v=env_value(env, "GV_TITLE_CACHE_TYPE_V", "q8_0").strip().lower(),
            title_flash_attn=parse_bool(env_value(env, "GV_TITLE_FLASH_ATTN", "1")),
            title_max_tokens=int(env_value(env, "GV_TITLE_MAX_TOKENS", "12")),
            keep_title_model=parse_bool(env_value(env, "GV_KEEP_TITLE_MODEL", "1")),
            hf_local_first=parse_bool(env_value(env, "GV_HF_LOCAL_FIRST", "1")),
            hf_local_files_only=parse_bool(env_value(env, "GV_HF_LOCAL_FILES_ONLY", "0")),
            compress_audio=parse_bool(env_value(env, "GV_COMPRESS_AUDIO", "1")),
            keep_wav_files=parse_bool(env_value(env, "GV_KEEP_WAV_FILES", "0")),
            compressed_audio_format=env_value(env, "GV_COMPRESSED_AUDIO_FORMAT", "opus").strip().lower(),
            opus_bitrate=env_value(env, "GV_OPUS_BITRATE", "24k").strip(),
            speaker_reference_mode=env_value(env, "GV_SPEAKER_REFERENCE_MODE", "sampled").strip().lower(),
            speaker_reference_seconds=int(env_value(env, "GV_SPEAKER_REFERENCE_SECONDS", "60")),
            speaker_reference_window_seconds=int(env_value(env, "GV_SPEAKER_REFERENCE_WINDOW_SECONDS", "20")),
            speaker_reference_min_rms=float(env_value(env, "GV_SPEAKER_REFERENCE_MIN_RMS", "0.003")),
            incremental_transcription=parse_bool(env_value(env, "GV_INCREMENTAL_TRANSCRIPTION", "1")),
            incremental_reference_transcription=parse_bool(env_value(env, "GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", "1")),
            incremental_segment_seconds=int(env_value(env, "GV_INCREMENTAL_SEGMENT_SECONDS", "60")),
            incremental_boundary_search_seconds=float(env_value(env, "GV_INCREMENTAL_BOUNDARY_SEARCH_SECONDS", "3")),
            incremental_max_segment_seconds=float(env_value(env, "GV_INCREMENTAL_MAX_SEGMENT_SECONDS", "75")),
            incremental_overlap_seconds=float(env_value(env, "GV_INCREMENTAL_OVERLAP_SECONDS", "1.5")),
            incremental_silence_rms=float(env_value(env, "GV_INCREMENTAL_SILENCE_RMS", "0.0025")),
            incremental_silence_window_ms=int(env_value(env, "GV_INCREMENTAL_SILENCE_WINDOW_MS", "250")),
            warm_granite_on_call_start=parse_bool(env_value(env, "GV_WARM_GRANITE_ON_CALL_START", "1")),
            transcribe=parse_bool(env_value(env, "GV_TRANSCRIBE", "1")),
            segment_seconds=int(env_value(env, "GV_SEGMENT_SECONDS", "240")),
            sample_rate=int(env_value(env, "GV_SAMPLE_RATE", "16000")),
            max_new_tokens=int(env_value(env, "GV_MAX_NEW_TOKENS", "2000")),
            reference_max_new_tokens=int(env_value(env, "GV_REFERENCE_MAX_NEW_TOKENS", "128")),
            force_cpu=parse_bool(env_value(env, "GV_FORCE_CPU", "0")),
            torch_dtype=env_value(env, "GV_TORCH_DTYPE", "auto").lower(),
        )


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_optional_path(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    return Path(value).expanduser()


def default_config_file_path() -> Path:
    configured = os.getenv("GV_CONFIG_FILE")
    if configured and configured.strip():
        return Path(configured).expanduser()

    appdata = os.getenv("APPDATA")
    if appdata and appdata.strip():
        return Path(appdata) / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    return Path.home() / "AppData" / "Roaming" / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def load_config_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = unquote_config_value(value.strip())
    return values


def merged_env(config_values: dict[str, str]) -> dict[str, str]:
    merged = dict(config_values)
    merged.update(os.environ)
    return merged


def env_value(env: dict[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None or value == "":
        return default
    return value


def unquote_config_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
