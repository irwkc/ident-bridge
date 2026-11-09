"""Общие утилиты: умный поиск ФИО, сетевая папка, дисковод."""

from __future__ import annotations

import ctypes
import re
import string
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ct_share.install import load_config

# Давтян_Анна_Самвеловна_1983_10_01_NNTViewer&DICOM
NAME_WITH_DATE_RE = re.compile(
    r"^(?P<name>.+?)[_\s\-]+(?P<year>\d{4})[_\s\-](?P<month>\d{2})[_\s\-](?P<day>\d{2})"
    r"(?:[_\s\-]+(?P<suffix>.+))?$",
    re.UNICODE,
)

JUNK_SUFFIX_RE = re.compile(
    r"[_\s\-]*(?:NNTViewer.*|DICOM.*|CDViewer.*|eFilm.*|RadiAnt.*|Viewer.*|&.*)$",
    re.IGNORECASE | re.UNICODE,
)

TECHNICAL_NAMES = {
    "dicom",
    "dicomdir",
    "nntviewer",
    "viewer",
    "images",
    "image",
    "data",
    "files",
    "autorun",
    "system",
    "meta",
    "metadata",
}

DRIVE_REMOVABLE = 2
DRIVE_CDROM = 5

# DICOM (0010,0010) Patient's Name, little-endian
_DICOM_PN_TAG = b"\x10\x00\x10\x00"
_DICOM_BD_TAG = b"\x10\x00\x30\x00"  # (0010,0030) Patient's Birth Date


@dataclass(frozen=True)
class PatientScan:
    folder_name: str
    path: Path
    display_name: str
    birth_date: Optional[str]
    mtime: float


@dataclass(frozen=True)
class DetectedScan:
    """Найденный снимок: чистое ФИО для папки + откуда копировать."""

    fio_folder: str  # Давтян_Анна_Самвеловна
    display_name: str  # Давтян Анна Самвеловна
    birth_date: Optional[str]
    source: Path


def shared_root() -> Path:
    cfg = load_config()
    path = Path(cfg["shared_folder"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _strip_junk(name: str) -> str:
    stem = name
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    stem = stem.strip().strip("_").strip("-")
    stem = JUNK_SUFFIX_RE.sub("", stem).strip("_").strip("-").strip()
    return stem


def _normalize_fio_parts(raw: str) -> list[str]:
    """Разбивает строку ФИО на части, убирает мусор."""
    s = (
        raw.replace("^", " ")
        .replace("_", " ")
        .replace("/", " ")
        .replace("\\", " ")
        .replace("-", " ")
    )
    s = re.sub(r"[^\wА-Яа-яЁё]+", " ", s, flags=re.UNICODE)
    parts = [p for p in s.split() if p and p.lower() not in TECHNICAL_NAMES]
    # отбрасываем чисто цифровые куски (годы и т.п.)
    parts = [p for p in parts if not re.fullmatch(r"\d{1,4}", p)]
    return parts


def fio_to_folder(parts: list[str] | str) -> str:
    if isinstance(parts, str):
        parts = _normalize_fio_parts(parts)
    if not parts:
        return ""
    return "_".join(parts)


def fio_to_display(parts: list[str] | str) -> str:
    if isinstance(parts, str):
        parts = _normalize_fio_parts(parts)
    return " ".join(parts)


def parse_patient_folder(folder_name: str) -> tuple[str, Optional[str]]:
    """Для уже созданных папок на шаре / старых имён с диска."""
    stem = _strip_junk(folder_name)
    m = NAME_WITH_DATE_RE.match(stem)
    if m:
        parts = _normalize_fio_parts(m.group("name"))
        birth = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
        return fio_to_display(parts) or stem.replace("_", " "), birth
    parts = _normalize_fio_parts(stem)
    return fio_to_display(parts) or stem.replace("_", " "), None


def display_label(folder_name: str) -> str:
    fio, birth = parse_patient_folder(folder_name)
    return f"{fio} ({birth})" if birth else fio


def extract_identity_from_text(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Из произвольной строки → (fio_folder, display, birth_date).
    """
    stem = _strip_junk(text)
    if not stem or stem.lower() in TECHNICAL_NAMES:
        return None, None, None

    m = NAME_WITH_DATE_RE.match(stem)
    if m:
        parts = _normalize_fio_parts(m.group("name"))
        if len(parts) >= 2:
            birth = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
            return fio_to_folder(parts), fio_to_display(parts), birth

    parts = _normalize_fio_parts(stem)
    # Фамилия + Имя (+ Отчество) — минимум 2 слова
    if len(parts) >= 2 and all(re.search(r"[A-Za-zА-Яа-яЁё]", p) for p in parts[:3]):
        return fio_to_folder(parts), fio_to_display(parts), None

    return None, None, None


def looks_like_patient_name(name: str) -> bool:
    folder, _, _ = extract_identity_from_text(name)
    return bool(folder)


def looks_like_ct_folder(name: str) -> bool:
    lower = name.lower()
    if looks_like_patient_name(name):
        return True
    return any(h in lower for h in ("nntviewer", "dicom", "viewer"))


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


def _decode_dicom_text(raw: bytes) -> str:
    raw = raw.replace(b"\x00", b"").strip()
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore").strip()


def _read_dicom_element(data: bytes, tag: bytes) -> Optional[str]:
    """Ищет тег в буфере (explicit/implicit VR LE) и возвращает строку."""
    start = 0
    while True:
        idx = data.find(tag, start)
        if idx < 0 or idx + 8 > len(data):
            return None
        pos = idx + 4
        # Explicit VR: 2-char VR
        if pos + 2 <= len(data) and data[pos : pos + 2].isascii() and data[pos : pos + 2].isalpha():
            vr = data[pos : pos + 2].decode("ascii", errors="ignore")
            pos += 2
            if vr in {"OB", "OW", "OF", "SQ", "UT", "UN", "OD", "OL", "UC", "UR"}:
                if pos + 6 > len(data):
                    start = idx + 1
                    continue
                pos += 2  # reserved
                length = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                if pos + 2 > len(data):
                    start = idx + 1
                    continue
                length = struct.unpack_from("<H", data, pos)[0]
                pos += 2
        else:
            # Implicit VR
            if pos + 4 > len(data):
                start = idx + 1
                continue
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4

        if length == 0xFFFFFFFF or length > 256 or pos + length > len(data):
            start = idx + 1
            continue

        value = _decode_dicom_text(data[pos : pos + length])
        if value:
            return value
        start = idx + 1


def read_dicom_patient(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Читает PatientName и BirthDate из .dcm / DICOMDIR (первые ~256 КБ)."""
    try:
        with path.open("rb") as f:
            data = f.read(262144)
    except OSError:
        return None, None

    if b"DICM" not in data[:132] and _DICOM_PN_TAG not in data:
        # всё равно попробуем — некоторые файлы без преамбулы
        if _DICOM_PN_TAG not in data:
            return None, None

    name = _read_dicom_element(data, _DICOM_PN_TAG)
    birth_raw = _read_dicom_element(data, _DICOM_BD_TAG)
    birth = None
    if birth_raw and re.fullmatch(r"\d{8}", birth_raw.strip()):
        b = birth_raw.strip()
        birth = f"{b[0:4]}-{b[4:6]}-{b[6:8]}"
    return name, birth


def find_dicom_files(root: Path, limit: int = 40) -> list[Path]:
    found: list[Path] = []
    try:
        # Сначала типичные места
        candidates: list[Path] = []
        for pattern in ("**/*.dcm", "**/DICOMDIR", "**/DICOM/**/*", "**/*"):
            try:
                for p in root.glob(pattern):
                    if not p.is_file():
                        continue
                    name = p.name.upper()
                    if name.endswith(".DCM") or name == "DICOMDIR" or name.endswith(".DICOM"):
                        candidates.append(p)
                    elif p.suffix == "" and p.stat().st_size > 128:
                        # файлы без расширения — часто сырой DICOM
                        candidates.append(p)
                    if len(candidates) >= limit * 2:
                        break
            except OSError:
                continue
            if len(candidates) >= limit:
                break

        for p in candidates:
            try:
                with p.open("rb") as f:
                    head = f.read(132)
                if b"DICM" in head or p.suffix.lower() == ".dcm" or p.name.upper() == "DICOMDIR":
                    found.append(p)
                elif _DICOM_PN_TAG in head:
                    found.append(p)
            except OSError:
                continue
            if len(found) >= limit:
                break
    except OSError:
        pass
    return found


def identity_from_dicom_tree(root: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    for dcm in find_dicom_files(root):
        pn, birth = read_dicom_patient(dcm)
        if not pn:
            continue
        # PN: Last^First^Middle
        parts = [p for p in pn.replace("=", "^").split("^") if p.strip()]
        parts = _normalize_fio_parts(" ".join(parts))
        if len(parts) >= 1:
            folder = fio_to_folder(parts)
            display = fio_to_display(parts)
            if folder:
                return folder, display, birth
    return None, None, None


def shallow_has_ct(path: Path, max_depth: int = 3) -> bool:
    try:
        base_depth = len(path.parts)
        for child in path.rglob("*"):
            depth = len(child.parts) - base_depth
            if depth > max_depth:
                continue
            name = child.name.lower()
            if name.endswith(".dcm") or name in {"dicomdir", "dicom"}:
                return True
            if "nntviewer" in name or "viewer" in name:
                return True
            if name.endswith(".zip") and looks_like_patient_name(child.name):
                return True
    except OSError:
        return False
    return False


def media_fingerprint(root: Path) -> str:
    try:
        names = sorted(p.name for p in root.iterdir())[:30]
    except OSError:
        names = []
    label = volume_label(root)
    return f"{root}|{label}|{'/'.join(names)}"


def _identity_from_path_name(path: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    return extract_identity_from_text(path.name)


def detect_scan(disc_root: Path) -> Optional[DetectedScan]:
    """
    Умно ищет снимок и ФИО:
    1) имя папки/ZIP на диске
    2) DICOM PatientName внутри файлов
    3) метка тома
    Папка на шаре = только чистое ФИО.
    """
    try:
        entries = [p for p in disc_root.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None

    # --- кандидаты источника ---
    patient_dirs = [p for p in entries if p.is_dir() and looks_like_patient_name(p.name)]
    patient_zips = [
        p
        for p in entries
        if p.is_file() and p.suffix.lower() == ".zip" and looks_like_patient_name(p.name)
    ]
    ct_dirs = [p for p in entries if p.is_dir() and looks_like_ct_folder(p.name)]
    has_ct = bool(patient_dirs or patient_zips or ct_dirs or shallow_has_ct(disc_root))
    if not has_ct:
        return None

    source: Path
    if patient_dirs:
        source = patient_dirs[0]
    elif patient_zips:
        source = patient_zips[0]
    elif ct_dirs and len(ct_dirs) == 1 and not looks_like_patient_name(ct_dirs[0].name):
        # один DICOM/NNTViewer — копируем весь диск
        source = disc_root
    elif ct_dirs:
        source = disc_root
    else:
        source = disc_root

    # --- ФИО: приоритет имя → DICOM → метка тома ---
    fio_folder = display = birth = None

    for candidate in (
        source if source != disc_root else None,
        *(patient_dirs[:1]),
        *(patient_zips[:1]),
        *entries,
    ):
        if candidate is None:
            continue
        fio_folder, display, birth = _identity_from_path_name(candidate)
        if fio_folder:
            break

    if not fio_folder:
        scan_root = source if source.is_dir() else disc_root
        fio_folder, display, birth = identity_from_dicom_tree(scan_root)

    if not fio_folder:
        label = volume_label(disc_root)
        if label:
            fio_folder, display, birth = extract_identity_from_text(label)

    if not fio_folder:
        # последний шанс — техническое имя с датой
        fio_folder = f"КТ_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        display = fio_folder.replace("_", " ")

    # если имя взяли из папки вида ФИО_дата_..., birth уже есть;
    # если нет — попробуем добрать дату из DICOM
    if birth is None and source.is_dir():
        _, _, birth2 = identity_from_dicom_tree(source if source.is_dir() else disc_root)
        birth = birth2

    return DetectedScan(
        fio_folder=fio_folder,
        display_name=display or fio_folder.replace("_", " "),
        birth_date=birth,
        source=source,
    )


def find_patient_source_on_disc(disc_root: Path) -> Optional[tuple[str, Path]]:
    """Совместимость: (имя_папки_фио, source)."""
    det = detect_scan(disc_root)
    if not det:
        return None
    return det.fio_folder, det.source


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
