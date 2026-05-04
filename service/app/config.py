from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "ibm-granite/granite-speech-4.1-2b-plus"
DEFAULT_TITLE_MODEL = "unsloth/gemma-4-E4B-it-GGUF"
DEFAULT_TITLE_GGUF_FILENAME = "gemma-4-E4B-it-Q4_K_M.gguf"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
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
        default_dir = Path.home() / "Documents" / "Google Voice Transcripts"
        title_model_name = os.getenv("GV_TITLE_MODEL_NAME", DEFAULT_TITLE_MODEL)
        title_backend = os.getenv("GV_TITLE_BACKEND")
        if title_backend is None:
            title_backend = "llama_cpp" if "gguf" in title_model_name.lower() else "transformers"

        title_gguf_path = parse_optional_path(os.getenv("GV_TITLE_GGUF_PATH"))
        return cls(
            host=os.getenv("GV_SERVICE_HOST", "127.0.0.1"),
            port=int(os.getenv("GV_SERVICE_PORT", "8765")),
            recordings_dir=Path(os.getenv("GV_RECORDINGS_DIR", str(default_dir))).expanduser(),
            model_name=os.getenv("GV_MODEL_NAME", DEFAULT_MODEL),
            title_backend=title_backend.strip().lower(),
            title_model_name=title_model_name,
            title_gguf_filename=os.getenv("GV_TITLE_GGUF_FILENAME", DEFAULT_TITLE_GGUF_FILENAME),
            title_gguf_path=title_gguf_path,
            title_context_tokens=int(os.getenv("GV_TITLE_CONTEXT_TOKENS", "2048")),
            title_batch_tokens=int(os.getenv("GV_TITLE_BATCH_TOKENS", "512")),
            title_gpu_layers=int(os.getenv("GV_TITLE_GPU_LAYERS", "-1")),
            title_threads=int(os.getenv("GV_TITLE_THREADS", "0")),
            title_cache_type_k=os.getenv("GV_TITLE_CACHE_TYPE_K", "q8_0").strip().lower(),
            title_cache_type_v=os.getenv("GV_TITLE_CACHE_TYPE_V", "q8_0").strip().lower(),
            title_flash_attn=parse_bool(os.getenv("GV_TITLE_FLASH_ATTN", "1")),
            title_max_tokens=int(os.getenv("GV_TITLE_MAX_TOKENS", "12")),
            keep_title_model=parse_bool(os.getenv("GV_KEEP_TITLE_MODEL", "1")),
            hf_local_first=parse_bool(os.getenv("GV_HF_LOCAL_FIRST", "1")),
            hf_local_files_only=parse_bool(os.getenv("GV_HF_LOCAL_FILES_ONLY", "0")),
            compress_audio=parse_bool(os.getenv("GV_COMPRESS_AUDIO", "1")),
            compressed_audio_format=os.getenv("GV_COMPRESSED_AUDIO_FORMAT", "opus").strip().lower(),
            opus_bitrate=os.getenv("GV_OPUS_BITRATE", "24k").strip(),
            speaker_reference_mode=os.getenv("GV_SPEAKER_REFERENCE_MODE", "sampled").strip().lower(),
            speaker_reference_seconds=int(os.getenv("GV_SPEAKER_REFERENCE_SECONDS", "60")),
            speaker_reference_window_seconds=int(os.getenv("GV_SPEAKER_REFERENCE_WINDOW_SECONDS", "20")),
            speaker_reference_min_rms=float(os.getenv("GV_SPEAKER_REFERENCE_MIN_RMS", "0.003")),
            incremental_transcription=parse_bool(os.getenv("GV_INCREMENTAL_TRANSCRIPTION", "1")),
            incremental_reference_transcription=parse_bool(os.getenv("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", "1")),
            incremental_segment_seconds=int(os.getenv("GV_INCREMENTAL_SEGMENT_SECONDS", "60")),
            incremental_boundary_search_seconds=float(os.getenv("GV_INCREMENTAL_BOUNDARY_SEARCH_SECONDS", "3")),
            incremental_max_segment_seconds=float(os.getenv("GV_INCREMENTAL_MAX_SEGMENT_SECONDS", "75")),
            incremental_overlap_seconds=float(os.getenv("GV_INCREMENTAL_OVERLAP_SECONDS", "1.5")),
            incremental_silence_rms=float(os.getenv("GV_INCREMENTAL_SILENCE_RMS", "0.0025")),
            incremental_silence_window_ms=int(os.getenv("GV_INCREMENTAL_SILENCE_WINDOW_MS", "250")),
            warm_granite_on_call_start=parse_bool(os.getenv("GV_WARM_GRANITE_ON_CALL_START", "1")),
            transcribe=parse_bool(os.getenv("GV_TRANSCRIBE", "1")),
            segment_seconds=int(os.getenv("GV_SEGMENT_SECONDS", "240")),
            sample_rate=int(os.getenv("GV_SAMPLE_RATE", "16000")),
            max_new_tokens=int(os.getenv("GV_MAX_NEW_TOKENS", "2000")),
            reference_max_new_tokens=int(os.getenv("GV_REFERENCE_MAX_NEW_TOKENS", "128")),
            force_cpu=parse_bool(os.getenv("GV_FORCE_CPU", "0")),
            torch_dtype=os.getenv("GV_TORCH_DTYPE", "auto").lower(),
        )


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_optional_path(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    return Path(value).expanduser()
