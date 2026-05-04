from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np


def write_wav_from_pcm16(
    pcm_path: Path,
    wav_path: Path,
    *,
    sample_rate: int,
    channels: int,
) -> float:
    data = pcm_path.read_bytes()
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(data)

    if sample_rate <= 0 or channels <= 0:
        return 0.0
    return len(data) / float(2 * channels * sample_rate)


def pcm16_bytes_to_mono_float32(data: bytes, *, channels: int) -> np.ndarray:
    if not data:
        return np.array([], dtype=np.float32)

    audio = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1 and audio.size:
        usable = audio.size - (audio.size % channels)
        if usable <= 0:
            return np.array([], dtype=np.float32)
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)
    return audio.astype(np.float32, copy=False)


def compressed_audio_path(wav_path: Path, *, output_format: str = "opus") -> Path:
    if output_format != "opus":
        raise ValueError(f"Unsupported compressed audio format: {output_format}")
    return wav_path.with_suffix(".opus")


def write_opus_from_wav(
    wav_path: Path,
    opus_path: Path,
    *,
    bitrate: str = "24k",
    force: bool = True,
) -> None:
    if not wav_path.exists() or wav_path.stat().st_size <= 44:
        raise FileNotFoundError(f"Missing source WAV for Opus compression: {wav_path}")

    from imageio_ffmpeg import get_ffmpeg_exe

    opus_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        get_ffmpeg_exe(),
        "-y" if force else "-n",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav_path),
        "-vn",
        "-ac",
        "1",
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-application",
        "voip",
        str(opus_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(error or f"ffmpeg exited with status {result.returncode}")

    if not opus_path.exists() or opus_path.stat().st_size == 0:
        raise RuntimeError(f"Opus encoder did not create output: {opus_path}")


def load_wav_mono_float32(wav_path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1 and audio.size:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return audio, sample_rate


def resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)

    duration = audio.size / float(source_rate)
    target_size = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)
