from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Any

import numpy as np

from .audio import (
    compressed_audio_path,
    load_wav_mono_float32,
    pcm16_bytes_to_mono_float32,
    write_opus_from_wav,
    write_wav_from_pcm16,
)
from .config import Settings
from .conversation import (
    TRACK_FILENAMES,
    TRACKS,
    build_conversation,
    clean_subject,
    fallback_subject,
    parse_speaker_turns as parse_conversation_speaker_turns,
    slugify,
)
from .transcriber import GraniteTranscriber, TranscriptionResult


MAX_FOLDER_NAME_LENGTH = 255
MAX_WINDOWS_DIRECTORY_PATH_LENGTH = 220


@dataclass
class SessionRecord:
    session_id: str
    session_dir: Path
    pcm_path: Path
    wav_path: Path
    mic_pcm_path: Path
    mic_wav_path: Path
    caller_pcm_path: Path
    caller_wav_path: Path
    compressed_audio_path: Path
    transcript_path: Path
    conversation_path: Path
    metadata_path: Path
    started_at: str
    source: str
    tab_id: int | None
    tab_url: str
    page_title: str
    trigger_label: str
    callee_label: str
    transcript_mode: str
    audio_mode: str
    mic_required: bool = True
    mic_device_id: str = ""
    mic_captured: bool = False
    mic_error: str | None = None
    mic_track_label: str = ""
    mic_peak: float = 0.0
    tab_peak: float = 0.0
    mixed_peak: float = 0.0
    sample_rate: int | None = None
    channels: int | None = None
    chunks_received: int = 0
    expected_sequence: int = 0
    sequence_gaps: list[dict[str, int]] = field(default_factory=list)
    track_chunks_received: dict[str, int] = field(default_factory=dict)
    track_expected_sequence: dict[str, int] = field(default_factory=dict)
    track_sequence_gaps: dict[str, list[dict[str, int]]] = field(default_factory=dict)
    track_durations_seconds: dict[str, float] = field(default_factory=dict)
    track_upload_errors: dict[str, list[str]] = field(default_factory=dict)
    compressed_audio_error: str | None = None
    wav_files_retained: bool = True
    ended_at: str | None = None
    duration_seconds: float | None = None
    status: str = "recording"
    finish_reason: str | None = None
    upload_errors: list[str] = field(default_factory=list)
    callee: str = ""
    subject: str = ""
    speaker_map: dict[str, str] = field(default_factory=dict)
    final_session_dir: Path | None = None
    incremental_next_start_sample: int = 0
    incremental_completed_until_sample: int = 0
    incremental_tasks_submitted: int = 0
    incremental_tasks_completed: int = 0
    incremental_transcribe_seconds: float = 0.0
    incremental_mixed_transcribe_seconds: float = 0.0
    incremental_audio_seconds: float = 0.0
    incremental_transcript_error: str | None = None
    incremental_segments: list[dict[str, Any]] = field(default_factory=list)
    incremental_boundary_decisions: list[dict[str, Any]] = field(default_factory=list)
    incremental_reference_text: dict[str, str] = field(default_factory=dict)
    incremental_reference_transcribe_seconds: dict[str, float] = field(default_factory=dict)
    incremental_reference_audio_seconds: dict[str, float] = field(default_factory=dict)
    incremental_reference_tasks_completed: dict[str, int] = field(default_factory=dict)
    incremental_reference_errors: dict[str, str] = field(default_factory=dict)
    speech_warmup_requested: bool = False
    speech_warmup_status: str = "not_requested"
    speech_warmup_seconds: float | None = None
    speech_warmup_error: str | None = None


@dataclass
class IncrementalTranscriptionTask:
    session_id: str
    audio_start_sample: int
    commit_start_sample: int
    commit_end_sample: int
    sample_rate: int
    boundary_reason: str
    boundary_rms: float | None
    overlap_samples: int
    audio: np.ndarray
    track_audio: dict[str, np.ndarray] = field(default_factory=dict)


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.recordings_dir / "_tmp").mkdir(parents=True, exist_ok=True)
        self._records: dict[str, SessionRecord] = {}
        self._lock = Lock()
        self._transcriber_lock = Lock()
        self._warmup_lock = Lock()
        self._transcriber: GraniteTranscriber | None = None
        self._speech_warmup_status = "idle"
        self._speech_warmup_last_seconds: float | None = None
        self._speech_warmup_last_error: str | None = None
        self._speech_warmup_thread: Thread | None = None
        self._incremental_queue: Queue[IncrementalTranscriptionTask] = Queue()
        self._incremental_worker = Thread(
            target=self._run_incremental_worker,
            name="google-voice-incremental-transcriber",
            daemon=True,
        )
        self._incremental_worker.start()

    def start(self, payload: dict[str, Any]) -> SessionRecord:
        session_id = uuid.uuid4().hex
        started_at = payload.get("started_at") or datetime.now(timezone.utc).isoformat()
        session_dir = unique_path(self.settings.recordings_dir / "_tmp" / session_id)
        session_dir.mkdir(parents=True, exist_ok=False)

        record = SessionRecord(
            session_id=session_id,
            session_dir=session_dir,
            pcm_path=session_dir / "audio.pcm",
            wav_path=session_dir / "audio.wav",
            mic_pcm_path=session_dir / "you.pcm",
            mic_wav_path=session_dir / "you.wav",
            caller_pcm_path=session_dir / "caller.pcm",
            caller_wav_path=session_dir / "caller.wav",
            compressed_audio_path=session_dir / "audio.opus",
            transcript_path=session_dir / "transcript.json",
            conversation_path=session_dir / "conversation.txt",
            metadata_path=session_dir / "session.json",
            started_at=started_at,
            source=str(payload.get("source") or "google_voice"),
            tab_id=payload.get("tab_id"),
            tab_url=str(payload.get("tab_url") or ""),
            page_title=str(payload.get("page_title") or ""),
            trigger_label=str(payload.get("trigger_label") or ""),
            callee_label=str(payload.get("callee_label") or ""),
            transcript_mode=str(payload.get("transcript_mode") or "speaker_attributed_asr"),
            audio_mode=str(payload.get("audio_mode") or "mixed_tab_and_microphone_pcm"),
            mic_required=bool(payload.get("mic_required", True)),
            mic_device_id=str(payload.get("mic_device_id") or ""),
            track_chunks_received={track: 0 for track in TRACKS},
            track_expected_sequence={track: 0 for track in TRACKS},
            track_sequence_gaps={track: [] for track in TRACKS},
            track_upload_errors={track: [] for track in TRACKS},
            wav_files_retained=self.settings.keep_wav_files,
            incremental_reference_text={"mic": "", "caller": ""},
            incremental_reference_transcribe_seconds={"mic": 0.0, "caller": 0.0},
            incremental_reference_audio_seconds={"mic": 0.0, "caller": 0.0},
            incremental_reference_tasks_completed={"mic": 0, "caller": 0},
            incremental_reference_errors={},
        )

        for track in TRACKS:
            track_pcm_path(record, track).touch()

        self._write_metadata(record)
        write_json(record.transcript_path, self._base_transcript(record, status="recording"))

        with self._lock:
            self._records[session_id] = record

        self._request_speech_warmup(record)
        return record

    def append_chunk(
        self,
        session_id: str,
        *,
        track: str,
        sequence: int,
        sample_rate: int,
        channels: int,
        data: bytes,
    ) -> SessionRecord:
        record = self.get(session_id)
        track = normalize_track(track)
        if record.status != "recording":
            raise ValueError(f"Session {session_id} is not recording.")

        expected = record.track_expected_sequence.get(track, 0)
        if sequence != expected:
            gap = {"expected": expected, "received": sequence}
            record.track_sequence_gaps.setdefault(track, []).append(gap)
            if track == "mixed":
                record.sequence_gaps.append(gap)
            record.track_expected_sequence[track] = sequence

        with track_pcm_path(record, track).open("ab") as handle:
            handle.write(data)

        record.track_expected_sequence[track] = record.track_expected_sequence.get(track, sequence) + 1
        record.track_chunks_received[track] = record.track_chunks_received.get(track, 0) + 1
        record.chunks_received = sum(record.track_chunks_received.values())
        record.sample_rate = sample_rate
        record.channels = channels
        record.expected_sequence = record.track_expected_sequence.get("mixed", 0)

        if record.chunks_received % 75 == 0:
            self._write_metadata(record)
        if (
            self.settings.transcribe
            and self.settings.incremental_transcription
            and (track == "mixed" or self.settings.incremental_reference_transcription)
        ):
            self._queue_incremental_transcription(record)
        return record

    def finish(self, session_id: str, payload: dict[str, Any]) -> SessionRecord:
        record = self.get(session_id)
        if record.status in {"finished", "transcribing", "transcribed"}:
            return record

        record.status = "finished"
        record.ended_at = payload.get("ended_at") or datetime.now(timezone.utc).isoformat()
        record.finish_reason = str(payload.get("reason") or "finished")
        record.sample_rate = int(payload.get("sample_rate") or record.sample_rate or 48000)
        record.channels = int(payload.get("channels") or record.channels or 1)
        record.mic_captured = bool(payload.get("mic_captured"))
        record.mic_error = payload.get("mic_error")
        record.mic_track_label = str(payload.get("mic_track_label") or "")
        record.mic_peak = float(payload.get("mic_peak") or 0.0)
        record.tab_peak = float(payload.get("tab_peak") or 0.0)
        record.mixed_peak = float(payload.get("mixed_peak") or 0.0)
        record.upload_errors = list(payload.get("upload_errors") or [])
        record.track_upload_errors = normalize_track_errors(payload.get("track_upload_errors"), record.upload_errors)

        for track in TRACKS:
            pcm_path = track_pcm_path(record, track)
            wav_path = track_wav_path(record, track)
            if pcm_path.exists():
                record.track_durations_seconds[track] = write_wav_from_pcm16(
                    pcm_path,
                    wav_path,
                    sample_rate=record.sample_rate,
                    channels=record.channels,
                )
                pcm_path.unlink(missing_ok=True)

        if self.settings.compress_audio:
            record.compressed_audio_error = compress_recording_audio(
                record.wav_path,
                record.compressed_audio_path,
                output_format=self.settings.compressed_audio_format,
                bitrate=self.settings.opus_bitrate,
            )

        record.duration_seconds = record.track_durations_seconds.get("mixed") or max(
            record.track_durations_seconds.values() or [0.0]
        )

        if not self.settings.transcribe:
            record.callee = "Callee"
            record.subject = "not transcribed"
            apply_wav_retention(record, keep_wav_files=self.settings.keep_wav_files)
            self._finalize_session_dir(record)

        self._write_metadata(record)
        write_json(record.transcript_path, self._base_transcript(record, status="queued" if self.settings.transcribe else "disabled"))
        return record

    def abort(self, session_id: str, reason: str) -> SessionRecord:
        record = self.get(session_id)
        record.status = "aborted"
        record.finish_reason = reason
        record.ended_at = datetime.now(timezone.utc).isoformat()
        record.subject = "aborted"
        record.callee = "Callee"
        self._finalize_session_dir(record)
        self._write_metadata(record)
        write_json(
            record.transcript_path,
            {
                **self._base_transcript(record, status="aborted"),
                "error": reason,
            },
        )
        return record

    def transcribe(self, session_id: str) -> None:
        record = self.get(session_id)
        if not self.settings.transcribe:
            return

        total_started = time.perf_counter()
        timings: dict[str, Any] = {}
        record.status = "transcribing"
        self._write_metadata(record)
        write_json(record.transcript_path, self._base_transcript(record, status="transcribing"))

        try:
            started = time.perf_counter()
            result = self._transcribe_mixed_audio(record)
            timings["mixed_transcribe_seconds"] = elapsed_seconds(started)
            timings["mixed_transcription_source"] = "incremental" if record.incremental_completed_until_sample else "final"
            timings["incremental_transcribe_seconds"] = round(record.incremental_transcribe_seconds, 3)
            timings["incremental_mixed_transcribe_seconds"] = round(record.incremental_mixed_transcribe_seconds, 3)
            timings["incremental_audio_seconds"] = round(record.incremental_audio_seconds, 3)
            timings["incremental_boundary_decision_count"] = len(record.incremental_boundary_decisions)
            timings["incremental_boundary_reasons"] = boundary_reason_counts(record.incremental_boundary_decisions)
            timings["incremental_reference_transcription"] = self.settings.incremental_reference_transcription
            timings["incremental_reference_transcribe_seconds"] = {
                track: round(seconds, 3)
                for track, seconds in record.incremental_reference_transcribe_seconds.items()
            }
            timings["incremental_reference_audio_seconds"] = {
                track: round(seconds, 3)
                for track, seconds in record.incremental_reference_audio_seconds.items()
            }
            timings["incremental_reference_tasks_completed"] = dict(record.incremental_reference_tasks_completed)
            started = time.perf_counter()
            you_reference, you_reference_audio_seconds, you_reference_source = self._speaker_reference_text(
                record,
                track="mic",
                wav_path=record.mic_wav_path,
            )
            timings["you_reference_transcribe_seconds"] = elapsed_seconds(started)
            timings["you_reference_audio_seconds"] = round(you_reference_audio_seconds, 3)
            timings["you_reference_source"] = you_reference_source
            started = time.perf_counter()
            caller_reference, caller_reference_audio_seconds, caller_reference_source = self._speaker_reference_text(
                record,
                track="caller",
                wav_path=record.caller_wav_path,
            )
            timings["caller_reference_transcribe_seconds"] = elapsed_seconds(started)
            timings["caller_reference_audio_seconds"] = round(caller_reference_audio_seconds, 3)
            timings["caller_reference_source"] = caller_reference_source
            timings["speaker_reference_mode"] = self.settings.speaker_reference_mode
            record.callee = "Callee"
            started = time.perf_counter()
            with self._transcriber_lock:
                subject = clean_subject(self._get_transcriber().summarize_subject(result.full_text))
                subject_model_error = self._get_transcriber().last_subject_error
            timings["title_generation_seconds"] = elapsed_seconds(started)
            record.subject = subject or fallback_subject(result.full_text, default="conversation")

            started = time.perf_counter()
            conversation_text, speaker_map = build_conversation(
                result.segments,
                callee_name=record.callee,
                you_reference_text=you_reference,
                caller_reference_text=caller_reference,
            )
            timings["conversation_build_seconds"] = elapsed_seconds(started)
            timings["transcription_total_seconds"] = elapsed_seconds(total_started)
            record.speaker_map = speaker_map
            record.status = "transcribed"
            apply_wav_retention(record, keep_wav_files=self.settings.keep_wav_files)
            self._finalize_session_dir(record)
            write_text(record.conversation_path, conversation_text)
            resolved_segments = resolve_segment_speakers(result.segments, record.speaker_map)
            self._write_metadata(record)
            write_json(
                record.transcript_path,
                {
                    **self._base_transcript(record, status="transcribed"),
                    "model": result.model,
                    "mode": result.mode,
                    "sample_rate": result.sample_rate,
                    "full_text": conversation_text.strip(),
                    "segments": resolved_segments,
                    "speaker_reference_text": {
                        "you": you_reference,
                        "callee": caller_reference,
                    },
                    "timings": timings,
                    **timings,
                    "warnings": {
                        "speaker_identity": "You/callee labels are resolved from separate microphone and caller tracks when available.",
                        "subject_backend": self.settings.title_backend,
                        "subject_model": self.settings.title_model_name,
                        "subject_model_file": self.settings.title_gguf_filename if self.settings.title_backend == "llama_cpp" else None,
                        "subject_model_error": subject_model_error,
                    },
                },
            )
        except Exception as error:
            record.status = "transcription_failed"
            record.callee = "Callee"
            record.subject = "transcription failed"
            timings["transcription_total_seconds"] = elapsed_seconds(total_started)
            apply_wav_retention(record, keep_wav_files=self.settings.keep_wav_files)
            self._finalize_session_dir(record)
            self._write_metadata(record)
            write_json(
                record.transcript_path,
                {
                    **self._base_transcript(record, status="transcription_failed"),
                    "model": self.settings.model_name,
                    "mode": record.transcript_mode,
                    "full_text": "",
                    "segments": [],
                    "timings": timings,
                    **timings,
                    "error": str(error),
                },
            )

    def get(self, session_id: str) -> SessionRecord:
        with self._lock:
            record = self._records.get(session_id)

        if not record:
            raise KeyError(f"Unknown session: {session_id}")
        return record

    def as_response(self, record: SessionRecord) -> dict[str, Any]:
        payload = record_to_json(record)
        payload["session_dir"] = str(record.session_dir)
        payload["audio_path"] = exposed_wav_path(record, record.wav_path)
        payload["compressed_audio_path"] = str(record.compressed_audio_path)
        payload["compressed_audio_error"] = record.compressed_audio_error
        payload["you_audio_path"] = exposed_wav_path(record, record.mic_wav_path)
        payload["caller_audio_path"] = exposed_wav_path(record, record.caller_wav_path)
        payload["transcript_path"] = str(record.transcript_path)
        payload["conversation_path"] = str(record.conversation_path)
        payload["speech_model_warmup"] = {
            **self.speech_warmup_state(),
            "requested_for_session": record.speech_warmup_requested,
            "session_status": record.speech_warmup_status,
            "session_seconds": record.speech_warmup_seconds,
            "session_error": record.speech_warmup_error,
        }
        return payload

    def _get_transcriber(self) -> GraniteTranscriber:
        if self._transcriber is None:
            self._transcriber = GraniteTranscriber(self.settings)
        return self._transcriber

    def speech_warmup_state(self) -> dict[str, Any]:
        loaded = self._speech_model_loaded()
        with self._warmup_lock:
            status = self._speech_warmup_status
            if loaded:
                status = "loaded"
            elif status == "loaded":
                status = "idle"
            return {
                "enabled": self.settings.warm_granite_on_call_start,
                "status": status,
                "model_loaded": loaded,
                "last_seconds": self._speech_warmup_last_seconds,
                "last_error": None if loaded else self._speech_warmup_last_error,
            }

    def _speech_model_loaded(self) -> bool:
        return self._transcriber is not None and self._transcriber.is_speech_model_loaded()

    def _request_speech_warmup(self, record: SessionRecord) -> None:
        if not self.settings.transcribe or not self.settings.warm_granite_on_call_start:
            record.speech_warmup_requested = False
            record.speech_warmup_status = "disabled"
            self._write_metadata(record)
            return

        record.speech_warmup_requested = True
        thread_to_start: Thread | None = None

        with self._warmup_lock:
            if self._speech_model_loaded():
                self._speech_warmup_status = "loaded"
                self._speech_warmup_last_error = None
                record.speech_warmup_status = "loaded"
                record.speech_warmup_seconds = self._speech_warmup_last_seconds
                record.speech_warmup_error = None
            elif (
                self._speech_warmup_status == "loading"
                and self._speech_warmup_thread is not None
                and self._speech_warmup_thread.is_alive()
            ):
                record.speech_warmup_status = "loading"
                record.speech_warmup_error = None
            else:
                self._speech_warmup_status = "loading"
                self._speech_warmup_last_seconds = None
                self._speech_warmup_last_error = None
                record.speech_warmup_status = "loading"
                record.speech_warmup_seconds = None
                record.speech_warmup_error = None
                thread_to_start = Thread(
                    target=self._run_speech_warmup,
                    name="google-voice-granite-warmup",
                    daemon=True,
                )
                self._speech_warmup_thread = thread_to_start

        self._write_metadata(record)
        write_json(record.transcript_path, self._base_transcript(record, status=record.status))
        if thread_to_start is not None:
            thread_to_start.start()

    def _run_speech_warmup(self) -> None:
        started = time.perf_counter()
        status = "loaded"
        error_text: str | None = None

        try:
            with self._transcriber_lock:
                self._get_transcriber().warm_speech_model()
        except Exception as error:
            status = "failed"
            error_text = str(error)

        seconds = elapsed_seconds(started)
        with self._warmup_lock:
            self._speech_warmup_status = status
            self._speech_warmup_last_seconds = seconds
            self._speech_warmup_last_error = error_text

        self._apply_speech_warmup_result(status=status, seconds=seconds, error_text=error_text)

    def _apply_speech_warmup_result(self, *, status: str, seconds: float, error_text: str | None) -> None:
        with self._lock:
            records = list(self._records.values())

        for record in records:
            if not record.speech_warmup_requested:
                continue
            record.speech_warmup_status = status
            record.speech_warmup_seconds = seconds
            record.speech_warmup_error = error_text
            self._write_metadata(record)

    def _speaker_reference_text(self, record: SessionRecord, *, track: str, wav_path: Path) -> tuple[str, float, str]:
        tasks_completed = record.incremental_reference_tasks_completed.get(track, 0)
        if (
            self.settings.incremental_reference_transcription
            and self.settings.incremental_transcription
            and tasks_completed > 0
        ):
            return (
                record.incremental_reference_text.get(track, ""),
                record.incremental_reference_audio_seconds.get(track, 0.0),
                "incremental",
            )

        text, audio_seconds = transcribe_reference_track(
            self._get_transcriber(),
            wav_path,
            settings=self.settings,
            transcriber_lock=self._transcriber_lock,
        )
        return text, audio_seconds, "post_call"

    def _queue_incremental_transcription(self, record: SessionRecord) -> None:
        if not record.sample_rate or not record.channels:
            return

        segment_seconds = max(0, self.settings.incremental_segment_seconds)
        if segment_seconds <= 0:
            return

        frame_size = 2 * record.channels
        if frame_size <= 0 or not record.pcm_path.exists():
            return

        queued_tracks = TRACKS if self.settings.incremental_reference_transcription else ("mixed",)
        track_paths = {track: track_pcm_path(record, track) for track in queued_tracks}
        if any(not path.exists() for path in track_paths.values()):
            return

        track_available_samples = {
            track: path.stat().st_size // frame_size
            for track, path in track_paths.items()
        }
        available_samples = min(track_available_samples.values())
        while True:
            decision = choose_incremental_boundary(
                record.pcm_path,
                record.incremental_next_start_sample,
                available_samples,
                channels=record.channels,
                sample_rate=record.sample_rate,
                settings=self.settings,
            )
            if decision is None:
                return

            commit_start_sample = record.incremental_next_start_sample
            commit_end_sample = int(decision["commit_end_sample"])
            overlap_samples = min(
                commit_start_sample,
                max(0, int(round(self.settings.incremental_overlap_seconds * record.sample_rate))),
            )
            audio_start_sample = commit_start_sample - overlap_samples
            track_audio = {}
            for queued_track, path in track_paths.items():
                data = read_pcm16_range(path, audio_start_sample, commit_end_sample, channels=record.channels)
                track_audio[queued_track] = pcm16_bytes_to_mono_float32(data, channels=record.channels)

            audio = track_audio.get("mixed", np.array([], dtype=np.float32))
            if audio.size == 0:
                return

            task_index = record.incremental_tasks_submitted
            boundary_decision = incremental_boundary_decision_payload(
                task_index=task_index,
                audio_start_sample=audio_start_sample,
                commit_start_sample=commit_start_sample,
                commit_end_sample=commit_end_sample,
                sample_rate=record.sample_rate,
                boundary_reason=str(decision["boundary_reason"]),
                boundary_rms=decision.get("boundary_rms"),
                overlap_samples=overlap_samples,
            )
            record.incremental_boundary_decisions.append(boundary_decision)
            record.incremental_next_start_sample = commit_end_sample
            record.incremental_tasks_submitted += 1
            self._incremental_queue.put(
                IncrementalTranscriptionTask(
                    session_id=record.session_id,
                    audio_start_sample=audio_start_sample,
                    commit_start_sample=commit_start_sample,
                    commit_end_sample=commit_end_sample,
                    sample_rate=record.sample_rate,
                    boundary_reason=str(decision["boundary_reason"]),
                    boundary_rms=decision.get("boundary_rms"),
                    overlap_samples=overlap_samples,
                    audio=audio,
                    track_audio=track_audio,
                )
            )

    def _run_incremental_worker(self) -> None:
        while True:
            task = self._incremental_queue.get()
            try:
                self._process_incremental_task(task)
            finally:
                self._incremental_queue.task_done()

    def _process_incremental_task(self, task: IncrementalTranscriptionTask) -> None:
        try:
            record = self.get(task.session_id)
        except KeyError:
            return

        started = time.perf_counter()
        try:
            prefix_text = segments_full_text(record.incremental_segments)
            reference_texts: dict[str, str] = {}
            reference_seconds: dict[str, float] = {}
            reference_audio_seconds: dict[str, float] = {}
            reference_errors: dict[str, str] = {}
            with self._transcriber_lock:
                transcriber = self._get_transcriber()
                mixed_started = time.perf_counter()
                result = transcriber.transcribe_audio(
                    task.audio,
                    task.sample_rate,
                    mode="speaker_attributed_asr",
                    start_offset_seconds=task.audio_start_sample / float(task.sample_rate),
                    initial_prefix_text=prefix_text,
                )
                mixed_elapsed = elapsed_seconds(mixed_started)

                for reference_track in ("mic", "caller"):
                    if not self.settings.incremental_reference_transcription:
                        break
                    reference_audio = task.track_audio.get(reference_track)
                    if reference_audio is None or reference_audio.size == 0:
                        continue

                    reference_started = time.perf_counter()
                    try:
                        reference_result = transcriber.transcribe_audio(
                            reference_audio,
                            task.sample_rate,
                            mode="plain_asr",
                            start_offset_seconds=task.audio_start_sample / float(task.sample_rate),
                            max_new_tokens=self.settings.reference_max_new_tokens,
                        )
                        reference_texts[reference_track] = reference_result.full_text
                        reference_seconds[reference_track] = elapsed_seconds(reference_started)
                        reference_audio_seconds[reference_track] = round(
                            reference_audio.size / float(task.sample_rate),
                            3,
                        )
                    except Exception as error:
                        reference_errors[reference_track] = str(error)

            new_segments = prepare_incremental_segments(
                result.segments,
                record.incremental_segments,
                commit_start_sample=task.commit_start_sample,
                commit_end_sample=task.commit_end_sample,
                sample_rate=task.sample_rate,
                boundary_reason=task.boundary_reason,
                boundary_rms=task.boundary_rms,
                overlap_samples=task.overlap_samples,
            )
            record.incremental_segments = reindex_segments(record.incremental_segments + new_segments)
            record.incremental_completed_until_sample = max(record.incremental_completed_until_sample, task.commit_end_sample)
            record.incremental_tasks_completed += 1
            record.incremental_transcribe_seconds = round(
                record.incremental_transcribe_seconds + elapsed_seconds(started),
                3,
            )
            record.incremental_mixed_transcribe_seconds = round(
                record.incremental_mixed_transcribe_seconds + mixed_elapsed,
                3,
            )
            record.incremental_audio_seconds = round(
                record.incremental_audio_seconds + (task.commit_end_sample - task.audio_start_sample) / float(task.sample_rate),
                3,
            )
            apply_incremental_reference_results(
                record,
                reference_texts=reference_texts,
                reference_seconds=reference_seconds,
                reference_audio_seconds=reference_audio_seconds,
                reference_errors=reference_errors,
            )
            self._write_incremental_draft(record)
        except Exception as error:
            record.incremental_transcript_error = str(error)
            record.incremental_tasks_completed += 1
            record.incremental_transcribe_seconds = round(
                record.incremental_transcribe_seconds + elapsed_seconds(started),
                3,
            )
            self._write_metadata(record)

    def _write_incremental_draft(self, record: SessionRecord) -> None:
        if record.status in {"transcribed", "transcription_failed", "aborted"}:
            return

        status = "queued" if record.status == "finished" and self.settings.transcribe else record.status
        self._write_metadata(record)
        write_json(
            record.transcript_path,
            {
                **self._base_transcript(record, status=status),
                "draft_full_text": segments_full_text(record.incremental_segments),
                "draft_segments": record.incremental_segments,
            },
        )

    def _wait_for_incremental_tasks(self, record: SessionRecord) -> None:
        while record.incremental_tasks_completed < record.incremental_tasks_submitted:
            time.sleep(0.25)

    def _transcribe_mixed_audio(self, record: SessionRecord) -> TranscriptionResult:
        if not self.settings.incremental_transcription:
            with self._transcriber_lock:
                return self._get_transcriber().transcribe_wav(record.wav_path)

        self._wait_for_incremental_tasks(record)
        if record.incremental_transcript_error:
            with self._transcriber_lock:
                return self._get_transcriber().transcribe_wav(record.wav_path)

        completed_until_sample = max(0, record.incremental_completed_until_sample)
        if completed_until_sample <= 0:
            with self._transcriber_lock:
                return self._get_transcriber().transcribe_wav(record.wav_path)

        audio, source_rate = load_wav_mono_float32(record.wav_path)
        completed_until_sample = min(completed_until_sample, audio.size)
        segments = reindex_segments(record.incremental_segments)
        prefix_text = segments_full_text(segments)

        if completed_until_sample < audio.size:
            tail_overlap_samples = min(
                completed_until_sample,
                max(0, int(round(self.settings.incremental_overlap_seconds * source_rate))),
            )
            tail_audio_start_sample = completed_until_sample - tail_overlap_samples
            tail_reference_audio = (
                load_reference_tail_audio(
                    record,
                    start_sample=tail_audio_start_sample,
                    end_sample=audio.size,
                    source_rate=source_rate,
                )
                if self.settings.incremental_reference_transcription
                else {}
            )
            reference_texts: dict[str, str] = {}
            reference_seconds: dict[str, float] = {}
            reference_audio_seconds: dict[str, float] = {}
            reference_errors: dict[str, str] = {}
            with self._transcriber_lock:
                transcriber = self._get_transcriber()
                tail_result = transcriber.transcribe_audio(
                    audio[tail_audio_start_sample:],
                    source_rate,
                    mode="speaker_attributed_asr",
                    start_offset_seconds=tail_audio_start_sample / float(source_rate),
                    initial_prefix_text=prefix_text,
                )
                for reference_track, reference_item in tail_reference_audio.items():
                    reference_audio, reference_rate = reference_item
                    if reference_audio.size == 0:
                        continue

                    reference_started = time.perf_counter()
                    try:
                        reference_result = transcriber.transcribe_audio(
                            reference_audio,
                            reference_rate,
                            mode="plain_asr",
                            start_offset_seconds=tail_audio_start_sample / float(source_rate),
                            max_new_tokens=self.settings.reference_max_new_tokens,
                        )
                        reference_texts[reference_track] = reference_result.full_text
                        reference_seconds[reference_track] = elapsed_seconds(reference_started)
                        reference_audio_seconds[reference_track] = round(
                            reference_audio.size / float(reference_rate),
                            3,
                        )
                    except Exception as error:
                        reference_errors[reference_track] = str(error)
            tail_segments = prepare_incremental_segments(
                tail_result.segments,
                segments,
                commit_start_sample=completed_until_sample,
                commit_end_sample=audio.size,
                sample_rate=source_rate,
                boundary_reason="finish_tail",
                boundary_rms=None,
                overlap_samples=tail_overlap_samples,
            )
            record.incremental_boundary_decisions.append(
                incremental_boundary_decision_payload(
                    task_index=record.incremental_tasks_submitted,
                    audio_start_sample=tail_audio_start_sample,
                    commit_start_sample=completed_until_sample,
                    commit_end_sample=audio.size,
                    sample_rate=source_rate,
                    boundary_reason="finish_tail",
                    boundary_rms=None,
                    overlap_samples=tail_overlap_samples,
                )
            )
            apply_incremental_reference_results(
                record,
                reference_texts=reference_texts,
                reference_seconds=reference_seconds,
                reference_audio_seconds=reference_audio_seconds,
                reference_errors=reference_errors,
            )
            segments = reindex_segments(segments + tail_segments)

        return TranscriptionResult(
            model=self.settings.model_name,
            mode="speaker_attributed_asr",
            sample_rate=self.settings.sample_rate,
            full_text=segments_full_text(segments),
            segments=segments,
        )

    def _base_transcript(self, record: SessionRecord, *, status: str) -> dict[str, Any]:
        return {
            "session_id": record.session_id,
            "status": status,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "duration_seconds": record.duration_seconds,
            "audio_path": exposed_wav_path(record, record.wav_path),
            "compressed_audio_path": str(record.compressed_audio_path),
            "compressed_audio_error": record.compressed_audio_error,
            "you_audio_path": exposed_wav_path(record, record.mic_wav_path),
            "caller_audio_path": exposed_wav_path(record, record.caller_wav_path),
            "transcript_path": str(record.transcript_path),
            "conversation_path": str(record.conversation_path),
            "session_dir": str(record.session_dir),
            "final_session_dir": str(record.final_session_dir) if record.final_session_dir else None,
            "callee": record.callee,
            "subject": record.subject,
            "model": self.settings.model_name,
            "mode": record.transcript_mode,
            "full_text": "",
            "segments": [],
            "speech_model_warmup": {
                **self.speech_warmup_state(),
                "requested_for_session": record.speech_warmup_requested,
                "session_status": record.speech_warmup_status,
                "session_seconds": record.speech_warmup_seconds,
                "session_error": record.speech_warmup_error,
            },
            "incremental_transcription": {
                "enabled": self.settings.incremental_transcription,
                "reference_transcription_enabled": self.settings.incremental_reference_transcription,
                "segment_seconds": self.settings.incremental_segment_seconds,
                "boundary_search_seconds": self.settings.incremental_boundary_search_seconds,
                "max_segment_seconds": self.settings.incremental_max_segment_seconds,
                "overlap_seconds": self.settings.incremental_overlap_seconds,
                "silence_rms": self.settings.incremental_silence_rms,
                "silence_window_ms": self.settings.incremental_silence_window_ms,
                "tasks_submitted": record.incremental_tasks_submitted,
                "tasks_completed": record.incremental_tasks_completed,
                "boundary_decisions": record.incremental_boundary_decisions,
                "completed_audio_seconds": round(
                    record.incremental_completed_until_sample / float(record.sample_rate),
                    3,
                )
                if record.sample_rate
                else 0.0,
                "transcribe_seconds": record.incremental_transcribe_seconds,
                "mixed_transcribe_seconds": record.incremental_mixed_transcribe_seconds,
                "audio_seconds": record.incremental_audio_seconds,
                "error": record.incremental_transcript_error,
                "speaker_reference": {
                    "text_chars": {
                        track: len(text)
                        for track, text in record.incremental_reference_text.items()
                    },
                    "transcribe_seconds": record.incremental_reference_transcribe_seconds,
                    "audio_seconds": record.incremental_reference_audio_seconds,
                    "tasks_completed": record.incremental_reference_tasks_completed,
                    "errors": record.incremental_reference_errors,
                },
            },
            "speaker_reference": {
                "mode": self.settings.speaker_reference_mode,
                "seconds": self.settings.speaker_reference_seconds,
                "window_seconds": self.settings.speaker_reference_window_seconds,
                "min_rms": self.settings.speaker_reference_min_rms,
                "reference_max_new_tokens": self.settings.reference_max_new_tokens,
            },
            "capture": {
                "mic_captured": record.mic_captured,
                "mic_error": record.mic_error,
                "mic_track_label": record.mic_track_label,
                "mic_peak": record.mic_peak,
                "tab_peak": record.tab_peak,
                "mixed_peak": record.mixed_peak,
                "track_chunks_received": record.track_chunks_received,
                "track_durations_seconds": record.track_durations_seconds,
                "track_upload_errors": record.track_upload_errors,
                "compressed_audio_enabled": self.settings.compress_audio,
                "compressed_audio_format": self.settings.compressed_audio_format,
                "wav_files_retained": record.wav_files_retained,
                "wav_files_available": {
                    "mixed": record.wav_path.exists(),
                    "mic": record.mic_wav_path.exists(),
                    "caller": record.caller_wav_path.exists(),
                },
            },
        }

    def _finalize_session_dir(self, record: SessionRecord) -> None:
        if record.final_session_dir:
            return

        local_start = parse_datetime(record.started_at).astimezone()
        day_dir = self.settings.recordings_dir / local_start.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        timestamp = local_start.strftime("%Y%m%dT%H%M%S%z")
        subject_slug = slugify(record.subject or "conversation", default="conversation", max_length=MAX_FOLDER_NAME_LENGTH)
        final_dir = unique_final_path(day_dir, timestamp, subject_slug)

        if record.session_dir != final_dir:
            record.session_dir.rename(final_dir)

        record.final_session_dir = final_dir
        rebind_paths(record, final_dir)

    def _write_metadata(self, record: SessionRecord) -> None:
        write_json(record.metadata_path, record_to_json(record))


def read_pcm16_range(path: Path, start_sample: int, end_sample: int, *, channels: int) -> bytes:
    frame_size = 2 * channels
    if frame_size <= 0 or end_sample <= start_sample:
        return b""

    with path.open("rb") as handle:
        handle.seek(start_sample * frame_size)
        return handle.read((end_sample - start_sample) * frame_size)


def choose_incremental_boundary(
    pcm_path: Path,
    start_sample: int,
    available_samples: int,
    *,
    channels: int,
    sample_rate: int,
    settings: Settings,
) -> dict[str, Any] | None:
    if sample_rate <= 0 or channels <= 0:
        return None

    target_samples = max(1, int(round(settings.incremental_segment_seconds * sample_rate)))
    max_samples = max(target_samples, int(round(settings.incremental_max_segment_seconds * sample_rate)))
    target_end_sample = start_sample + target_samples
    max_end_sample = start_sample + max_samples
    if available_samples < target_end_sample:
        return None

    search_samples = max(0, int(round(settings.incremental_boundary_search_seconds * sample_rate)))
    window_samples = max(1, int(round(settings.incremental_silence_window_ms / 1000.0 * sample_rate)))
    search_start_sample = max(start_sample, target_end_sample - search_samples)
    search_end_sample = min(available_samples, max_end_sample)
    silence = find_quiet_boundary(
        pcm_path,
        search_start_sample,
        search_end_sample,
        channels=channels,
        window_samples=window_samples,
        silence_rms=max(0.0, settings.incremental_silence_rms),
    )
    if silence is not None:
        if int(silence["commit_end_sample"]) <= start_sample:
            return None
        return silence

    if available_samples < max_end_sample:
        return None

    return {
        "commit_end_sample": max_end_sample,
        "boundary_reason": "max_overlap",
        "boundary_rms": None,
    }


def find_quiet_boundary(
    pcm_path: Path,
    search_start_sample: int,
    search_end_sample: int,
    *,
    channels: int,
    window_samples: int,
    silence_rms: float,
) -> dict[str, Any] | None:
    if search_end_sample - search_start_sample < window_samples:
        return None

    data = read_pcm16_range(pcm_path, search_start_sample, search_end_sample, channels=channels)
    audio = pcm16_bytes_to_mono_float32(data, channels=channels)
    if audio.size < window_samples:
        return None

    squared = np.square(audio.astype(np.float32, copy=False))
    cumulative = np.concatenate(([0.0], np.cumsum(squared, dtype=np.float64)))
    window_sums = cumulative[window_samples:] - cumulative[:-window_samples]
    if window_sums.size == 0:
        return None

    rms_values = np.sqrt(window_sums / float(window_samples))
    best_index = int(np.argmin(rms_values))
    best_rms = float(rms_values[best_index])
    if best_rms > silence_rms:
        return None

    return {
        "commit_end_sample": search_start_sample + best_index + window_samples // 2,
        "boundary_reason": "silence",
        "boundary_rms": round(best_rms, 6),
    }


def incremental_boundary_decision_payload(
    *,
    task_index: int,
    audio_start_sample: int,
    commit_start_sample: int,
    commit_end_sample: int,
    sample_rate: int,
    boundary_reason: str,
    boundary_rms: float | None,
    overlap_samples: int,
) -> dict[str, Any]:
    return {
        "task_index": task_index,
        "boundary_reason": boundary_reason,
        "boundary_rms": boundary_rms,
        "sample_rate": sample_rate,
        "audio_start_sample": audio_start_sample,
        "commit_start_sample": commit_start_sample,
        "commit_end_sample": commit_end_sample,
        "audio_start_seconds": round(audio_start_sample / float(sample_rate), 3) if sample_rate else 0.0,
        "commit_start_seconds": round(commit_start_sample / float(sample_rate), 3) if sample_rate else 0.0,
        "commit_end_seconds": round(commit_end_sample / float(sample_rate), 3) if sample_rate else 0.0,
        "overlap_seconds": round(overlap_samples / float(sample_rate), 3) if sample_rate else 0.0,
    }


def boundary_reason_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        reason = str(decision.get("boundary_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def prepare_incremental_segments(
    new_segments: list[dict[str, Any]],
    existing_segments: list[dict[str, Any]],
    *,
    commit_start_sample: int,
    commit_end_sample: int,
    sample_rate: int,
    boundary_reason: str,
    boundary_rms: float | None,
    overlap_samples: int,
) -> list[dict[str, Any]]:
    commit_start_seconds = commit_start_sample / float(sample_rate) if sample_rate else 0.0
    commit_end_seconds = commit_end_sample / float(sample_rate) if sample_rate else 0.0
    previous_text = segments_content_text(existing_segments)
    trimming_overlap = overlap_samples > 0 and bool(previous_text)
    prepared: list[dict[str, Any]] = []

    for segment in new_segments:
        item = dict(segment)
        source_start_seconds = float(item.get("start_seconds") or commit_start_seconds)
        source_end_seconds = float(item.get("end_seconds") or commit_end_seconds)
        turns = segment_speaker_turns(item)
        if trimming_overlap and turns:
            turns = trim_repeated_turn_prefix(previous_text, turns)
            if turns:
                trimming_overlap = False
            else:
                continue

        if turns:
            text = format_speaker_turns(turns)
        elif trimming_overlap:
            text = trim_repeated_text_prefix(previous_text, str(item.get("text") or ""))
            if text:
                trimming_overlap = False
        else:
            text = normalize_text_space(str(item.get("text") or ""))
        if not text:
            continue

        item["source_start_seconds"] = round(source_start_seconds, 3)
        item["source_end_seconds"] = round(source_end_seconds, 3)
        item["start_seconds"] = round(max(source_start_seconds, commit_start_seconds), 3)
        item["end_seconds"] = round(min(max(source_end_seconds, commit_start_seconds), commit_end_seconds), 3)
        item["text"] = text
        item["speaker_turns"] = turns if turns else parse_conversation_speaker_turns(text)
        item["incremental_boundary"] = {
            "reason": boundary_reason,
            "rms": boundary_rms,
            "overlap_seconds": round(overlap_samples / float(sample_rate), 3) if sample_rate else 0.0,
            "commit_start_seconds": round(commit_start_seconds, 3),
            "commit_end_seconds": round(commit_end_seconds, 3),
        }
        prepared.append(item)

    return prepared


def segment_speaker_turns(segment: dict[str, Any]) -> list[dict[str, str]]:
    raw_turns = segment.get("speaker_turns") or parse_conversation_speaker_turns(str(segment.get("text") or ""))
    turns: list[dict[str, str]] = []
    for turn in raw_turns:
        speaker = str(turn.get("speaker") or "Unknown").strip() or "Unknown"
        text = normalize_text_space(str(turn.get("text") or ""))
        if text:
            turns.append({"speaker": speaker, "text": text})
    return turns


def trim_repeated_turn_prefix(previous_text: str, turns: list[dict[str, str]]) -> list[dict[str, str]]:
    trimmed_turns: list[dict[str, str]] = []
    still_trimming = True
    for turn in turns:
        item = dict(turn)
        if still_trimming:
            item["text"] = trim_repeated_text_prefix(previous_text, item["text"])
            if not item["text"]:
                continue
            still_trimming = False
        trimmed_turns.append(item)
    return trimmed_turns


def trim_repeated_text_prefix(previous_text: str, text: str) -> str:
    text = normalize_text_space(text)
    if not previous_text or not text:
        return text

    previous_tokens = token_spans(previous_text)[-40:]
    current_tokens = token_spans(text)[:40]
    max_overlap = min(len(previous_tokens), len(current_tokens))
    for length in range(max_overlap, 0, -1):
        previous_slice = [token for token, _start, _end in previous_tokens[-length:]]
        current_slice = [token for token, _start, _end in current_tokens[:length]]
        if previous_slice != current_slice or not meaningful_token_overlap(current_slice):
            continue

        cut_index = current_tokens[length - 1][2]
        return text[cut_index:].lstrip(" \t\r\n,.;:-")

    return text


def meaningful_token_overlap(tokens: list[str]) -> bool:
    return len(tokens) >= 3 or sum(len(token) for token in tokens) >= 12


def token_spans(text: str) -> list[tuple[str, int, int]]:
    return [(match.group(0).lower(), match.start(), match.end()) for match in re.finditer(r"[A-Za-z0-9']+", text)]


def segments_content_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        turns = segment_speaker_turns(segment)
        if turns:
            parts.extend(turn["text"] for turn in turns)
        else:
            text = strip_speaker_labels(str(segment.get("text") or ""))
            if text:
                parts.append(text)
    return normalize_text_space(" ".join(parts))


def strip_speaker_labels(text: str) -> str:
    return normalize_text_space(re.sub(r"\[(?:Speaker\s*\d+|You|Callee|Caller|Unknown)\]:", " ", text, flags=re.I))


def format_speaker_turns(turns: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for turn in turns:
        speaker = str(turn.get("speaker") or "Unknown").strip() or "Unknown"
        text = normalize_text_space(str(turn.get("text") or ""))
        if not text:
            continue
        label = f"{speaker}:" if speaker.startswith("[") else f"[{speaker}]:"
        parts.append(f"{label} {text}")
    return " ".join(parts).strip()


def normalize_text_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def segments_full_text(segments: list[dict[str, Any]]) -> str:
    return "\n".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip())


def apply_incremental_reference_results(
    record: SessionRecord,
    *,
    reference_texts: dict[str, str],
    reference_seconds: dict[str, float],
    reference_audio_seconds: dict[str, float],
    reference_errors: dict[str, str],
) -> None:
    for reference_track in ("mic", "caller"):
        if reference_track in reference_texts:
            append_incremental_reference_text(record, reference_track, reference_texts[reference_track])
            record.incremental_reference_errors.pop(reference_track, None)
        if reference_track in reference_seconds:
            record.incremental_reference_transcribe_seconds[reference_track] = round(
                record.incremental_reference_transcribe_seconds.get(reference_track, 0.0)
                + reference_seconds[reference_track],
                3,
            )
        if reference_track in reference_audio_seconds:
            record.incremental_reference_audio_seconds[reference_track] = round(
                record.incremental_reference_audio_seconds.get(reference_track, 0.0)
                + reference_audio_seconds[reference_track],
                3,
            )
        if reference_track in reference_texts or reference_track in reference_errors:
            record.incremental_reference_tasks_completed[reference_track] = (
                record.incremental_reference_tasks_completed.get(reference_track, 0) + 1
            )
        if reference_track in reference_errors:
            record.incremental_reference_errors[reference_track] = reference_errors[reference_track]


def append_incremental_reference_text(record: SessionRecord, track: str, text: str) -> None:
    text = normalize_text_space(text)
    if not text:
        return

    previous = normalize_text_space(record.incremental_reference_text.get(track, ""))
    if previous:
        text = trim_repeated_text_prefix(previous, text)
    if not text:
        return

    record.incremental_reference_text[track] = normalize_text_space(f"{previous} {text}")


def reindex_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_segments = sorted(
        (dict(segment) for segment in segments),
        key=lambda segment: float(segment.get("start_seconds") or 0.0),
    )
    for index, segment in enumerate(sorted_segments):
        segment["index"] = index
    return sorted_segments


def transcribe_reference_track(
    transcriber: GraniteTranscriber,
    wav_path: Path,
    *,
    settings: Settings,
    transcriber_lock: Lock,
) -> tuple[str, float]:
    if not wav_path.exists() or wav_path.stat().st_size <= 44:
        return "", 0.0

    try:
        mode = settings.speaker_reference_mode.strip().lower()
        if mode in {"", "off", "none", "disabled", "0", "false"}:
            return "", 0.0

        audio, source_rate = load_wav_mono_float32(wav_path)
        if mode == "full":
            reference_audio = audio
        else:
            reference_audio = select_reference_audio(audio, source_rate, settings=settings)

        if reference_audio.size == 0:
            return "", 0.0

        with transcriber_lock:
            text = transcriber.transcribe_audio(
                reference_audio,
                source_rate,
                mode="plain_asr",
                max_new_tokens=settings.reference_max_new_tokens,
            ).full_text
        return text, round(reference_audio.size / float(source_rate), 3)
    except Exception:
        return "", 0.0


def load_reference_tail_audio(
    record: SessionRecord,
    *,
    start_sample: int,
    end_sample: int,
    source_rate: int,
) -> dict[str, tuple[np.ndarray, int]]:
    items: dict[str, tuple[np.ndarray, int]] = {}
    for track in ("mic", "caller"):
        wav_path = track_wav_path(record, track)
        if not wav_path.exists() or wav_path.stat().st_size <= 44:
            continue

        try:
            audio, sample_rate = load_wav_mono_float32(wav_path)
        except Exception:
            continue

        if sample_rate <= 0 or audio.size == 0:
            continue

        if source_rate > 0 and sample_rate != source_rate:
            track_start = int(round(start_sample * sample_rate / float(source_rate)))
            track_end = int(round(end_sample * sample_rate / float(source_rate)))
        else:
            track_start = start_sample
            track_end = end_sample

        track_start = max(0, min(track_start, audio.size))
        track_end = max(track_start, min(track_end, audio.size))
        items[track] = (audio[track_start:track_end], sample_rate)

    return items


def select_reference_audio(audio: np.ndarray, sample_rate: int, *, settings: Settings) -> np.ndarray:
    if audio.size == 0 or sample_rate <= 0:
        return np.array([], dtype=np.float32)

    target_seconds = max(0, settings.speaker_reference_seconds)
    if target_seconds <= 0:
        return np.array([], dtype=np.float32)

    target_samples = min(audio.size, int(target_seconds * sample_rate))
    window_seconds = max(1, settings.speaker_reference_window_seconds)
    window_samples = max(1, min(target_samples, int(window_seconds * sample_rate)))
    min_rms = max(0.0, settings.speaker_reference_min_rms)

    candidates: list[tuple[float, int, int]] = []
    for start in range(0, audio.size, window_samples):
        end = min(start + window_samples, audio.size)
        window = audio[start:end]
        if window.size == 0:
            continue

        rms = float(np.sqrt(np.mean(np.square(window.astype(np.float32, copy=False)))))
        if rms >= min_rms:
            candidates.append((rms, start, end))

    if not candidates:
        return np.array([], dtype=np.float32)

    selected: list[tuple[int, int]] = []
    selected_samples = 0
    for _rms, start, end in sorted(candidates, reverse=True):
        window_samples = end - start
        if selected_samples >= target_samples:
            break

        if selected_samples + window_samples > target_samples:
            end = start + (target_samples - selected_samples)
            window_samples = end - start
        if window_samples <= 0:
            continue

        selected.append((start, end))
        selected_samples += window_samples

    if not selected:
        return np.array([], dtype=np.float32)

    selected.sort()
    return np.concatenate([audio[start:end] for start, end in selected]).astype(np.float32, copy=False)


def compress_recording_audio(
    wav_path: Path,
    output_path: Path,
    *,
    output_format: str,
    bitrate: str,
) -> str | None:
    try:
        if output_format != "opus":
            raise ValueError(f"Unsupported compressed audio format: {output_format}")
        write_opus_from_wav(wav_path, output_path, bitrate=bitrate, force=True)
        return None
    except Exception as error:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        return str(error)


def apply_wav_retention(record: SessionRecord, *, keep_wav_files: bool) -> None:
    record.wav_files_retained = keep_wav_files
    if keep_wav_files:
        return

    for track in TRACKS:
        try:
            track_wav_path(record, track).unlink(missing_ok=True)
        except Exception:
            pass


def exposed_wav_path(record: SessionRecord, wav_path: Path) -> str | None:
    if record.wav_files_retained or wav_path.exists():
        return str(wav_path)
    return None


def resolve_segment_speakers(segments: list[dict[str, Any]], speaker_map: dict[str, str]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []

    for segment in segments:
        item = dict(segment)
        turns = []
        for turn in segment.get("speaker_turns") or []:
            speaker = speaker_map.get(str(turn.get("speaker") or ""), "Callee")
            turns.append(
                {
                    **turn,
                    "speaker": speaker,
                }
            )
        item["speaker_turns"] = turns
        item["text"] = " ".join(f"[{turn['speaker']}]: {turn['text']}" for turn in turns).strip() if turns else item.get("text", "")
        resolved.append(item)

    return resolved


def normalize_track(track: str) -> str:
    track = (track or "mixed").strip().lower()
    if track == "tab":
        track = "caller"
    if track not in TRACKS:
        raise ValueError(f"Unknown audio track: {track}")
    return track


def normalize_track_errors(value: Any, fallback: list[str]) -> dict[str, list[str]]:
    if isinstance(value, dict):
        return {track: list(value.get(track) or []) for track in TRACKS}
    return {"mixed": fallback, "mic": [], "caller": []}


def track_pcm_path(record: SessionRecord, track: str) -> Path:
    if track == "mic":
        return record.mic_pcm_path
    if track == "caller":
        return record.caller_pcm_path
    return record.pcm_path


def track_wav_path(record: SessionRecord, track: str) -> Path:
    if track == "mic":
        return record.mic_wav_path
    if track == "caller":
        return record.caller_wav_path
    return record.wav_path


def rebind_paths(record: SessionRecord, session_dir: Path) -> None:
    record.session_dir = session_dir
    record.pcm_path = session_dir / TRACK_FILENAMES["mixed"][0]
    record.wav_path = session_dir / TRACK_FILENAMES["mixed"][1]
    record.mic_pcm_path = session_dir / TRACK_FILENAMES["mic"][0]
    record.mic_wav_path = session_dir / TRACK_FILENAMES["mic"][1]
    record.caller_pcm_path = session_dir / TRACK_FILENAMES["caller"][0]
    record.caller_wav_path = session_dir / TRACK_FILENAMES["caller"][1]
    record.compressed_audio_path = compressed_audio_path(record.wav_path)
    record.transcript_path = session_dir / "transcript.json"
    record.conversation_path = session_dir / "conversation.txt"
    record.metadata_path = session_dir / "session.json"


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not allocate unique session directory for {path}")


def unique_final_path(day_dir: Path, timestamp: str, subject_slug: str) -> Path:
    for index in range(1, 1000):
        suffix = "" if index == 1 else f"_{index}"
        max_component_subject_length = MAX_FOLDER_NAME_LENGTH - len(timestamp) - 1 - len(suffix)
        max_path_subject_length = MAX_WINDOWS_DIRECTORY_PATH_LENGTH - len(str(day_dir.resolve())) - 1 - len(timestamp) - 1 - len(suffix)
        max_subject_length = max(12, min(max_component_subject_length, max_path_subject_length))
        safe_subject = subject_slug[:max_subject_length].strip("._- ") or "conversation"
        candidate = day_dir / f"{timestamp}_{safe_subject}{suffix}"
        if (
            len(candidate.name) <= MAX_FOLDER_NAME_LENGTH
            and len(str(candidate.resolve())) <= MAX_WINDOWS_DIRECTORY_PATH_LENGTH
            and not candidate.exists()
        ):
            return candidate

    raise RuntimeError(f"Could not allocate unique final session directory for {timestamp}")


def elapsed_seconds(started: float) -> float:
    return round(time.perf_counter() - started, 3)


def record_to_json(record: SessionRecord) -> dict[str, Any]:
    return jsonable(asdict(record))


def jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
