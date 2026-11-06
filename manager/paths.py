from __future__ import annotations

import os
import sys
from pathlib import Path


def install_dir() -> Path:
    return Path(os.environ.get("IDENT_BRIDGE_DIR", r"C:\ident-bridge"))


def exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    # Dev: run via python -m manager.main
    return Path(sys.executable)


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(name: str) -> Path:
    """File shipped inside the exe (PyInstaller) or the repo root."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / name
            if bundled.exists():
                return bundled
    return project_root() / name


def log_path() -> Path:
    return install_dir() / "bridge.log"


def env_path() -> Path:
    return install_dir() / ".env"


def lock_path() -> Path:
    return install_dir() / "service.lock"
