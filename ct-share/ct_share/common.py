"""Общие утилиты: имена пациентов, сетевая папка, дисковод."""

from __future__ import annotations

import ctypes
import re
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ct_share.install import load_config

# Давтян_Анна_Самвеловна_1983_10_01_NNTViewer&DICOM
PATIENT_FOLDER_RE = re.compile(
    r"^(?P<name>.+?)_(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})"
    r"(?:_(?P<suffix>.+))?$",
    re.UNICODE,
)

DRIVE_REMOVABLE = 2
DRIVE_CDROM = 5


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
    stem = folder_name
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    m = PATIENT_FOLDER_RE.match(stem.strip())
    if not m:
        return stem.replace("_", " "), None
    fio = m.group("name").replace("_", " ")
    birth = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
    return fio, birth


def display_label(folder_name: str) -> str:
    fio, birth = parse_patient_folder(folder_name)
    return f"{fio} ({birth})" if birth else fio


def looks_like_patient_name(name: str) -> bool:
    stem = name[:-4] if name.lower().endswith(".zip") else name
    return bool(PATIENT_FOLDER_RE.match(stem))


def looks_like_ct_folder(name: str) -> bool:
    lower = name.lower()
    if looks_like_patient_name(name):
        return True
    return any(h in lower for h in ("nntviewer", "dicom"))


def volume_label(root: Path) -> str:
    if sys_platform_is_win():
        try:
            kernel32 = ctypes.windll.kernel32
            buf = ctypes.create_unicode_buffer(261)
            ok = kernel32.GetVolumeInformationW(
                str(root),
                buf,
                len(buf),
                None,
                None,
                None,
                None,
                0,
            )
            if ok and buf.value.strip():
                return buf.value.strip()
        except Exception:
            pass
    return ""


def sys_platform_is_win() -> bool:
    import sys

    return sys.platform == "win32"


def shallow_has_ct(path: Path, max_depth: int = 2) -> bool:
    """Быстрая проверка: не сканируем весь диск целиком."""
    try:
        base_depth = len(path.parts)
        for child in path.rglob("*"):
            if len(child.parts) - base_depth > max_depth:
                continue
            name = child.name.lower()
            if name.endswith(".dcm") or name in {"dicomdir", "dicom"}:
                return True
            if "nntviewer" in name:
                return True
            if name.endswith(".zip") and looks_like_patient_name(child.name):
                return True
    except OSError:
        return False
    return False


def media_fingerprint(root: Path) -> str:
    """Отпечаток носителя: меняется при смене диска."""
    try:
        names = sorted(p.name for p in root.iterdir())[:30]
    except OSError:
        names = []
    label = volume_label(root)
    return f"{root}|{label}|{'/'.join(names)}"


def find_patient_source_on_disc(disc_root: Path) -> Optional[tuple[str, Path]]:
    try:
        entries = [p for p in disc_root.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None

    # 1) Папка с ФИО пациента
    patient_dirs = [p for p in entries if p.is_dir() and looks_like_patient_name(p.name)]
    if patient_dirs:
        return patient_dirs[0].name, patient_dirs[0]

    # 2) ZIP с ФИО (часто так отдают снимки)
    patient_zips = [
        p for p in entries if p.is_file() and p.suffix.lower() == ".zip" and looks_like_patient_name(p.name)
    ]
    if patient_zips:
        z = patient_zips[0]
        return z.stem, z

    # 3) Типичный NNT/DICOM-диск без имени в корне
    ct_dirs = [p for p in entries if p.is_dir() and looks_like_ct_folder(p.name)]
    if ct_dirs or shallow_has_ct(disc_root):
        label = volume_label(disc_root)
        if label and looks_like_patient_name(label):
            folder_name = label
        elif label:
            safe = re.sub(r"[^\w\-А-Яа-яЁё]+", "_", label, flags=re.UNICODE).strip("_")
            folder_name = safe or f"CT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        else:
            folder_name = f"CT_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return folder_name, disc_root

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


def candidate_drive_roots() -> list[Path]:
    """CD/DVD и съёмные носители (USB-DVD часто = Removable)."""
    if not sys_platform_is_win():
        return []
    try:
        kernel32 = ctypes.windll.kernel32
    except Exception:
        return []

    roots: list[Path] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            dtype = kernel32.GetDriveTypeW(root)
        except Exception:
            continue
        if dtype in (DRIVE_CDROM, DRIVE_REMOVABLE):
            roots.append(Path(root))
    return roots


def disc_is_ready(root: Path) -> bool:
    try:
        if not root.exists():
            return False
        next(root.iterdir(), None)
        return True
    except OSError:
        return False


def iter_ready_media() -> Iterable[Path]:
    for root in candidate_drive_roots():
        if disc_is_ready(root):
            yield root


# backward-compatible alias
def iter_ready_discs() -> Iterable[Path]:
    yield from iter_ready_media()


def describe_drives() -> str:
    parts: list[str] = []
    for root in candidate_drive_roots():
        ready = disc_is_ready(root)
        label = volume_label(root) if ready else ""
        mark = "есть носитель" if ready else "пусто"
        extra = f", {label}" if label else ""
        parts.append(f"{root} {mark}{extra}")
    return "; ".join(parts) if parts else "дисковод не найден"
