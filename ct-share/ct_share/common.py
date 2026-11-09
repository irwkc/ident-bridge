"""Общие утилиты: имена пациентов, сетевая папка, дисковод."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ct_share.install import load_config

# Давтян_Анна_Самвеловна_1983_10_01_NNTViewer&DICOM
PATIENT_FOLDER_RE = re.compile(
    r"^(?P<name>.+?)_(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})"
    r"(?:_(?P<suffix>.+))?$",
    re.UNICODE,
)


@dataclass(frozen=True)
class PatientScan:
    folder_name: str
    path: Path
    display_name: str
    birth_date: Optional[str]
    mtime: float


def shared_root() -> Path:
    cfg = load_config()
    path = Path(cfg["shared_folder"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_patient_folder(folder_name: str) -> tuple[str, Optional[str]]:
    m = PATIENT_FOLDER_RE.match(folder_name.strip())
    if not m:
        return folder_name.replace("_", " "), None
    fio = m.group("name").replace("_", " ")
    birth = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
    return fio, birth


def display_label(folder_name: str) -> str:
    fio, birth = parse_patient_folder(folder_name)
    return f"{fio} ({birth})" if birth else fio


def looks_like_ct_folder(name: str) -> bool:
    lower = name.lower()
    if PATIENT_FOLDER_RE.match(name):
        return True
    return any(h in lower for h in ("nntviewer", "dicom"))


def path_has_ct_content(path: Path) -> bool:
    try:
        for child in path.rglob("*"):
            name = child.name.lower()
            if name.endswith(".dcm") or name in {"dicomdir", "dicom"}:
                return True
            if "nntviewer" in name:
                return True
    except OSError:
        return False
    return False


def find_patient_source_on_disc(disc_root: Path) -> Optional[tuple[str, Path]]:
    try:
        entries = [p for p in disc_root.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None

    named = [p for p in entries if p.is_dir() and looks_like_ct_folder(p.name)]
    if len(named) == 1:
        return named[0].name, named[0]
    if named:
        for p in named:
            if PATIENT_FOLDER_RE.match(p.name):
                return p.name, p
        return named[0].name, named[0]

    if path_has_ct_content(disc_root):
        vol = disc_root.name.strip("\\/") or "CT_SCAN"
        safe = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", vol, flags=re.UNICODE).strip("_")
        return safe or "CT_SCAN", disc_root

    return None


def list_scans(root: Optional[Path] = None) -> list[PatientScan]:
    root = root or shared_root()
    scans: list[PatientScan] = []
    if not root.exists():
        return scans

    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        fio, birth = parse_patient_folder(child.name)
        label = f"{fio} ({birth})" if birth else fio
        scans.append(
            PatientScan(
                folder_name=child.name,
                path=child,
                display_name=label,
                birth_date=birth,
                mtime=mtime,
            )
        )
    scans.sort(key=lambda s: s.mtime, reverse=True)
    return scans


def search_scans(query: str, limit: Optional[int] = None) -> list[PatientScan]:
    q = query.strip().lower().replace(" ", "_")
    q_spaces = query.strip().lower()
    scans = list_scans()
    if q_spaces:
        scans = [
            s
            for s in scans
            if q in s.folder_name.lower()
            or q_spaces in s.display_name.lower()
            or q_spaces in s.folder_name.lower().replace("_", " ")
        ]
    if limit is not None:
        return scans[:limit]
    return scans


def recent_scans(count: Optional[int] = None) -> list[PatientScan]:
    cfg = load_config()
    n = count if count is not None else int(cfg.get("recent_count", 5))
    return list_scans()[:n]


def cdrom_roots() -> list[Path]:
    try:
        kernel32 = __import__("ctypes").windll.kernel32  # type: ignore[attr-defined]
    except Exception:
        return []

    DRIVE_CDROM = 5
    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if kernel32.GetDriveTypeW(root) == DRIVE_CDROM:
                roots.append(Path(root))
        except Exception:
            continue
    return roots


def disc_is_ready(root: Path) -> bool:
    try:
        if not root.exists():
            return False
        next(root.iterdir(), None)
        return True
    except OSError:
        return False


def iter_ready_discs() -> Iterable[Path]:
    for root in cdrom_roots():
        if disc_is_ready(root):
            yield root
