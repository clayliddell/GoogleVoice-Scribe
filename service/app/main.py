from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import Settings
from .sessions import SessionManager, exposed_wav_path
from .version import APP_NAME, __version__


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("google_voice_transcriber")

settings = Settings.from_env()
manager = SessionManager(settings)

app = FastAPI(title=APP_NAME, version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartSessionRequest(BaseModel):
    source: str = "google_voice"
    tab_id: int | None = None
    tab_url: str = ""
    page_title: str = ""
    started_at: str | None = None
    trigger_label: str = ""
    callee_label: str = ""
    transcript_mode: str = "speaker_attributed_asr"
    audio_mode: str = "mixed_tab_and_microphone_pcm"
    mic_required: bool = True
    mic_device_id: str = ""


class FinishSessionRequest(BaseModel):
    reason: str = "finished"
    chunks: int = 0
    dropped_chunks: int = 0
    upload_errors: list[str] = Field(default_factory=list)
    track_chunks: dict[str, int] = Field(default_factory=dict)
    track_dropped_chunks: dict[str, int] = Field(default_factory=dict)
    track_upload_errors: dict[str, list[str]] = Field(default_factory=dict)
    sample_rate: int = 48000
    channels: int = 1
    mic_captured: bool = False
    mic_error: str | None = None
    mic_track_label: str = ""
    mic_peak: float = 0.0
    tab_peak: float = 0.0
    mixed_peak: float = 0.0
    ended_at: str | None = None


class AbortSessionRequest(BaseModel):
    reason: str = "aborted"


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "config_file_path": str(settings.config_file_path),
        "recordings_dir": str(settings.recordings_dir),
        "model_name": settings.model_name,
        "title_backend": settings.title_backend,
        "title_model_name": settings.title_model_name,
        "title_gguf_filename": settings.title_gguf_filename,
        "title_context_tokens": settings.title_context_tokens,
        "title_gpu_layers": settings.title_gpu_layers,
        "title_cache_type_k": settings.title_cache_type_k,
        "title_cache_type_v": settings.title_cache_type_v,
        "title_flash_attn": settings.title_flash_attn,
        "keep_title_model": settings.keep_title_model,
        "hf_local_first": settings.hf_local_first,
        "hf_local_files_only": settings.hf_local_files_only,
        "compress_audio": settings.compress_audio,
        "keep_wav_files": settings.keep_wav_files,
        "compressed_audio_format": settings.compressed_audio_format,
        "opus_bitrate": settings.opus_bitrate,
        "speaker_reference_mode": settings.speaker_reference_mode,
        "speaker_reference_seconds": settings.speaker_reference_seconds,
        "speaker_reference_window_seconds": settings.speaker_reference_window_seconds,
        "speaker_reference_min_rms": settings.speaker_reference_min_rms,
        "incremental_transcription": settings.incremental_transcription,
        "incremental_reference_transcription": settings.incremental_reference_transcription,
        "incremental_segment_seconds": settings.incremental_segment_seconds,
        "incremental_boundary_search_seconds": settings.incremental_boundary_search_seconds,
        "incremental_max_segment_seconds": settings.incremental_max_segment_seconds,
        "incremental_overlap_seconds": settings.incremental_overlap_seconds,
        "incremental_silence_rms": settings.incremental_silence_rms,
        "incremental_silence_window_ms": settings.incremental_silence_window_ms,
        "warm_granite_on_call_start": settings.warm_granite_on_call_start,
        "speech_model_warmup": manager.speech_warmup_state(),
        "transcribe": settings.transcribe,
        "segment_seconds": settings.segment_seconds,
        "max_new_tokens": settings.max_new_tokens,
        "reference_max_new_tokens": settings.reference_max_new_tokens,
    }


@app.post("/sessions/start")
def start_session(payload: StartSessionRequest) -> dict[str, Any]:
    record = manager.start(payload.model_dump())
    logger.info("Started session %s in %s", record.session_id, record.session_dir)
    return {
        "ok": True,
        "session_id": record.session_id,
        "started_at": record.started_at,
        "session_dir": str(record.session_dir),
    }


@app.post("/sessions/{session_id}/chunk")
async def append_chunk(
    session_id: str,
    request: Request,
    track: str = Query("mixed"),
    sequence: int = Query(..., ge=0),
    sample_rate: int = Query(..., gt=0),
    channels: int = Query(1, ge=1, le=2),
) -> dict[str, Any]:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty chunk.")

    try:
        record = manager.append_chunk(
            session_id,
            track=track,
            sequence=sequence,
            sample_rate=sample_rate,
            channels=channels,
            data=data,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return {
        "ok": True,
        "session_id": record.session_id,
        "chunks_received": record.chunks_received,
        "track_chunks_received": record.track_chunks_received,
    }


@app.post("/sessions/{session_id}/finish")
def finish_session(
    session_id: str,
    payload: FinishSessionRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        record = manager.finish(session_id, payload.model_dump())
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if settings.transcribe:
        background_tasks.add_task(run_transcription, session_id)
        transcript_status = "queued"
    else:
        transcript_status = "disabled"

    logger.info("Finished recording session %s; transcript status=%s", session_id, transcript_status)
    return {
        "ok": True,
        "session_id": record.session_id,
        "status": record.status,
        "transcript_status": transcript_status,
        "duration_seconds": record.duration_seconds,
        "audio_path": exposed_wav_path(record, record.wav_path),
        "compressed_audio_path": str(record.compressed_audio_path),
        "compressed_audio_error": record.compressed_audio_error,
        "you_audio_path": exposed_wav_path(record, record.mic_wav_path),
        "callee_audio_path": exposed_wav_path(record, record.callee_wav_path),
        "transcript_path": str(record.transcript_path),
        "conversation_path": str(record.conversation_path),
        "session_dir": str(record.session_dir),
    }


@app.post("/sessions/{session_id}/abort")
def abort_session(session_id: str, payload: AbortSessionRequest) -> dict[str, Any]:
    try:
        record = manager.abort(session_id, payload.reason)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    logger.warning("Aborted session %s: %s", session_id, payload.reason)
    return manager.as_response(record)


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return manager.as_response(manager.get(session_id))
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def run_transcription(session_id: str) -> None:
    logger.info("Starting transcription for %s", session_id)
    manager.transcribe(session_id)
    logger.info("Finished transcription for %s", session_id)
