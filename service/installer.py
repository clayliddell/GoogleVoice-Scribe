from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import Tk, messagebox
from tkinter import ttk

from install_common import (
    APP_NAME,
    APP_VERSION,
    bundled_payload_dir,
    compare_versions,
    config_file_path,
    copy_payload,
    create_shortcuts,
    default_install_dir,
    install_dependencies,
    read_installed_version,
    stop_managed_server,
    write_config,
    write_install_metadata,
)


class InstallerApp:
    def __init__(self, *, install_dir: Path, launch_after_install: bool) -> None:
        self.install_dir = install_dir
        self.launch_after_install = launch_after_install
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.root = Tk()
        self.root.title(f"{APP_NAME} Setup")
        self.root.geometry("580x320")
        self.root.resizable(False, False)
        self.progress_value = 0
        self.status_label = ttk.Label(self.root, text="Preparing installer...", font=("Segoe UI", 11, "bold"))
        self.detail_label = ttk.Label(self.root, text="", foreground="#475569", wraplength=520)
        self.progress = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.close_button = ttk.Button(self.root, text="Close", command=self.root.destroy, state="disabled")
        self.build()

    def build(self) -> None:
        frame = ttk.Frame(self.root, padding=22)
        frame.pack(fill="both", expand=True)
        title = ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")
        subtitle = ttk.Label(frame, text=f"Installer v{APP_VERSION}", foreground="#64748b")
        subtitle.pack(anchor="w", pady=(0, 18))
        self.status_label.pack(in_=frame, anchor="w")
        self.detail_label.pack(in_=frame, anchor="w", pady=(6, 14))
        self.progress.pack(in_=frame, fill="x")
        self.close_button.pack(in_=frame, anchor="e", pady=(24, 0))

    def run(self) -> None:
        self.root.after(200, self.start_worker)
        self.root.after(150, self.poll_events)
        self.root.mainloop()

    def start_worker(self) -> None:
        threading.Thread(target=self.install_worker, daemon=True).start()

    def install_worker(self) -> None:
        try:
            self.install()
            self.events.put(("done", None))
        except Exception as error:
            self.events.put(("failed", str(error)))

    def install(self) -> None:
        payload_dir = bundled_payload_dir()
        if not payload_dir.exists():
            raise RuntimeError(f"Installer payload is missing: {payload_dir}")

        installed_version = read_installed_version(self.install_dir)
        if installed_version and compare_versions(installed_version, APP_VERSION) > 0:
            raise RuntimeError(
                f"A newer version is already installed ({installed_version}). "
                f"This installer is v{APP_VERSION}."
            )

        action = "Updating" if installed_version else "Installing"
        if installed_version and compare_versions(installed_version, APP_VERSION) == 0:
            action = "Repairing"

        self.step(10, f"{action} application files...", str(self.install_dir))
        stop_managed_server()
        copy_payload(payload_dir, self.install_dir)

        self.step(35, "Writing default configuration...", str(config_file_path()))
        if not config_file_path().exists():
            write_config({})

        self.step(50, "Installing runtime dependencies...", "This can take several minutes the first time.")
        install_dependencies(self.install_dir, report=lambda message: self.events.put(("detail", message)))

        self.step(85, "Creating shortcuts...", "Start Menu and Desktop shortcuts are being refreshed.")
        create_shortcuts(self.install_dir)

        self.step(95, "Saving install metadata...", "")
        write_install_metadata(self.install_dir, version=APP_VERSION)

        self.step(100, "Installation complete.", "Launching GoogleVoice Scribe.")
        if self.launch_after_install:
            self.launch_control_app()

    def launch_control_app(self) -> None:
        control_app = self.install_dir / "GoogleVoiceScribe.exe"
        if control_app.exists():
            subprocess.Popen([str(control_app)], cwd=self.install_dir)

    def step(self, percent: int, status: str, detail: str) -> None:
        self.events.put(("progress", percent))
        self.events.put(("status", status))
        self.events.put(("detail", detail))

    def poll_events(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.progress.configure(value=int(value))
            elif kind == "status":
                self.status_label.configure(text=str(value))
            elif kind == "detail":
                self.detail_label.configure(text=str(value))
            elif kind == "done":
                self.progress.configure(value=100)
                self.close_button.configure(state="normal")
            elif kind == "failed":
                self.close_button.configure(state="normal")
                self.status_label.configure(text="Installation failed.")
                self.detail_label.configure(text=str(value))
                messagebox.showerror(f"{APP_NAME} Setup", str(value))
        self.root.after(150, self.poll_events)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"Install {APP_NAME}.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} Setup {APP_VERSION}")
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--no-launch", action="store_true", help="Do not launch the control app after install.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    InstallerApp(install_dir=args.install_dir, launch_after_install=not args.no_launch).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
