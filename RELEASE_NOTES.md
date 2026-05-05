# GoogleVoice Scribe v0.2.1

Granite Speech compatibility and installer dependency validation update.

## Assets

- `GoogleVoiceScribeSetup-v0.2.1-win-x64.exe` - per-user Windows installer for the local server and control app.
- `GoogleVoiceScribeExtension-v0.2.1.crx` - packaged Chromium MV3 extension.

## Notes

- Pins Transformers to a source revision that includes `granite_speech_plus` support for `ibm-granite/granite-speech-4.1-2b-plus`.
- Installer dependency checks now verify Granite Speech Plus support before treating the runtime as ready.
- Dependency install verification prints the Transformers version and fails early if the speech architecture is missing.
- Model weights are not bundled. The server downloads and caches model files locally on first use.
- Review local call-recording consent requirements before use.
