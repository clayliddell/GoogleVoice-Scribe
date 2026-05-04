from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_VERSION = "0.1.0"


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the GoogleVoice Scribe local server.")
    parser.add_argument("--install-only", action="store_true", help="Create/update the runtime environment and exit.")
    parser.add_argument("--no-install", action="store_true", help="Do not create or update the runtime environment.")
    args, service_args = parser.parse_known_args()

    root = app_root()
    service_dir = root / "service"
    scripts_dir = root / "scripts"
    venv_python = root / ".venv" / "Scripts" / "python.exe"

    if not service_dir.is_dir():
        print(f"Missing service directory beside launcher: {service_dir}", file=sys.stderr)
        return 2

    if not venv_python.exists():
        if args.no_install:
            print(f"Missing runtime Python environment: {venv_python}", file=sys.stderr)
            return 2
        create_venv(root / ".venv")

    if not args.no_install and not dependencies_ready(venv_python):
        install_script = scripts_dir / "install-service-deps.ps1"
        if not install_script.exists():
            print(f"Missing dependency installer: {install_script}", file=sys.stderr)
            return 2
        run(["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(install_script)], cwd=root)

    if args.install_only:
        return 0

    env = os.environ.copy()
    env["PYTHONPATH"] = str(service_dir)
    return subprocess.call([str(venv_python), "-m", "app.cli", *service_args], cwd=service_dir, env=env)


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def create_venv(venv_dir: Path) -> None:
    launcher = shutil.which("py")
    if launcher:
        run([launcher, "-3.12", "-m", "venv", str(venv_dir)], cwd=venv_dir.parent)
        return

    python = shutil.which("python")
    if not python:
        raise SystemExit("Python 3.12 is required but was not found on PATH.")
    run([python, "-m", "venv", str(venv_dir)], cwd=venv_dir.parent)


def dependencies_ready(venv_python: Path) -> bool:
    check = (
        "import fastapi, uvicorn, numpy, torch, torchaudio, transformers, huggingface_hub; "
        "import llama_cpp; "
        "print('dependencies ready')"
    )
    completed = subprocess.run(
        [str(venv_python), "-c", check],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def run(command: list[str], *, cwd: Path) -> None:
    print(" ".join(command), flush=True)
    subprocess.check_call(command, cwd=cwd)


if __name__ == "__main__":
    raise SystemExit(main())
