from __future__ import annotations

import atexit
import os
import socket
import sys
import threading
import time
import traceback
from typing import Optional
from urllib.request import urlopen

from manager.paths import env_path, install_dir, lock_path, log_path


HOST = "0.0.0.0"
PORT = 8000
HEALTH_URL = f"http://127.0.0.1:{PORT}/openapi.json"


def append_log(message: str) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def redirect_stdio_to_log() -> None:
    """Windowed PyInstaller exe has no console; prevent hangs on print/logging."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = fh  # type: ignore[assignment]
        sys.stderr = fh  # type: ignore[assignment]
    except OSError:
        pass


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


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ServiceLock:
    def __init__(self) -> None:
        self.path = lock_path()

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


def pids_listening_on_port(port: int = PORT) -> list[int]:
    """Best-effort: find PIDs bound to TCP port (Windows netstat)."""
    if sys.platform != "win32":
        return []
    import re
    import subprocess

    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    pids: set[int] = set()
    #  TCP    0.0.0.0:8000    0.0.0.0:0    LISTENING    1234
    pat = re.compile(rf":{port}\s+\S+\s+LISTENING\s+(\d+)", re.I)
    for line in out.splitlines():
        m = pat.search(line)
        if m:
            pid = int(m.group(1))
            if pid > 0:
                pids.add(pid)
    return sorted(pids)


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
        self._thread = threading.Thread(
            target=self._run_loop, name="bridge-uvicorn", daemon=True
        )
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
        os.environ["IDENT_BRIDGE_DIR"] = str(install_dir())
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
        import importlib

        from dotenv import load_dotenv
        import uvicorn

        load_dotenv(env_path(), override=True)

        # Fresh settings from .env on each restart
        if "app" in sys.modules:
            importlib.reload(sys.modules["app"])
        import app as app_module

        config = uvicorn.Config(
            app_module.app,
            host=HOST,
            port=PORT,
            log_level="info",
            access_log=True,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        self._server = server
        server.run()
        self._server = None


def run_service_forever() -> int:
    """Headless mode for Task Scheduler / --service."""
    try:
        install_dir().mkdir(parents=True, exist_ok=True)
        redirect_stdio_to_log()
        lock = ServiceLock()
        if not lock.acquire():
            append_log("service: already running, exit")
            return 0

        append_log(f"service: pid={os.getpid()} start")
        append_log(f"service: dir={install_dir()} env={env_path()}")
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
    except Exception:
        append_log("service: fatal\n" + traceback.format_exc())
        return 1
