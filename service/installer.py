from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import DISABLED, END, NORMAL, Tk, messagebox
from tkinter.scrolledtext import ScrolledText
from tkinter import ttk

from install_common import (
    APP_NAME,
    APP_VERSION,
    apply_tk_icon,
    bundled_payload_dir,
    compare_versions,
    config_file_path,
    copy_payload,
    create_shortcuts,
    default_install_dir,
    install_dependencies,
    read_installed_version,
    set_windows_app_user_model_id,
    stop_managed_server,
    write_config,
    write_install_metadata,
)


class InstallerApp:
    def __init__(self, *, install_dir: Path, launch_after_install: bool) -> None:
        self.install_dir = install_dir
        self.launch_after_install = launch_after_install
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        set_windows_app_user_model_id()
        self.root = Tk()
        self.root.title(f"{APP_NAME} Setup")
        apply_tk_icon(self.root)
        self.root.geometry("680x500")
        self.root.resizable(False, False)
        self.progress_value = 0
        self.build()

    def build(self) -> None:
        frame = ttk.Frame(self.root, padding=22)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")
        subtitle = ttk.Label(frame, text=f"Installer v{APP_VERSION}", foreground="#64748b")
        subtitle.pack(anchor="w", pady=(0, 16))

        self.status_label = ttk.Label(frame, text="Preparing installer...", font=("Segoe UI", 11, "bold"))
        self.status_label.pack(anchor="w")
        self.detail_label = ttk.Label(frame, text="Starting setup...", foreground="#475569", wraplength=620)
        self.detail_label.pack(anchor="w", pady=(6, 12))

        progress_row = ttk.Frame(frame)
        progress_row.pack(fill="x", pady=(0, 16))
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.percent_label = ttk.Label(progress_row, text="0%", width=5, anchor="e")
        self.percent_label.pack(side="left", padx=(10, 0))

        log_label = ttk.Label(frame, text="Installation log", font=("Segoe UI", 10, "bold"))
        log_label.pack(anchor="w")
        self.log_text = ScrolledText(
            frame,
            height=12,
            wrap="word",
            state=DISABLED,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, pady=(6, 14))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x")
        self.close_button = ttk.Button(button_row, text="Close", command=self.root.destroy, state="disabled")
        self.close_button.pack(side="right")

    def run(self) -> None:
        self.root.after(200, self.start_worker)
        self.root.after(150, self.poll_events)
        self.root.after(500, lambda: apply_tk_icon(self.root))
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
        self.log("Application files copied.")

        self.step(35, "Writing default configuration...", str(config_file_path()))
        if not config_file_path().exists():
            write_config({})
            self.log("Default configuration file created.")
        else:
            self.log("Existing configuration file preserved.")

        self.step(50, "Installing runtime dependencies...", "This can take several minutes the first time.")
        install_dependencies(self.install_dir, report=self.report_dependency_progress)

        self.step(85, "Creating shortcuts...", "Start Menu and Desktop shortcuts are being refreshed.")
        create_shortcuts(self.install_dir)
        self.log("Shortcuts refreshed.")

        self.step(95, "Saving install metadata...", "")
        write_install_metadata(self.install_dir, version=APP_VERSION)
        self.log("Install metadata saved.")

        self.step(100, "Installation complete.", "Launching GoogleVoice Scribe.")
        self.log("Installation complete.")
        if self.launch_after_install:
            self.launch_control_app()

    def launch_control_app(self) -> None:
        control_app = self.install_dir / "GoogleVoiceScribe.exe"
        if control_app.exists():
            subprocess.Popen([str(control_app)], cwd=self.install_dir)
            self.log("Control app launched.")

    def step(self, percent: int, status: str, detail: str) -> None:
        self.events.put(("progress", percent))
        self.events.put(("status", status))
        self.events.put(("detail", detail))
        self.events.put(("log", f"{status} {detail}".strip()))

    def log(self, message: str) -> None:
        self.events.put(("log", message))

    def report_dependency_progress(self, message: str) -> None:
        self.events.put(("detail", message))
        self.events.put(("log", message))

    def poll_events(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self.progress.configure(value=int(value))
                self.percent_label.configure(text=f"{int(value)}%")
            elif kind == "status":
                self.status_label.configure(text=str(value))
            elif kind == "detail":
                self.detail_label.configure(text=str(value))
            elif kind == "log":
                self.append_log(str(value))
            elif kind == "done":
                self.progress.configure(value=100)
                self.percent_label.configure(text="100%")
                self.close_button.configure(state="normal")
            elif kind == "failed":
                self.close_button.configure(state="normal")
                self.status_label.configure(text="Installation failed.")
                self.detail_label.configure(text=str(value))
                self.append_log(f"ERROR: {value}")
                messagebox.showerror(f"{APP_NAME} Setup", str(value))
        self.root.after(150, self.poll_events)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, f"[{timestamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)


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
