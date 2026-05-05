from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


APP_NAME = "GoogleVoice Scribe"
APP_DIR_NAME = "GoogleVoiceScribe"
APP_ICON_PNG_NAME = "GoogleVoiceScribe.png"
APP_ICON_ICO_NAME = "GoogleVoiceScribe.ico"
APP_USER_MODEL_ID = "GoogleVoiceScribe.GoogleVoiceScribe"
APP_VERSION = "0.2.1"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = "8765"
GRANITE_SPEECH_PLUS_MODULE = "transformers.models.granite_speech_plus"

CONFIG_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("GV_TRANSCRIBE", "Transcribe calls", "1"),
    ("GV_COMPRESS_AUDIO", "Create compressed audio.opus", "1"),
    ("GV_KEEP_WAV_FILES", "Keep large WAV files in transcript folders", "0"),
    ("GV_INCREMENTAL_REFERENCE_TRANSCRIPTION", "Use 3-track incremental speaker references", "1"),
    ("GV_WARM_GRANITE_ON_CALL_START", "Warm Granite when a call starts", "1"),
    ("GV_FORCE_CPU", "Force CPU mode (disable GPU acceleration)", "0"),
    ("GV_HF_LOCAL_FILES_ONLY", "Strict offline mode after models are cached", "0"),
    ("GV_KEEP_TITLE_MODEL", "Keep title model warm", "1"),
)

CONFIG_DEFAULTS: dict[str, str] = {
    "GV_SERVICE_HOST": SERVER_HOST,
    "GV_SERVICE_PORT": SERVER_PORT,
    "GV_TRANSCRIBE": "1",
    "GV_COMPRESS_AUDIO": "1",
    "GV_KEEP_WAV_FILES": "0",
    "GV_INCREMENTAL_REFERENCE_TRANSCRIPTION": "1",
    "GV_WARM_GRANITE_ON_CALL_START": "1",
    "GV_FORCE_CPU": "0",
    "GV_HF_LOCAL_FILES_ONLY": "0",
    "GV_KEEP_TITLE_MODEL": "1",
}

CREATE_NO_WINDOW = 0x08000000


def default_install_dir() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_DIR_NAME
    return Path.home() / "AppData" / "Local" / APP_DIR_NAME


def config_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME


def config_file_path() -> Path:
    return config_dir() / "config.env"


def install_metadata_path(install_dir: Path | None = None) -> Path:
    return (install_dir or default_install_dir()) / "install.json"


def pid_file_path() -> Path:
    return config_dir() / "server.pid"


def log_dir() -> Path:
    return config_dir() / "logs"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_payload_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "payload"
    return app_root()


def app_asset_path(name: str) -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / name)
        candidates.append(Path(meipass) / "payload" / name)
    candidates.append(app_root() / name)
    candidates.append(default_install_dir() / name)

    for path in candidates:
        if path.exists():
            return path
    return None


def apply_tk_icon(root: object) -> None:
    ico_path = app_asset_path(APP_ICON_ICO_NAME)
    if ico_path is not None and os.name == "nt":
        try:
            root.iconbitmap(default=str(ico_path))  # type: ignore[attr-defined]
        except Exception:
            pass

    png_path = app_asset_path(APP_ICON_PNG_NAME)
    if png_path is not None:
        try:
            from tkinter import PhotoImage

            icon = PhotoImage(file=str(png_path))
            root.iconphoto(True, icon)  # type: ignore[attr-defined]
            setattr(root, "_googlevoice_scribe_icon", icon)
        except Exception:
            pass

    if ico_path is not None and os.name == "nt":
        try:
            apply_windows_window_icon(root, ico_path)
        except Exception:
            pass


def set_windows_app_user_model_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        return


def apply_windows_window_icon(root: object, icon_path: Path) -> None:
    if os.name != "nt":
        return
    hwnd = int(root.winfo_id())  # type: ignore[attr-defined]
    if not hwnd:
        return

    user32 = ctypes.windll.user32
    load_image = user32.LoadImageW
    load_image.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    load_image.restype = ctypes.c_void_p
    send_message = user32.SendMessageW
    send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    send_message.restype = ctypes.c_void_p

    image_icon = 1
    lr_load_from_file = 0x00000010
    wm_seticon = 0x0080
    icon_small = 0
    icon_big = 1
    icon_small2 = 2

    large_icon = load_image(None, str(icon_path), image_icon, 32, 32, lr_load_from_file)
    small_icon = load_image(None, str(icon_path), image_icon, 16, 16, lr_load_from_file)
    if large_icon:
        send_message(hwnd, wm_seticon, icon_big, large_icon)
    if small_icon:
        send_message(hwnd, wm_seticon, icon_small, small_icon)
        send_message(hwnd, wm_seticon, icon_small2, small_icon)
    setattr(root, "_googlevoice_scribe_icon_handles", (large_icon, small_icon))


def read_config(path: Path | None = None) -> dict[str, str]:
    path = path or config_file_path()
    values = dict(CONFIG_DEFAULTS)
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = unquote(value.strip())
    return values


def write_config(values: dict[str, str], path: Path | None = None) -> None:
    path = path or config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(CONFIG_DEFAULTS)
    merged.update(values)
    lines = [
        "# GoogleVoice Scribe configuration",
        "# Environment variables with the same names override these values.",
    ]
    for key in sorted(merged):
        lines.append(f"{key}={quote_if_needed(str(merged[key]))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def server_environment() -> dict[str, str]:
    env = os.environ.copy()
    config_path = config_file_path()
    if not config_path.exists():
        write_config({})
    for key, value in read_config(config_path).items():
        env.setdefault(key, value)
    env["GV_CONFIG_FILE"] = str(config_path)
    return env


def server_health_url() -> str:
    config = read_config()
    host = config.get("GV_SERVICE_HOST", SERVER_HOST).strip() or SERVER_HOST
    port = config.get("GV_SERVICE_PORT", SERVER_PORT).strip() or SERVER_PORT
    return f"http://{host}:{port}/health"


def server_health_payload(timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(server_health_url(), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if payload.get("ok") is True and payload.get("model_name"):
        return payload
    return None


def quote_if_needed(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].replace('\\"', '"')
    return value


def compare_versions(left: str, right: str) -> int:
    left_parts = version_parts(left)
    right_parts = version_parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (width - len(left_parts)))
    right_parts.extend([0] * (width - len(right_parts)))
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def version_parts(value: str) -> list[int]:
    parts: list[int] = []
    for item in value.strip().lstrip("v").split("."):
        digits = "".join(char for char in item if char.isdigit())
        parts.append(int(digits or "0"))
    return parts or [0]


def read_installed_version(install_dir: Path) -> str | None:
    metadata_path = install_metadata_path(install_dir)
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    version = payload.get("version")
    return str(version) if version else None


def write_install_metadata(install_dir: Path, *, version: str = APP_VERSION) -> None:
    payload = {
        "name": APP_NAME,
        "version": version,
        "install_dir": str(install_dir),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    install_dir.mkdir(parents=True, exist_ok=True)
    install_metadata_path(install_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def managed_server_pid() -> int | None:
    path = pid_file_path()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if is_process_running(pid) else None


def adopt_managed_server_pid(pid: int) -> None:
    if pid <= 0:
        return
    config_dir().mkdir(parents=True, exist_ok=True)
    pid_file_path().write_text(str(pid), encoding="utf-8")


def configured_port() -> int:
    config = read_config()
    raw_port = config.get("GV_SERVICE_PORT", SERVER_PORT).strip() or SERVER_PORT
    try:
        return int(raw_port)
    except ValueError:
        return int(SERVER_PORT)


def configured_listener_pid() -> int | None:
    return listener_pid_for_port(configured_port())


def listener_pid_for_port(port: int) -> int | None:
    completed = subprocess.run(
        ["netstat.exe", "-ano", "-p", "tcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        return None

    suffix = f":{port}"
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split()
        if len(parts) < 5:
            continue
        local_address, state, pid_text = parts[1], parts[3].upper(), parts[-1]
        if state != "LISTENING":
            continue
        if not local_address.endswith(suffix):
            continue
        try:
            return int(pid_text)
        except ValueError:
            continue
    return None


def stop_process_tree(pid: int) -> None:
    if pid <= 0 or not is_process_running(pid):
        return
    subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )


def stop_managed_server() -> None:
    pid = managed_server_pid()
    if pid:
        stop_process_tree(pid)

    # If the PID file is stale but a healthy GoogleVoice Scribe server is still
    # bound to the configured port, stop that process too. This handles orphaned
    # servers left behind by earlier builds or manual development runs.
    if server_health_payload() is not None:
        listener_pid = configured_listener_pid()
        if listener_pid and listener_pid != pid:
            stop_process_tree(listener_pid)

    try:
        pid_file_path().unlink(missing_ok=True)
    except Exception:
        pass


def venv_python(install_dir: Path) -> Path:
    return install_dir / ".venv" / "Scripts" / "python.exe"


def dependencies_ready(install_dir: Path) -> bool:
    python = venv_python(install_dir)
    if not python.exists():
        return False
    check = (
        "import os, sys; "
        "import importlib.util; "
        "torch_lib = os.path.join(sys.prefix, 'Lib', 'site-packages', 'torch', 'lib'); "
        "os.add_dll_directory(torch_lib) if os.path.isdir(torch_lib) else None; "
        "import fastapi, uvicorn, numpy, torch, torchaudio, torchvision, transformers, huggingface_hub; "
        "import llama_cpp; "
        "assert torch.__version__.startswith('2.6.0+cu124'), torch.__version__; "
        "assert torchaudio.__version__.startswith('2.6.0+cu124'), torchaudio.__version__; "
        "assert torchvision.__version__.startswith('0.21.0+cu124'), torchvision.__version__; "
        f"assert importlib.util.find_spec({GRANITE_SPEECH_PLUS_MODULE!r}) is not None, transformers.__version__"
    )
    completed = subprocess.run(
        [str(python), "-c", check],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def ensure_venv(install_dir: Path, report: Callable[[str], None] | None = None) -> None:
    python = venv_python(install_dir)
    reporter = report or (lambda _message: None)
    if python.exists():
        reporter("Using existing Python runtime environment.")
        return
    reporter("Creating Python 3.12 runtime environment...")
    launcher = shutil.which("py")
    if launcher:
        run_streamed([launcher, "-3.12", "-m", "venv", str(install_dir / ".venv")], cwd=install_dir, report=reporter)
        return
    python_exe = shutil.which("python")
    if not python_exe:
        raise RuntimeError("Python 3.12 is required but was not found on PATH.")
    run_streamed([python_exe, "-m", "venv", str(install_dir / ".venv")], cwd=install_dir, report=reporter)


def install_dependencies(install_dir: Path, report: Callable[[str], None] | None = None) -> None:
    reporter = report or (lambda _message: None)
    ensure_venv(install_dir, report=report)
    if dependencies_ready(install_dir):
        reporter("Server dependencies are already installed.")
        return
    script = install_dir / "scripts" / "install_service_deps.py"
    if not script.exists():
        raise RuntimeError(f"Missing dependency installer: {script}")
    reporter("Installing server dependencies. This can take a while on first install...")
    run_streamed([str(venv_python(install_dir)), str(script)], cwd=install_dir, report=reporter)


def start_server(install_dir: Path) -> int:
    stop_managed_server()
    config_dir().mkdir(parents=True, exist_ok=True)
    log_dir().mkdir(parents=True, exist_ok=True)
    python = venv_python(install_dir)
    if not python.exists():
        raise RuntimeError("Server runtime is not installed yet.")

    env = server_environment()
    env["PYTHONPATH"] = str(install_dir / "service")
    stdout = (log_dir() / "server.out.log").open("ab")
    stderr = (log_dir() / "server.err.log").open("ab")
    process = subprocess.Popen(
        [str(python), "-m", "app.cli"],
        cwd=install_dir / "service",
        env=env,
        stdout=stdout,
        stderr=stderr,
        creationflags=CREATE_NO_WINDOW,
    )
    pid_file_path().write_text(str(process.pid), encoding="utf-8")
    return process.pid


def run_streamed(command: list[str], *, cwd: Path, report: Callable[[str], None]) -> None:
    env = os.environ.copy()
    env["GV_HIDE_SUBPROCESS_WINDOWS"] = "1"
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=CREATE_NO_WINDOW,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if line:
            report(line)
    exit_code = process.wait()
    if exit_code != 0:
        raise RuntimeError(f"Command failed with exit code {exit_code}: {' '.join(command)}")


def copy_payload(payload_dir: Path, install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    preserve = {".venv", "install.json"}
    for item in install_dir.iterdir():
        if item.name in preserve:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink(missing_ok=True)

    for item in payload_dir.iterdir():
        target = install_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def create_shortcuts(install_dir: Path) -> None:
    target = install_dir / "GoogleVoiceScribe.exe"
    if not target.exists():
        return

    start_menu = Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path(os.getenv("USERPROFILE", str(Path.home()))) / "Desktop"
    for shortcut_path in (start_menu / "GoogleVoice Scribe.lnk", desktop / "GoogleVoice Scribe.lnk"):
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
        create_shortcut(shortcut_path, target, install_dir)


def create_shortcut(shortcut_path: Path, target: Path, working_dir: Path) -> None:
    try:
        import pythoncom
        from win32com.client import Dispatch

        pythoncom.CoInitialize()
        shell = Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(target)
        shortcut.WorkingDirectory = str(working_dir)
        shortcut.IconLocation = str(target)
        shortcut.Save()
    except Exception:
        pass
    finally:
        try:
            pythoncom.CoUninitialize()  # type: ignore[name-defined]
        except Exception:
            pass
