# GoogleVoice Scribe v0.1.0

Initial public release.

## Assets

- `GoogleVoiceScribeServer-v0.1.0-win-x64.zip` - Windows server launcher and service package.
- `GoogleVoiceScribeExtension-v0.1.0.zip` - Chromium MV3 extension package for sideloading.

## Notes

- Model weights are not bundled. The server downloads and caches model files locally on first use.
- The server package creates/uses a local `.venv` beside the launcher and installs CUDA Python dependencies there.
- Review local call-recording consent requirements before use.
