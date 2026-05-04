from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import BooleanVar, Tk, messagebox
from tkinter import ttk

from install_common import (
    APP_NAME,
    CONFIG_OPTIONS,
    config_file_path,
    default_install_dir,
    dependencies_ready,
    install_dependencies,
    is_process_running,
    managed_server_pid,
    read_config,
    start_server,
    stop_managed_server,
    write_config,
)


class ControlApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.geometry("560x450")
        self.root.minsize(520, 420)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.install_dir = default_install_dir()
        self.config_vars: dict[str, BooleanVar] = {}
        self.status_var = ttk.Label(self.root, text="Checking server status...")
        self.detail_var = ttk.Label(self.root, text="", foreground="#475569", wraplength=500)
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.start_button: ttk.Button | None = None
        self.stop_button: ttk.Button | None = None
        self.save_button: ttk.Button | None = None
        self.build()
        self.load_config()
        self.root.after(200, self.poll_events)
        self.root.after(500, self.refresh_status)

    def build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w")

        self.status_var.pack(in_=frame, anchor="w", pady=(12, 2))
        self.detail_var.pack(in_=frame, anchor="w", pady=(0, 10))
        self.progress.pack(in_=frame, fill="x", pady=(0, 14))
        self.progress.stop()

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 14))
        self.start_button = ttk.Button(controls, text="Start Server", command=self.start_server_clicked)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop Server", command=self.stop_server_clicked)
        self.stop_button.pack(side="left", padx=(10, 0))
        self.save_button = ttk.Button(controls, text="Save Config", command=self.save_config_clicked)
        self.save_button.pack(side="left", padx=(10, 0))

        options_frame = ttk.LabelFrame(frame, text="Server options", padding=12)
        options_frame.pack(fill="both", expand=True)

        for key, label, _default in CONFIG_OPTIONS:
            variable = BooleanVar(value=False)
            checkbox = ttk.Checkbutton(options_frame, text=label, variable=variable)
            checkbox.pack(anchor="w", pady=2)
            self.config_vars[key] = variable

        footer = ttk.Label(
            frame,
            text=f"Config: {config_file_path()}",
            foreground="#64748b",
            wraplength=500,
        )
        footer.pack(anchor="w", pady=(12, 0))

    def load_config(self) -> None:
        values = read_config()
        for key, _label, default in CONFIG_OPTIONS:
            value = values.get(key, default).strip().lower()
            self.config_vars[key].set(value in {"1", "true", "yes", "on"})

    def save_config_clicked(self) -> None:
        self.save_config()
        self.set_status("Config saved.", "Restart the server for changes to apply.")

    def save_config(self) -> None:
        values = read_config()
        for key, _label, _default in CONFIG_OPTIONS:
            values[key] = "1" if self.config_vars[key].get() else "0"
        write_config(values)

    def start_server_clicked(self) -> None:
        self.save_config()
        self.set_busy(True)
        self.set_status("Starting server...", "Checking runtime and dependencies.")
        threading.Thread(target=self.start_server_worker, daemon=True).start()

    def start_server_worker(self) -> None:
        try:
            pid = managed_server_pid()
            if pid:
                self.events.put(("status", f"Server is already running. PID {pid}"))
                self.events.put(("detail", ""))
                return
            if not dependencies_ready(self.install_dir):
                install_dependencies(self.install_dir, report=lambda message: self.events.put(("detail", message)))
            pid = start_server(self.install_dir)
            self.events.put(("status", f"Server started. PID {pid}"))
            self.wait_for_health()
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("busy", "0"))

    def stop_server_clicked(self) -> None:
        self.set_busy(True)
        self.set_status("Stopping server...", "")
        threading.Thread(target=self.stop_server_worker, daemon=True).start()

    def stop_server_worker(self) -> None:
        try:
            stop_managed_server()
            self.events.put(("status", "Server stopped."))
            self.events.put(("detail", ""))
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("busy", "0"))

    def wait_for_health(self) -> None:
        for _attempt in range(40):
            healthy, detail = server_health()
            if healthy:
                self.events.put(("status", "Server is healthy."))
                self.events.put(("detail", detail))
                return
            time.sleep(0.5)
        self.events.put(("detail", "Server process started, but health check did not respond yet."))

    def poll_events(self) -> None:
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.status_var.configure(text=message)
            elif kind == "detail":
                self.detail_var.configure(text=message)
            elif kind == "busy":
                self.set_busy(message == "1")
            elif kind == "error":
                self.set_status("Error", message)
                messagebox.showerror(APP_NAME, message)
        self.root.after(200, self.poll_events)

    def refresh_status(self) -> None:
        pid = managed_server_pid()
        if pid and is_process_running(pid):
            healthy, detail = server_health()
            if healthy:
                self.set_status(f"Server is running. PID {pid}", detail)
            else:
                self.set_status(f"Server process is running. PID {pid}", "Waiting for health check.")
        else:
            self.set_status("Server is stopped.", "")
        self.root.after(5000, self.refresh_status)

    def set_status(self, status: str, detail: str) -> None:
        self.status_var.configure(text=status)
        self.detail_var.configure(text=detail)

    def set_busy(self, busy: bool) -> None:
        for button in (self.start_button, self.stop_button, self.save_button):
            if button:
                button.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def run(self) -> None:
        self.root.mainloop()


def server_health() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(server_health_url(), timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False, ""

    version = payload.get("version", "unknown")
    recordings_dir = payload.get("recordings_dir", "")
    return True, f"Version {version}. Recordings: {recordings_dir}"


def server_health_url() -> str:
    config = read_config()
    host = config.get("GV_SERVICE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = config.get("GV_SERVICE_PORT", "8765").strip() or "8765"
    return f"http://{host}:{port}/health"


def main() -> None:
    ControlApp().run()


if __name__ == "__main__":
    main()
