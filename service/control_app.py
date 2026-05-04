from __future__ import annotations

import queue
import threading
import time
from tkinter import BooleanVar, Canvas, Tk, messagebox
from tkinter import ttk

from install_common import (
    APP_NAME,
    CONFIG_OPTIONS,
    apply_tk_icon,
    config_file_path,
    configured_listener_pid,
    default_install_dir,
    dependencies_ready,
    install_dependencies,
    is_process_running,
    adopt_managed_server_pid,
    managed_server_pid,
    read_config,
    server_health_payload,
    set_windows_app_user_model_id,
    start_server,
    stop_managed_server,
    write_config,
)


STATE_STYLES = {
    "Checking": ("#64748b", "Checking"),
    "Stopped": ("#dc2626", "Stopped"),
    "Starting": ("#d97706", "Starting"),
    "Running": ("#16a34a", "Running"),
    "Stopping": ("#d97706", "Stopping"),
    "Error": ("#dc2626", "Error"),
}


class ControlApp:
    def __init__(self) -> None:
        set_windows_app_user_model_id()
        self.root = Tk()
        self.root.title(APP_NAME)
        apply_tk_icon(self.root)
        self.root.geometry("560x450")
        self.root.minsize(520, 420)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.install_dir = default_install_dir()
        self.config_vars: dict[str, BooleanVar] = {}
        self.server_state = "Checking"
        self.operation_active = False
        self.state_dot: Canvas | None = None
        self.state_label: ttk.Label | None = None
        self.status_var: ttk.Label | None = None
        self.detail_var: ttk.Label | None = None
        self.progress: ttk.Progressbar | None = None
        self.start_button: ttk.Button | None = None
        self.stop_button: ttk.Button | None = None
        self.save_button: ttk.Button | None = None
        self.build()
        self.load_config()
        self.root.after(200, self.poll_events)
        self.root.after(500, self.refresh_status)
        self.root.after(500, lambda: apply_tk_icon(self.root))

    def build(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w")

        status_panel = ttk.Frame(frame, relief="groove", padding=(12, 10))
        status_panel.pack(fill="x", pady=(12, 14))

        state_row = ttk.Frame(status_panel)
        state_row.pack(fill="x")
        self.state_dot = Canvas(state_row, width=14, height=14, highlightthickness=0)
        self.state_dot.pack(side="left", padx=(0, 8))
        self.state_label = ttk.Label(state_row, text="Server: Checking", font=("Segoe UI", 11, "bold"))
        self.state_label.pack(side="left")

        self.status_var = ttk.Label(status_panel, text="Checking server status...")
        self.status_var.pack(anchor="w", pady=(8, 2))
        self.detail_var = ttk.Label(status_panel, text="", foreground="#475569", wraplength=500)
        self.detail_var.pack(anchor="w")
        self.progress = ttk.Progressbar(status_panel, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 0))
        self.progress.stop()
        self.set_server_state("Checking")

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
        self.set_server_state("Starting")
        self.set_status("Starting server...", "Checking runtime and dependencies.")
        threading.Thread(target=self.start_server_worker, daemon=True).start()

    def start_server_worker(self) -> None:
        try:
            healthy, detail = server_health()
            if healthy:
                pid = configured_listener_pid()
                if pid:
                    adopt_managed_server_pid(pid)
                    self.events.put(("state", "Running"))
                    self.events.put(("status", f"Server is already running. PID {pid}"))
                else:
                    self.events.put(("state", "Running"))
                    self.events.put(("status", "Server is already running."))
                self.events.put(("detail", detail))
                return

            listener_pid = configured_listener_pid()
            if listener_pid:
                raise RuntimeError(
                    f"Port {configured_port_label()} is already in use by PID {listener_pid}, "
                    "but it did not respond as GoogleVoice Scribe."
                )

            pid = managed_server_pid()
            if pid:
                self.events.put(("state", "Running"))
                self.events.put(("status", f"Server is already running. PID {pid}"))
                self.events.put(("detail", ""))
                return
            if not dependencies_ready(self.install_dir):
                install_dependencies(self.install_dir, report=lambda message: self.events.put(("detail", message)))
            pid = start_server(self.install_dir)
            self.events.put(("status", f"Server started. PID {pid}"))
            self.wait_for_health(pid)
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("busy", "0"))

    def stop_server_clicked(self) -> None:
        self.set_busy(True)
        self.set_server_state("Stopping")
        self.set_status("Stopping server...", "")
        threading.Thread(target=self.stop_server_worker, daemon=True).start()

    def stop_server_worker(self) -> None:
        try:
            stop_managed_server()
            for _attempt in range(20):
                if not server_health()[0]:
                    break
                time.sleep(0.25)
            self.events.put(("state", "Stopped"))
            self.events.put(("status", "Server stopped."))
            self.events.put(("detail", ""))
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("busy", "0"))

    def wait_for_health(self, pid: int | None = None) -> None:
        for _attempt in range(40):
            healthy, detail = server_health()
            if healthy:
                listener_pid = configured_listener_pid()
                if listener_pid:
                    adopt_managed_server_pid(listener_pid)
                self.events.put(("state", "Running"))
                self.events.put(("status", f"Server is healthy. PID {listener_pid or pid}" if (listener_pid or pid) else "Server is healthy."))
                self.events.put(("detail", detail))
                return
            if pid and not is_process_running(pid):
                raise RuntimeError(
                    "Server process exited before it became healthy. "
                    f"Recent log: {recent_server_error_log()}"
                )
            time.sleep(0.5)
        self.events.put(("detail", "Server process started, but health check did not respond yet."))

    def poll_events(self) -> None:
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.set_status_text(message)
            elif kind == "detail":
                self.set_detail_text(message)
            elif kind == "state":
                self.set_server_state(message)
            elif kind == "busy":
                self.set_busy(message == "1")
            elif kind == "error":
                self.set_server_state("Error")
                self.set_status("Error", message)
                messagebox.showerror(APP_NAME, message)
        self.root.after(200, self.poll_events)

    def refresh_status(self) -> None:
        if self.operation_active:
            self.root.after(1000, self.refresh_status)
            return

        pid = managed_server_pid()
        if pid and is_process_running(pid):
            healthy, detail = server_health()
            if healthy:
                self.set_server_state("Running")
                self.set_status(f"Server is running. PID {pid}", detail)
            else:
                self.set_server_state("Starting")
                self.set_status(f"Server process is running. PID {pid}", "Waiting for health check.")
        elif server_health()[0]:
            listener_pid = configured_listener_pid()
            if listener_pid:
                adopt_managed_server_pid(listener_pid)
                self.set_server_state("Running")
                self.set_status(f"Server is running. PID {listener_pid}", "Adopted existing server on configured port.")
            else:
                self.set_server_state("Running")
                self.set_status("Server is running.", "Health check responded, but listener PID could not be resolved.")
        elif configured_listener_pid():
            listener_pid = configured_listener_pid()
            self.set_server_state("Error")
            self.set_status(
                f"Port {configured_port_label()} is in use by PID {listener_pid}.",
                "That process did not respond as GoogleVoice Scribe.",
            )
        else:
            self.set_server_state("Stopped")
            self.set_status("Server is stopped.", "")
        self.root.after(5000, self.refresh_status)

    def set_status(self, status: str, detail: str) -> None:
        self.set_status_text(status)
        self.set_detail_text(detail)

    def set_status_text(self, status: str) -> None:
        if self.status_var:
            self.status_var.configure(text=status)

    def set_detail_text(self, detail: str) -> None:
        if self.detail_var:
            self.detail_var.configure(text=detail)

    def set_server_state(self, state: str) -> None:
        color, label = STATE_STYLES.get(state, STATE_STYLES["Checking"])
        self.server_state = label
        if self.state_label:
            self.state_label.configure(text=f"Server: {label}")
        if self.state_dot:
            self.state_dot.delete("all")
            self.state_dot.create_oval(2, 2, 12, 12, fill=color, outline=color)

    def set_busy(self, busy: bool) -> None:
        self.operation_active = busy
        for button in (self.start_button, self.stop_button, self.save_button):
            if button:
                button.configure(state="disabled" if busy else "normal")
        if busy:
            if self.progress:
                self.progress.start(12)
        elif self.progress:
            self.progress.stop()

    def run(self) -> None:
        self.root.mainloop()


def server_health() -> tuple[bool, str]:
    payload = server_health_payload()
    if payload is None:
        return False, ""

    version = payload.get("version", "unknown")
    recordings_dir = payload.get("recordings_dir", "")
    return True, f"Version {version}. Recordings: {recordings_dir}"


def configured_port_label() -> str:
    config = read_config()
    port = config.get("GV_SERVICE_PORT", "8765").strip() or "8765"
    return port


def recent_server_error_log() -> str:
    from install_common import log_dir

    path = log_dir() / "server.err.log"
    if not path.exists():
        return "server.err.log does not exist."
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return str(error)
    return " | ".join(lines[-8:]) or "server.err.log is empty."


def main() -> None:
    ControlApp().run()


if __name__ == "__main__":
    main()
