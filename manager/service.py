from __future__ import annotations

import atexit
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

from manager.paths import install_dir, lock_path, log_path


HOST = "0.0.0.0"
PORT = 8000
HEALTH_URL = f"http://127.0.0.1:{PORT}/openapi.json"


def append_log(message: str) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"[{stamp}] {message}\n")


def port_open(host: str = "127.0.0.1", port: int = PORT, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(timeout: float = 1.5) -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def is_bridge_up() -> bool:
    return port_open() and http_ok()


class ServiceLock:
    def __init__(self) -> None:
        self.path = lock_path()
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                old_pid = int(self.path.read_text(encoding="ascii").strip() or "0")
            except ValueError:
                old_pid = 0
            if old_pid and _pid_alive(old_pid):
                return False
        self.path.write_text(str(os.getpid()), encoding="ascii")
        atexit.register(self.release)
        return True

    def release(self) -> None:
        try:
            if self.path.exists():
                text = self.path.read_text(encoding="ascii").strip()
                if text == str(os.getpid()):
                    self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_service_pid() -> Optional[int]:
    path = lock_path()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="ascii").strip() or "0")
    except ValueError:
        return None
    if pid and _pid_alive(pid):
        return pid
    return None


class BridgeServer:
    """Runs uvicorn with keep-alive restart loop."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_background(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="bridge-uvicorn", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        server = self._server
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self._server = None

    def _run_loop(self) -> None:
        os.environ.setdefault("IDENT_BRIDGE_DIR", str(install_dir()))
        while not self._stop.is_set():
            append_log("service: starting uvicorn")
            try:
                self._serve_once()
                if self._stop.is_set():
                    break
                append_log("service: uvicorn exited, restart in 3s")
            except Exception as exc:
                self.last_error = str(exc)
                append_log(f"service: error {exc!r}")
                append_log(traceback.format_exc())
            time.sleep(3)

    def _serve_once(self) -> None:
        import uvicorn

        # Import after env is set so app.py loads .env from install dir
        from app import app as fastapi_app

        config = uvicorn.Config(
            fastapi_app,
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=True,
        )
        server = uvicorn.Server(config)
        self._server = server
        server.run()
        self._server = None


def run_service_forever() -> int:
    """Headless mode for Task Scheduler / --service."""
    install_dir().mkdir(parents=True, exist_ok=True)
    lock = ServiceLock()
    if not lock.acquire():
        append_log("service: already running, exit")
        return 0

    append_log(f"service: pid={os.getpid()} start")
    bridge = BridgeServer()
    bridge.start_background()
    try:
        while True:
            time.sleep(1)
            if not bridge.running:
                append_log("service: worker died, restarting")
                bridge.start_background()
    except KeyboardInterrupt:
        append_log("service: keyboard interrupt")
    finally:
        bridge.stop()
        lock.release()
        append_log("service: stopped")
    return 0
