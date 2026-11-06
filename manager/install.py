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


def _creation_flags() -> int:
    flags = 0
    if sys.platform == "win32":
        # Do NOT use DETACHED_PROCESS — it often breaks windowed PyInstaller children.
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    return flags


def _startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def message_box(text: str, title: str = "IdentBridge") -> None:
    if sys.platform != "win32":
        print(f"{title}: {text}")
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x40)
    except Exception:
        pass


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_and_rerun(extra_args: list[str] | None = None) -> None:
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

    src_app = project_root() / "app.py"
    dst_app = root / "app.py"
    if src_app.exists() and not getattr(sys, "frozen", False):
        shutil.copy2(src_app, dst_app)

    return root


def copy_exe_to_install_dir() -> Path:
    root = ensure_install_layout()
    target = root / "IdentBridge.exe"
    current = exe_path()
    if getattr(sys, "frozen", False):
        if current.resolve() != target.resolve():
            try:
                shutil.copy2(current, target)
            except OSError:
                # Running from target already, or file locked — use current
                return current
        return target if target.exists() else current
    return current


def service_command() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        exe = str(copy_exe_to_install_dir())
        return exe, "--service"
    return sys.executable, "-m manager.main --service"


def autostart_installed() -> bool:
    if sys.platform != "win32":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    return r.returncode == 0


def _register_task_powershell(exe: str, args: str) -> tuple[bool, str]:
    """Register task with WorkingDirectory via PowerShell (more reliable than schtasks)."""
    # Escape for PowerShell single-quoted strings
    def q(s: str) -> str:
        return s.replace("'", "''")

    root = str(install_dir())
    ps = f"""
$ErrorActionPreference = 'Stop'
$TaskName = '{TASK_NAME}'
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute '{q(exe)}' -Argument '{q(args)}' -WorkingDirectory '{q(root)}'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650) -MultipleInstances IgnoreNew -Hidden
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
'OK'
"""
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "OK" in out, out


def install_autostart() -> str:
    from manager.service import append_log

    if sys.platform != "win32":
        return "Autostart is only supported on Windows."
    if not is_admin():
        elevate_and_rerun(["--install-autostart"])
        return "Requested administrator rights…"

    try:
        ensure_install_layout()
        exe, args = service_command()
        append_log(f"install: registering task exe={exe} args={args}")
        ok, out = _register_task_powershell(exe, args)
        if not ok:
            # Fallback schtasks ONLOGON as current user
            user = os.environ.get("USERNAME", "")
            tr = f'"{exe}" {args}'
            subprocess.run(
                ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True,
                creationflags=_creation_flags(),
            )
            create = subprocess.run(
                [
                    "schtasks",
                    "/Create",
                    "/TN", TASK_NAME,
                    "/TR", tr,
                    "/SC", "ONLOGON",
                    "/RL", "HIGHEST",
                    "/RU", user,
                    "/IT",
                    "/F",
                ],
                capture_output=True,
                text=True,
                creationflags=_creation_flags(),
            )
            if create.returncode != 0:
                msg = f"Failed to create task:\n{out}\n{create.stdout}\n{create.stderr}"
                append_log(msg)
                message_box(msg)
                return msg
            subprocess.run(
                ["schtasks", "/Run", "/TN", TASK_NAME],
                capture_output=True,
                creationflags=_creation_flags(),
            )

        # Also ensure a live process right now
        time.sleep(1.5)
        start_service_process()
        msg = "Autostart installed. Service started."
        append_log(msg)
        message_box(msg)
        return msg
    except Exception as exc:
        msg = f"Install failed: {exc}"
        append_log(msg)
        message_box(msg)
        return msg


def uninstall_autostart() -> str:
    if sys.platform != "win32":
        return "Windows only."
    if not is_admin():
        elevate_and_rerun(["--uninstall-autostart"])
        return "Requested administrator rights…"
    subprocess.run(
        ["schtasks", "/End", "/TN", TASK_NAME],
        capture_output=True,
        creationflags=_creation_flags(),
    )
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    if r.returncode != 0:
        msg = f"Remove failed:\n{r.stdout}\n{r.stderr}"
        message_box(msg)
        return msg
    message_box("Autostart removed.")
    return "Autostart removed."


def start_service_process() -> str:
    from manager.service import append_log, is_bridge_up, read_service_pid

    if is_bridge_up():
        return "Already running."
    if read_service_pid():
        return "Already starting…"

    ensure_install_layout()
    append_log("install: launching --service process")

    try:
        if getattr(sys, "frozen", False):
            exe = str(copy_exe_to_install_dir())
            subprocess.Popen(
                [exe, "--service"],
                cwd=str(install_dir()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=_startupinfo(),
                creationflags=_creation_flags(),
                close_fds=True,
            )
        else:
            subprocess.Popen(
                [sys.executable, "-m", "manager.main", "--service"],
                cwd=str(project_root()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except Exception as exc:
        append_log(f"install: start failed {exc!r}")
        return f"Start failed: {exc}"

    # Wait briefly for health
    for _ in range(20):
        time.sleep(0.5)
        if is_bridge_up():
            return "Started. API :8000 OK"
        if read_service_pid() is None and _ > 4:
            break

    # Still not up — point to log
    return "Started process, waiting for API… Check Open log if it stays STOPPED."


def stop_service_process() -> str:
    from manager.service import append_log, pids_listening_on_port, read_service_pid

    append_log("install: stop requested")
    if sys.platform == "win32":
        subprocess.run(
            ["schtasks", "/End", "/TN", TASK_NAME],
            capture_output=True,
            creationflags=_creation_flags(),
        )

    killed: list[int] = []
    pid = read_service_pid()
    if pid:
        killed.append(pid)

    for p in pids_listening_on_port(8000):
        if p not in killed:
            killed.append(p)

    for p in killed:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(p), "/F", "/T"],
                capture_output=True,
                creationflags=_creation_flags(),
            )
        else:
            try:
                os.kill(p, 15)
            except OSError:
                pass

    # Clear stale lock
    lock = install_dir() / "service.lock"
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass

    if killed:
        return f"Stopped PID(s): {', '.join(map(str, killed))}"
    return "Service was not running."


def restart_service_process() -> str:
    stop_msg = stop_service_process()
    time.sleep(1.5)
    start_msg = start_service_process()
    return f"{stop_msg} → {start_msg}"
