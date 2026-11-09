from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ct_share.paths import config_path, exe_path, install_dir, log_path, project_root, resource_path

DEFAULT_CONFIG = {
    "shared_folder": r"\\SERVER\KT",
    "poll_seconds": 2,
    "recent_count": 5,
}

ADMIN_EXE_NAME = "CTShareAdmin.exe"
DOCTOR_EXE_NAME = "CTShareDoctor.exe"
ADMIN_TASK = "CTShareAdmin"
ADMIN_SHORTCUT = "CT Share Админ.lnk"
DOCTOR_SHORTCUT = "CT Share Врач.lnk"


def _creation_flags() -> int:
    flags = 0
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags


def message_box(text: str, title: str = "CT Share", kind: str = "info") -> None:
    if sys.platform != "win32":
        print(f"{title}: {text}")
        return
    icons = {"info": 0x40, "warn": 0x30, "error": 0x10, "question": 0x20}
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), icons.get(kind, 0x40))
    except Exception:
        pass


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def append_log(role: str, text: str) -> None:
    try:
        root = install_dir()
        root.mkdir(parents=True, exist_ok=True)
        path = log_path(role)
        with path.open("a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except OSError:
        pass


def ensure_install_layout() -> Path:
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    cfg = config_path()
    if not cfg.exists():
        example = resource_path("config.example.json")
        if example.exists():
            shutil.copy2(example, cfg)
        else:
            cfg.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    for role in ("admin", "doctor"):
        log_path(role).touch(exist_ok=True)
    return root


def load_config() -> dict:
    ensure_install_layout()
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(data: dict) -> None:
    ensure_install_layout()
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    config_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def config_needs_setup(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    folder = str(cfg.get("shared_folder", "")).strip().replace("/", "\\").rstrip("\\")
    placeholders = {r"\\SERVER\KT", r"\\server\kt"}
    return not folder or folder.lower() in {p.lower() for p in placeholders}


def copy_exe_to_install_dir(exe_name: str) -> Path:
    root = ensure_install_layout()
    target = root / exe_name
    current = exe_path()
    if getattr(sys, "frozen", False):
        if current.resolve() != target.resolve():
            try:
                shutil.copy2(current, target)
            except OSError:
                return current if current.exists() else target
        return target if target.exists() else current
    return current


def _desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def _start_menu_dir() -> Path:
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "CT Share"


def create_shortcut(link_path: Path, target: Path, arguments: str = "", description: str = "") -> None:
    if sys.platform != "win32":
        return
    link_path.parent.mkdir(parents=True, exist_ok=True)
    # PowerShell COM shortcut
    def q(s: str) -> str:
        return s.replace("'", "''")

    ps = f"""
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut('{q(str(link_path))}')
$sc.TargetPath = '{q(str(target))}'
$sc.Arguments = '{q(arguments)}'
$sc.WorkingDirectory = '{q(str(target.parent))}'
$sc.Description = '{q(description)}'
$sc.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )


def install_shortcuts(role: str) -> None:
    """role: admin | doctor"""
    if role == "admin":
        name = ADMIN_SHORTCUT
        exe_name = ADMIN_EXE_NAME
        desc = "CT Share — мониторинг дисковода"
    else:
        name = DOCTOR_SHORTCUT
        exe_name = DOCTOR_EXE_NAME
        desc = "CT Share — поиск КТ"

    if getattr(sys, "frozen", False):
        target = copy_exe_to_install_dir(exe_name)
    else:
        # Dev: shortcut to python -m
        target = Path(sys.executable)
        module = "ct_share.admin_main" if role == "admin" else "ct_share.doctor_main"
        args = f'-m {module}'
        create_shortcut(_desktop_dir() / name, target, args, desc)
        create_shortcut(_start_menu_dir() / name, target, args, desc)
        return

    create_shortcut(_desktop_dir() / name, target, "", desc)
    create_shortcut(_start_menu_dir() / name, target, "", desc)


def elevate_and_rerun(extra_args: list[str], exe_name: str) -> None:
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(extra_args)
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(exe_path()), params, str(install_dir()), 1
        )
    else:
        module = "ct_share.admin_main" if "Admin" in exe_name or "admin" in " ".join(extra_args) else "ct_share.doctor_main"
        if "--install-autostart" in extra_args or "--uninstall-autostart" in extra_args:
            module = "ct_share.admin_main"
        cmd = [sys.executable, "-m", module, *extra_args]
        params = subprocess.list2cmdline(cmd[1:])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", cmd[0], params, str(project_root()), 1
        )


def autostart_installed() -> bool:
    if sys.platform != "win32":
        return False
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", ADMIN_TASK],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    return r.returncode == 0


def install_admin_autostart() -> str:
    if sys.platform != "win32":
        return "Автозапуск только на Windows."
    if not is_admin():
        elevate_and_rerun(["--install-autostart"], ADMIN_EXE_NAME)
        return "Запрос прав администратора…"

    ensure_install_layout()
    if getattr(sys, "frozen", False):
        exe = str(copy_exe_to_install_dir(ADMIN_EXE_NAME))
    else:
        exe = sys.executable
        # For scheduled task in dev: pythonw -m ct_share.admin_main
        args = f'-m ct_share.admin_main'
        ok, out = _register_task(exe, args)
        msg = "Автозапуск установлен." if ok else f"Не удалось:\n{out}"
        message_box(msg, "CT Share Админ")
        return msg

    ok, out = _register_task(exe, "")
    msg = "Автозапуск установлен. Админ-приложение стартует при входе в Windows." if ok else f"Не удалось:\n{out}"
    append_log("admin", msg)
    message_box(msg, "CT Share Админ")
    return msg


def uninstall_admin_autostart() -> str:
    if sys.platform != "win32":
        return "Windows only."
    if not is_admin():
        elevate_and_rerun(["--uninstall-autostart"], ADMIN_EXE_NAME)
        return "Запрос прав администратора…"
    subprocess.run(
        ["schtasks", "/End", "/TN", ADMIN_TASK],
        capture_output=True,
        creationflags=_creation_flags(),
    )
    r = subprocess.run(
        ["schtasks", "/Delete", "/TN", ADMIN_TASK, "/F"],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    if r.returncode != 0:
        msg = f"Не удалось удалить:\n{r.stdout}\n{r.stderr}"
        message_box(msg, "CT Share Админ", "error")
        return msg
    message_box("Автозапуск удалён.", "CT Share Админ")
    return "Автозапуск удалён."


def _register_task(exe: str, args: str) -> tuple[bool, str]:
    def q(s: str) -> str:
        return s.replace("'", "''")

    root = str(install_dir())
    ps = f"""
$ErrorActionPreference = 'Stop'
$TaskName = '{ADMIN_TASK}'
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute '{q(exe)}' -Argument '{q(args)}' -WorkingDirectory '{q(root)}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
'OK'
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        creationflags=_creation_flags(),
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "OK" in out, out


def first_run_install(role: str) -> None:
    """Копирует EXE, создаёт папку и ярлыки."""
    ensure_install_layout()
    if getattr(sys, "frozen", False):
        exe_name = ADMIN_EXE_NAME if role == "admin" else DOCTOR_EXE_NAME
        copy_exe_to_install_dir(exe_name)
    try:
        install_shortcuts(role)
    except Exception as exc:
        append_log(role, f"shortcuts failed: {exc}")
