from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from manager import __version__
from manager.install import (
    autostart_installed,
    ensure_install_layout,
    install_autostart,
    restart_service_process,
    start_service_process,
    stop_service_process,
    uninstall_autostart,
)
from manager.paths import env_path, install_dir, log_path
from manager.service import is_bridge_up, read_service_pid


class ManagerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("IdentBridge")
        self.geometry("360x300")
        self.resizable(False, False)
        self.configure(bg="#1e1f22")

        ensure_install_layout()

        pad = {"padx": 14, "pady": 4}
        title = tk.Label(
            self,
            text="IdentBridge",
            font=("Segoe UI", 16, "bold"),
            fg="#f2f3f5",
            bg="#1e1f22",
        )
        title.pack(pady=(14, 2))

        tk.Label(
            self,
            text=f"v{__version__}  ·  {install_dir()}",
            font=("Segoe UI", 8),
            fg="#949ba4",
            bg="#1e1f22",
        ).pack()

        self.status_var = tk.StringVar(value="Checking…")
        self.status_label = tk.Label(
            self,
            textvariable=self.status_var,
            font=("Segoe UI", 12, "bold"),
            fg="#f0b232",
            bg="#1e1f22",
        )
        self.status_label.pack(pady=(12, 2))

        self.detail_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self.detail_var,
            font=("Segoe UI", 9),
            fg="#b5bac1",
            bg="#1e1f22",
            wraplength=330,
            justify="center",
        ).pack(**pad)

        btn_frame = tk.Frame(self, bg="#1e1f22")
        btn_frame.pack(pady=10)

        self._btn(btn_frame, "Start", self.on_start, 0, 0)
        self._btn(btn_frame, "Stop", self.on_stop, 0, 1)
        self._btn(btn_frame, "Restart", self.on_restart, 0, 2)

        btn_frame2 = tk.Frame(self, bg="#1e1f22")
        btn_frame2.pack()
        self._btn(btn_frame2, "Install autostart", self.on_install, 0, 0, width=16)
        self._btn(btn_frame2, "Remove autostart", self.on_uninstall, 0, 1, width=16)

        btn_frame3 = tk.Frame(self, bg="#1e1f22")
        btn_frame3.pack(pady=8)
        self._btn(btn_frame3, "Open folder", self.on_open_folder, 0, 0, width=12)
        self._btn(btn_frame3, "Open log", self.on_open_log, 0, 1, width=12)
        self._btn(btn_frame3, "Edit .env", self.on_open_env, 0, 2, width=12)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(200, self.refresh_status)
        self.after(400, self._first_run_hint)

    def _btn(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        row: int,
        col: int,
        width: int = 10,
    ) -> None:
        b = tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            font=("Segoe UI", 9),
            bg="#2b2d31",
            fg="#f2f3f5",
            activebackground="#3f4147",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
        )
        b.grid(row=row, column=col, padx=4, pady=3)

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def refresh_status(self) -> None:
        up = is_bridge_up()
        pid = read_service_pid()
        auto = autostart_installed() if sys.platform == "win32" else False
        if up:
            self._set_status("RUNNING", "#3ba55d")
            detail = f"API :8000 OK"
            if pid:
                detail += f"  ·  PID {pid}"
        elif pid:
            self._set_status("STARTING…", "#f0b232")
            detail = f"PID {pid}, waiting for API"
        else:
            self._set_status("STOPPED", "#ed4245")
            detail = "Bridge is not running"
        detail += "  ·  Autostart: " + ("ON" if auto else "OFF")
        if not env_path().exists() or env_path().stat().st_size < 20:
            detail += "  ·  configure .env"
        self.detail_var.set(detail)
        self.after(2000, self.refresh_status)

    def _first_run_hint(self) -> None:
        if sys.platform != "win32":
            return
        if not autostart_installed():
            if messagebox.askyesno(
                "IdentBridge",
                "Install autostart now?\n\n"
                "Windows will ask for administrator permission.\n"
                "Bridge will start on boot and keep running.",
            ):
                self.on_install()
            else:
                start_service_process()
        elif not is_bridge_up():
            start_service_process()

    def on_start(self) -> None:
        self.detail_var.set("Starting…")
        self.update_idletasks()
        msg = start_service_process()
        self.detail_var.set(msg)
        if "failed" in msg.lower() or "STOPPED" in msg or "Check Open log" in msg:
            # Show last log lines to help diagnose
            try:
                lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
                if lines:
                    messagebox.showwarning("IdentBridge", "Service did not become healthy yet.\n\n" + "\n".join(lines))
            except OSError:
                pass

    def on_stop(self) -> None:
        msg = stop_service_process()
        self.detail_var.set(msg)

    def on_restart(self) -> None:
        self.detail_var.set("Restarting…")
        self.update_idletasks()
        msg = restart_service_process()
        self.detail_var.set(msg)

    def on_install(self) -> None:
        msg = install_autostart()
        self.detail_var.set(msg)
        if "Failed" in msg:
            messagebox.showerror("IdentBridge", msg)

    def on_uninstall(self) -> None:
        if messagebox.askyesno("IdentBridge", "Remove autostart task?"):
            msg = uninstall_autostart()
            self.detail_var.set(msg)

    def on_open_folder(self) -> None:
        path = install_dir()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def on_open_log(self) -> None:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        self._open_file(p)

    def on_open_env(self) -> None:
        ensure_install_layout()
        self._open_file(env_path())

    def _open_file(self, path) -> None:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def run_gui() -> None:
    app = ManagerApp()
    app.mainloop()
