from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from manager.paths import env_path, exe_path, install_dir, log_path, project_root, resource_path


TASK_NAME = "IdentBridge"
ENV_TEMPLATE = """IDENT_KEY=
YEASTAR_HOST=
AMI_PORT=5038
AMI_USER=
AMI_PASS=
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASS=
MYSQL_DB=
SERVICE_TIMEZONE=Europe/Moscow
INTERNAL_EXTENSIONS_REGEX=^\\d{3,4}$
TRUNK_LINE_MAP=
CLINIC_PHONE=
RECORDINGS_ROOT=
RECORD_PUBLIC_BASE_URL=
"""


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_and_rerun(extra_args: list[str] | None = None) -> None:
    """Re-launch current process with UAC prompt."""
    args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(args)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(exe_path()), params, str(install_dir()), 1
        )
    else:
        cmd = [sys.executable, "-m", "manager.main", *args]
        params = subprocess.list2cmdline(cmd[1:])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", cmd[0], params, str(project_root()), 1
        )


def ensure_install_layout() -> Path:
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    log_path().touch(exist_ok=True)

    env = env_path()
    if not env.exists():
        example = resource_path(".env.example")
        if example.exists():
            shutil.copy2(example, env)
        else:
            env.write_text(ENV_TEMPLATE, encoding="utf-8")

    # Keep a copy of app.py next to data for visibility/debug (frozen extracts logic from bundle)
    src_app = project_root() / "app.py"
    dst_app = root / "app.py"
    if src_app.exists() and not getattr(sys, "frozen", False):
        shutil.copy2(src_app, dst_app)

    return root


def copy_exe_to_install_dir() -> Path:
    """Place a stable copy of the exe under C:\\ident-bridge for autostart."""
    root = ensure_install_layout()
    target = root / "IdentBridge.exe"
    current = exe_path()
    if getattr(sys, "frozen", False):
        if current.resolve() != target.resolve():
            shutil.copy2(current, target)
        return target
    return current


def service_command() -> tuple[str, str]:
    """Return (executable, arguments) for the keep-alive service."""
    if getattr(sys, "frozen", False):
        exe = str(copy_exe_to_install_dir())
        return exe, "--service"
    return sys.executable, f'-m manager.main --service'


def autostart_installed() -> bool:
    if sys.platform != "win32":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def install_autostart() -> str:
    if sys.platform != "win32":
        return "Autostart is only supported on Windows."
    if not is_admin():
        elevate_and_rerun(["--install-autostart"])
        return "Requested administrator rights…"

    ensure_install_layout()
    exe, args = service_command()

    # Remove old task if present
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)

    # /SC ONSTART + highest privileges + run whether user is logged on (SYSTEM)
    # Use SYSTEM so bridge survives logout.
    tr = f'"{exe}" {args}'
    create = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN", TASK_NAME,
            "/TR", tr,
            "/SC", "ONSTART",
            "/RL", "HIGHEST",
            "/RU", "SYSTEM",
            "/F",
        ],
        capture_output=True,
        text=True,
    )
    if create.returncode != 0:
        # Fallback: current user at logon
        user = os.environ.get("USERNAME", "")
        create = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN", TASK_NAME,
                "/TR", tr,
                "/SC", "ONLOGON",
                "/RL", "HIGHEST",
                "/RU", user,
                "/F",
            ],
            capture_output=True,
            text=True,
        )
        if create.returncode != 0:
            return f"Failed to create task:\n{create.stdout}\n{create.stderr}"

    # Start now
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], capture_output=True)
    time.sleep(1)
    return "Autostart installed. Service started."


def uninstall_autostart() -> str:
    if sys.platform != "win32":
        return "Windows only."
    if not is_admin():
        elevate_and_rerun(["--uninstall-autostart"])
        return "Requested administrator rights…"
    subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], capture_output=True)
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return f"Remove failed:\n{r.stdout}\n{r.stderr}"
    return "Autostart removed."


def start_service_process() -> str:
    """Launch headless service process if not already up."""
    from manager.service import is_bridge_up, read_service_pid

    if is_bridge_up() or read_service_pid():
        return "Already running."

    ensure_install_layout()
    if getattr(sys, "frozen", False):
        exe = str(copy_exe_to_install_dir())
        subprocess.Popen(
            [exe, "--service"],
            cwd=str(install_dir()),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    else:
        subprocess.Popen(
            [sys.executable, "-m", "manager.main", "--service"],
            cwd=str(project_root()),
            start_new_session=True,
        )
    return "Starting…"


def stop_service_process() -> str:
    from manager.service import read_service_pid

    # End scheduled task instance if any
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], capture_output=True)

    pid = read_service_pid()
    if pid:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        return f"Stopped PID {pid}."
    return "Service was not running."


def restart_service_process() -> str:
    stop_service_process()
    time.sleep(1.5)
    return start_service_process()
