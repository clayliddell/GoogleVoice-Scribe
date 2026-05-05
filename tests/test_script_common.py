from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import script_common  # noqa: E402


def test_run_uses_no_window_creation_flags_when_requested(monkeypatch):
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(script_common.subprocess, "run", fake_run)
    monkeypatch.setenv("GV_HIDE_SUBPROCESS_WINDOWS", "1")

    script_common.run(["python", "--version"])

    assert calls[0]["creationflags"] == script_common.windows_creationflags()


def test_run_uses_normal_console_behavior_by_default(monkeypatch):
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(script_common.subprocess, "run", fake_run)
    monkeypatch.delenv("GV_HIDE_SUBPROCESS_WINDOWS", raising=False)

    script_common.run(["python", "--version"])

    assert calls[0]["creationflags"] == 0


def test_run_capture_uses_no_window_creation_flags_when_requested(monkeypatch):
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(script_common.subprocess, "run", fake_run)
    monkeypatch.setenv("GV_HIDE_SUBPROCESS_WINDOWS", "1")

    assert script_common.run_capture(["python", "--version"]) == "ok\n"
    assert calls[0]["creationflags"] == script_common.windows_creationflags()
