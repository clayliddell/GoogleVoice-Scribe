# Google Voice Local Transcriber

Chromium MV3 extension plus a local Python service for recording outgoing Google Voice calls and transcribing them locally with `ibm-granite/granite-speech-4.1-2b-plus`.

The extension records Google Voice tab audio and microphone audio, sends mono PCM chunks to the local service, and stops when the Google Voice call UI disappears or when you manually stop it from the extension action. The service writes a mixed `audio.wav`, separate `you.wav` and `caller.wav` tracks, then runs Granite Speech on CUDA when the model dependencies are installed.

## Project Layout

- `extension/` - unpacked Chromium extension.
- `service/` - local FastAPI transcription service.
- `scripts/start-service.ps1` - helper for launching the local service.

## Requirements

- Windows with an NVIDIA GPU and current driver.
- Chromium or Chrome 116+.
- Python 3.12 is recommended for PyTorch CUDA wheels. Python 3.14 may not have compatible PyTorch wheels yet.
- A CUDA-enabled PyTorch install.

## Install The Service

Create a Python 3.12 virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install the app dependencies:

```powershell
.\scripts\install-service-deps.ps1
```

That helper installs the service packages and then replaces the default CPU-only Windows PyTorch wheel with CUDA-matched `torch==2.6.0+cu124` and `torchaudio==2.6.0+cu124`, which work with the RTX 3070 and your NVIDIA driver.

The helper also installs `llama-cpp-python` from the CUDA 12.4 wheel index for local GGUF title generation.

The helper installs `imageio-ffmpeg` so the service can write compressed Opus playback files without requiring a system `ffmpeg` install.

The Granite model card says the plus model code may require a recent or source install of Transformers. If the installed PyPI release does not include `granite_speech_plus`, install the latest Transformers from source:

```powershell
pip install git+https://github.com/huggingface/transformers.git
```

## Run The Service

```powershell
.\scripts\start-service.ps1
```

Defaults:

- URL: `http://127.0.0.1:8765`
- Recordings folder: `%USERPROFILE%\Documents\Google Voice Transcripts`
- Model: `ibm-granite/granite-speech-4.1-2b-plus`
- Subject-title backend: `llama_cpp`
- Subject-title model: `unsloth/gemma-4-E4B-it-GGUF`
- Subject-title GGUF file: `gemma-4-E4B-it-Q4_K_M.gguf`
- Keep title model warm: `GV_KEEP_TITLE_MODEL=1`
- Mixed-audio incremental transcription: `GV_INCREMENTAL_TRANSCRIPTION=1`
- Incremental mixed-audio window: `GV_INCREMENTAL_SEGMENT_SECONDS=60`
- Incremental boundary search: `GV_INCREMENTAL_BOUNDARY_SEARCH_SECONDS=3`
- Incremental max window before overlap fallback: `GV_INCREMENTAL_MAX_SEGMENT_SECONDS=75`
- Incremental overlap context: `GV_INCREMENTAL_OVERLAP_SECONDS=1.5`
- Granite warmup on call start: `GV_WARM_GRANITE_ON_CALL_START=1`
- Speaker reference mode: `GV_SPEAKER_REFERENCE_MODE=sampled`
- Speaker reference audio per side: `GV_SPEAKER_REFERENCE_SECONDS=60`

The subject-title model is Apache-2.0 licensed and does not require accepting a gated Hugging Face license. The first title generation downloads the Q4_K_M GGUF file into the Hugging Face cache. By default, model loads are local-first: the service checks the local Hugging Face cache without a network request, and only contacts the Hub if a required file is missing. Set `GV_HF_LOCAL_FILES_ONLY=1` for strict offline mode after the models are cached. By default, the title model stays loaded after title generation for faster follow-up titles; it is released automatically before Granite Speech is loaded for the next transcription.

Override with environment variables:

```powershell
$env:GV_RECORDINGS_DIR = "D:\VoiceTranscripts"
$env:GV_SERVICE_PORT = "8765"
$env:GV_TRANSCRIBE = "1"
$env:GV_SEGMENT_SECONDS = "240"
$env:GV_TITLE_BACKEND = "llama_cpp"
$env:GV_TITLE_MODEL_NAME = "unsloth/gemma-4-E4B-it-GGUF"
$env:GV_TITLE_GGUF_FILENAME = "gemma-4-E4B-it-Q4_K_M.gguf"
$env:GV_TITLE_CONTEXT_TOKENS = "2048"
$env:GV_TITLE_GPU_LAYERS = "-1"
$env:GV_TITLE_CACHE_TYPE_K = "q8_0"
$env:GV_TITLE_CACHE_TYPE_V = "q8_0"
$env:GV_TITLE_FLASH_ATTN = "1"
$env:GV_KEEP_TITLE_MODEL = "1"
$env:GV_HF_LOCAL_FIRST = "1"
$env:GV_HF_LOCAL_FILES_ONLY = "0"
$env:GV_COMPRESS_AUDIO = "1"
$env:GV_COMPRESSED_AUDIO_FORMAT = "opus"
$env:GV_OPUS_BITRATE = "24k"
$env:GV_INCREMENTAL_TRANSCRIPTION = "1"
$env:GV_INCREMENTAL_SEGMENT_SECONDS = "60"
$env:GV_INCREMENTAL_BOUNDARY_SEARCH_SECONDS = "3"
$env:GV_INCREMENTAL_MAX_SEGMENT_SECONDS = "75"
$env:GV_INCREMENTAL_OVERLAP_SECONDS = "1.5"
$env:GV_INCREMENTAL_SILENCE_RMS = "0.0025"
$env:GV_INCREMENTAL_SILENCE_WINDOW_MS = "250"
$env:GV_WARM_GRANITE_ON_CALL_START = "1"
$env:GV_SPEAKER_REFERENCE_MODE = "sampled"
$env:GV_SPEAKER_REFERENCE_SECONDS = "60"
$env:GV_SPEAKER_REFERENCE_WINDOW_SECONDS = "20"
$env:GV_SPEAKER_REFERENCE_MIN_RMS = "0.003"
.\scripts\start-service.ps1
```

To use the older full Transformers title path instead, set `GV_TITLE_BACKEND=transformers` and point `GV_TITLE_MODEL_NAME` at a Transformers-compatible model.

For faster long-call turnaround, the service transcribes sealed mixed-audio windows while the call is still recording. The incremental window length is a target: the service prefers a nearby quiet boundary, waits up to the configured max window, and adds overlap context when a clean pause is unavailable. When the call ends, it waits for in-flight windows and transcribes only the remaining tail. Speaker labels use short RMS-selected samples from `you.wav` and `caller.wav` by default instead of fully transcribing both side tracks.

## Load The Extension

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose "Load unpacked".
4. Select the `extension` folder.
5. Open `https://voice.google.com/`.
6. Click the extension action once to arm the current Google Voice tab.
7. Click the Google Voice Call button. Recording starts on that outgoing call.

If microphone capture is blocked, open `extension/ui/permission.html` from the extension details page and grant microphone access, then retry the next call.

## Output

Active recordings are first written under a colocated `_tmp` directory. After transcription, the folder is moved once into the date directory with a short subject in the name.

Each completed call creates:

```text
YYYY-MM-DD/
  YYYYMMDDTHHMMSS-0700_Subject_Title/
    audio.wav
    audio.opus
    you.wav
    caller.wav
    transcript.json
    session.json
    conversation.txt
```

`audio.opus` is a compressed playback copy of the mixed recording for listening/storage. The original WAV files are kept for transcription and debugging.

`conversation.txt` is the human-readable transcript:

```text
[You]: ...
[Callee]: ...
```

`transcript.json` contains the resolved `[You]`/`[Callee]` transcript, raw model transcript fields for debugging, model metadata, segment information, capture diagnostics, and any transcription error if the WAV was saved but model inference failed.

To create `conversation.txt` for existing transcribed sessions:

```powershell
.\scripts\backfill-conversations.ps1
```

To create missing `audio.opus` files for existing sessions:

```powershell
.\scripts\compress-recordings.ps1
```

Use `-Force` to recreate existing compressed files.

## Benchmark A 15-Minute Pipeline Run

Replay the latest completed call through the same local service API used by the extension:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark-pipeline.py `
  --duration-seconds 900 `
  --service-url http://127.0.0.1:8765 `
  --chunk-frames 4096 `
  --sample-rate 48000
```

The benchmark loops or trims `audio.wav`, `caller.wav`, and `you.wav` from the source session to exactly 15 minutes, uploads extension-shaped PCM chunks, waits for transcription/title generation/finalization, then writes a JSON report under `benchmarks/`.

To pace replay like a real call, add `--realtime-upload`. This keeps the extension-shaped chunk pattern but waits between sequences so 15 minutes of fixture audio takes about 15 minutes to upload:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark-pipeline.py `
  --duration-seconds 150 `
  --service-url http://127.0.0.1:8765 `
  --chunk-frames 4096 `
  --sample-rate 48000 `
  --realtime-upload `
  --progress-every 100
```

Use a fixed source session when comparing runs:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark-pipeline.py `
  --source-session "C:\Users\Pew Pew Control\Documents\Google Voice Transcripts\2026-05-03\..." `
  --duration-seconds 900 `
  --runs 2
```

## Legal Note

Call-recording consent laws vary by jurisdiction. Use this only where you have the required consent.
