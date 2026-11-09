from __future__ import annotations

import os
import sys
from pathlib import Path


def install_dir() -> Path:
    return Path(os.environ.get("CT_SHARE_DIR", r"C:\ct-share"))


def exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable)


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(name: str) -> Path:
    """Файл из EXE (PyInstaller) или из корня проекта."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / name
            if bundled.exists():
                return bundled
    return project_root() / name


def config_path() -> Path:
    return install_dir() / "config.json"


def log_path(role: str = "app") -> Path:
    return install_dir() / f"{role}.log"
