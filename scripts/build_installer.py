from __future__ import annotations

import shutil
from pathlib import Path

from script_common import BUILD_ROOT, DIST_ROOT, REPO_ROOT, read_app_version, remove_path, run


APP_ICON_SOURCE = REPO_ROOT / "GoogleVoiceScribe.png"
APP_ICON_ICO_NAME = "GoogleVoiceScribe.ico"


def main() -> int:
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise SystemExit("Missing .venv. Create it first with: py -3.12 -m venv .venv")

    version = read_app_version()
    pyinstaller_root = BUILD_ROOT / "pyinstaller"
    payload_root = BUILD_ROOT / "installer-payload" / "payload"
    icon_path = prepare_icon(python)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    pyinstaller_root.mkdir(parents=True, exist_ok=True)

    run([python, "-m", "pip", "install", "pyinstaller>=6.11", "pywin32>=306", "pillow>=10"])

    control_dist = pyinstaller_root / "control-dist"
    control_work = pyinstaller_root / "control-work"
    installer_work = pyinstaller_root / "installer-work"
    spec_dir = pyinstaller_root / "spec"

    run(
        [
            python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            "GoogleVoiceScribe",
            "--distpath",
            control_dist,
            "--workpath",
            control_work,
            "--specpath",
            spec_dir,
            "--icon",
            icon_path,
            "--add-data",
            f"{APP_ICON_SOURCE};.",
            "--add-data",
            f"{icon_path};.",
            "--hidden-import",
            "win32com.client",
            "--hidden-import",
            "pythoncom",
            REPO_ROOT / "service" / "control_app.py",
        ]
    )

    control_exe = control_dist / "GoogleVoiceScribe.exe"
    if not control_exe.exists():
        raise SystemExit(f"PyInstaller did not create {control_exe}")

    stage_payload(control_exe, payload_root, version)
    payload_arg = f"{payload_root};payload"
    run(
        [
            python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            f"GoogleVoiceScribeSetup-v{version}-win-x64",
            "--distpath",
            DIST_ROOT,
            "--workpath",
            installer_work,
            "--specpath",
            spec_dir,
            "--add-data",
            payload_arg,
            "--add-data",
            f"{APP_ICON_SOURCE};.",
            "--add-data",
            f"{icon_path};.",
            "--icon",
            icon_path,
            "--hidden-import",
            "win32com.client",
            "--hidden-import",
            "pythoncom",
            REPO_ROOT / "service" / "installer.py",
        ]
    )

    installer_path = DIST_ROOT / f"GoogleVoiceScribeSetup-v{version}-win-x64.exe"
    if not installer_path.exists():
        raise SystemExit(f"PyInstaller did not create {installer_path}")
    if installer_path.stat().st_size >= 2 * 1024 * 1024 * 1024:
        raise SystemExit(f"Release asset exceeds GitHub's 2 GiB per-file limit: {installer_path}")
    print(f"Built {installer_path}")
    return 0


def prepare_icon(python: Path) -> Path:
    if not APP_ICON_SOURCE.exists():
        raise SystemExit(f"Missing app icon: {APP_ICON_SOURCE}")
    icon_path = BUILD_ROOT / "assets" / APP_ICON_ICO_NAME
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "import sys; "
        "from pathlib import Path; "
        "from PIL import Image; "
        "source = Path(sys.argv[1]); target = Path(sys.argv[2]); "
        "image = Image.open(source).convert('RGBA'); "
        "image.save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])"
    )
    run([python, "-c", script, APP_ICON_SOURCE, icon_path])
    return icon_path


def stage_payload(control_exe: Path, payload_root: Path, version: str) -> None:
    remove_path(payload_root)
    payload_root.mkdir(parents=True, exist_ok=True)

    shutil.copy2(control_exe, payload_root / "GoogleVoiceScribe.exe")
    shutil.copytree(REPO_ROOT / "service", payload_root / "service")
    shutil.copytree(REPO_ROOT / "scripts", payload_root / "scripts")
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".env.example"):
        shutil.copy2(REPO_ROOT / name, payload_root / name)
    shutil.copy2(APP_ICON_SOURCE, payload_root / APP_ICON_SOURCE.name)
    shutil.copy2(BUILD_ROOT / "assets" / APP_ICON_ICO_NAME, payload_root / APP_ICON_ICO_NAME)

    crx = DIST_ROOT / f"GoogleVoiceScribeExtension-v{version}.crx"
    if crx.exists():
        extension_dir = payload_root / "extension"
        extension_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(crx, extension_dir / crx.name)

    for pycache in payload_root.rglob("__pycache__"):
        shutil.rmtree(pycache)
    for pattern in ("*.pyc", "*.pyo"):
        for path in payload_root.rglob(pattern):
            path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
