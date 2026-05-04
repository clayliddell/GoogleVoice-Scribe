# GoogleVoice Scribe v0.2.0

Installer and packaging update.

## Assets

- `GoogleVoiceScribeSetup-v0.2.0-win-x64.exe` - per-user Windows installer for the local server and control app.
- `GoogleVoiceScribeExtension-v0.2.0.crx` - packaged Chromium MV3 extension.

## Notes

- Adds a launchable control app with Start Server, Stop Server, and core config checkboxes.
- The installer updates an existing per-user install when run with the same or a newer version.
- Completed transcript folders now keep compressed `audio.opus` by default and remove large WAV files unless `GV_KEEP_WAV_FILES=1`.
- Model weights are not bundled. The server downloads and caches model files locally on first use.
- Review local call-recording consent requirements before use.
