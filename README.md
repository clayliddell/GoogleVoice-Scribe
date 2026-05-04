# GoogleVoice Scribe

GoogleVoice Scribe is a Chromium extension plus a local Windows transcription
server for recording outgoing Google Voice calls. The extension captures Google
Voice tab audio and microphone audio, streams PCM chunks to `127.0.0.1`, and the
server writes local recordings and transcripts using local GPU inference.

The project is designed for local-first use on Windows with an NVIDIA GPU. Model
weights are not stored in this repository or bundled in releases.

## Release Packages

GitHub releases contain two assets:

- `GoogleVoiceScribeServer-v0.1.0-win-x64.zip` - Windows launcher executable plus server source package.
- `GoogleVoiceScribeExtension-v0.1.0.zip` - Chromium MV3 extension for sideloading.

The server ZIP is intentionally thin. On first run, `GoogleVoiceScribeServer.exe`
creates a local `.venv`, installs the Python/CUDA runtime dependencies, then
starts the local API. Granite Speech and Gemma GGUF model files are downloaded
into the user's Hugging Face cache on demand.

## Requirements

- Windows 10/11.
- NVIDIA GPU with current drivers. The default development target is RTX 3070.
- Python 3.12 available through the Windows `py` launcher or `python` on PATH.
- Chromium or Chrome 116+.
- Network access for first-time dependency/model downloads.

## Install From A GitHub Release

1. Download and extract `GoogleVoiceScribeServer-v0.1.0-win-x64.zip`.
2. Run `GoogleVoiceScribeServer.exe`.
3. Wait for the first-run dependency installation to complete.
4. Confirm the local service is healthy at `http://127.0.0.1:8765/health`.
5. Download and extract `GoogleVoiceScribeExtension-v0.1.0.zip`.
6. Open `chrome://extensions`, enable Developer mode, choose "Load unpacked",
   and select the extracted extension folder.
7. Open `https://voice.google.com/`, click the extension icon to arm recording,
   then start an outgoing call.

If microphone capture is blocked, open the extension options page and grant
microphone access before arming again.

## Development Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
.\scripts\install-service-deps.ps1
.\scripts\start-service.ps1
```

Load `extension/` as an unpacked Chromium extension during development.

## Configuration

Configuration is via environment variables. Copy `.env.example` for the complete
set of supported options.

Important defaults:

- API URL: `http://127.0.0.1:8765`
- Recordings folder: `%USERPROFILE%\Documents\Google Voice Transcripts`
- Speech model: `ibm-granite/granite-speech-4.1-2b-plus`
- Title model: `unsloth/gemma-4-E4B-it-GGUF`
- Incremental mixed transcription: `GV_INCREMENTAL_TRANSCRIPTION=1`
- Incremental `you`/`caller` reference transcription: `GV_INCREMENTAL_REFERENCE_TRANSCRIPTION=1`
- Reference decode cap: `GV_REFERENCE_MAX_NEW_TOKENS=128`
- Strict offline mode after caching: `GV_HF_LOCAL_FILES_ONLY=1`

Disable 3-track incremental reference transcription on weaker GPUs:

```powershell
$env:GV_INCREMENTAL_REFERENCE_TRANSCRIPTION = "0"
.\scripts\start-service.ps1
```

## Output

Completed calls are moved from `_tmp` into a date folder:

```text
YYYY-MM-DD/
  YYYYMMDDTHHMMSS-0700_subject/
    audio.wav
    audio.opus
    you.wav
    caller.wav
    transcript.json
    session.json
    conversation.txt
```

`conversation.txt` is the human-readable transcript:

```text
[You]: ...
[Callee]: ...
```

`audio.opus` is a compressed playback copy of the mixed recording. The WAV files
are retained for debugging and reprocessing.

## Maintenance Commands

Backfill conversations:

```powershell
.\scripts\backfill-conversations.ps1
```

Create missing compressed playback files:

```powershell
.\scripts\compress-recordings.ps1
```

Benchmark a simulated 15-minute call:

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark-pipeline.py `
  --duration-seconds 900 `
  --service-url http://127.0.0.1:8765 `
  --chunk-frames 4096 `
  --sample-rate 48000 `
  --realtime-upload `
  --progress-every 100
```

## Build Release Assets

```powershell
.\scripts\build-server-exe.ps1
.\scripts\build-extension.ps1
```

The generated ZIP files are written to `dist/`.

To create the GitHub repository and publish `v0.1.0`:

```powershell
.\scripts\release.ps1
```

## Legal

This project is MIT licensed. Model weights and runtime dependencies remain
subject to their own upstream licenses and terms. See `THIRD_PARTY_NOTICES.md`.

Call-recording consent laws vary by jurisdiction. Use this only where you have
the required consent.
