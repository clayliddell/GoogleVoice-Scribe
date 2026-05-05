from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "service"
DIST_ROOT = REPO_ROOT / "dist"
BUILD_ROOT = REPO_ROOT / "build"


def repo_python() -> Path:
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if python.exists():
        return python
    return Path(sys.executable)


def run(command: Iterable[str | Path], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> None:
    command = [str(item) for item in command]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True, creationflags=subprocess_creationflags())


def run_capture(command: Iterable[str | Path], *, cwd: Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess_creationflags(),
    )
    return completed.stdout


def read_app_version() -> str:
    version_path = SERVICE_ROOT / "app" / "version.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_path.read_text(encoding="utf-8"))
    if not match:
        raise RuntimeError(f"Could not read app version from {version_path}")
    return match.group(1)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def copytree_clean(source: Path, target: Path) -> None:
    remove_path(target)
    shutil.copytree(source, target)


def windows_creationflags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def subprocess_creationflags() -> int:
    if os.getenv("GV_HIDE_SUBPROCESS_WINDOWS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return windows_creationflags()
    return 0
