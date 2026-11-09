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
    "dotnet20",
    "setup",
    "vcredist",
    "vcredist_x86",
    "vcredist_x64",
}

# GUID / хеш-имена вроде bded778e759c4a05...
HEX_NAME_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
VOLUME_TECH_RE = re.compile(r"^(GWG|CD|DVD|VOLUME|DISK|DISC)\d", re.IGNORECASE)

SKIP_EXE_NAMES = {
    "setup",
    "install",
    "uninstall",
    "vcredist",
    "dotnet",
    "dotnetfx",
    "msiexec",
    "redist",
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


def is_garbage_name(text: str) -> bool:
    """Хеши, GUID, технические метки диска — не ФИО."""
    stem = _strip_junk(text)
    if not stem:
        return True
    compact = re.sub(r"[\-_]", "", stem)
    if HEX_NAME_RE.fullmatch(compact):
        return True
    if VOLUME_TECH_RE.match(stem):
        return True
    if stem.lower() in TECHNICAL_NAMES:
        return True
    # одно «слово» из латиницы+цифр без кириллицы и без пробелов/подчёркиваний как ФИО
    if len(stem) >= 16 and re.fullmatch(r"[0-9A-Za-z\-_]+", stem) and not re.search(r"[А-Яа-яЁё]", stem):
        if not re.search(r"[_\s]", text) or HEX_NAME_RE.fullmatch(compact):
            return True
    return False


def extract_identity_from_text(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Из произвольной строки → (fio_folder, display, birth_date).
    """
    stem = _strip_junk(text)
    if not stem or stem.lower() in TECHNICAL_NAMES or is_garbage_name(stem):
        return None, None, None

    m = NAME_WITH_DATE_RE.match(stem)
    if m:
        parts = _normalize_fio_parts(m.group("name"))
        if len(parts) >= 2 and not is_garbage_name("_".join(parts)):
            birth = f"{m.group('year')}-{m.group('month')}-{m.group('day')}"
            return fio_to_folder(parts), fio_to_display(parts), birth

    parts = _normalize_fio_parts(stem)
    # Фамилия + Имя (+ Отчество) — минимум 2 слова с буквами
    if len(parts) >= 2 and all(re.search(r"[A-Za-zА-Яа-яЁё]", p) for p in parts[:3]):
        folder = fio_to_folder(parts)
        if folder and not is_garbage_name(folder):
            return folder, fio_to_display(parts), None

    return None, None, None


def human_fio_to_folder(text: str) -> str:
    """Ручной ввод админа → безопасное имя папки."""
    parts = _normalize_fio_parts(text)
    if parts:
        return fio_to_folder(parts)
    safe = re.sub(r"[^\wА-Яа-яЁё\-]+", "_", text.strip(), flags=re.UNICODE).strip("_")
    return safe or f"КТ_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


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


def looks_like_viewer_disc(root: Path) -> bool:
    """Диск с просмотрщиком: Autorun.inf, Setup, VCRedist, .exe и т.п."""
    try:
        names = {p.name.lower() for p in root.iterdir()}
    except OSError:
        return False
    if "autorun.inf" in names:
        return True
    markers = {"setup", "dotnet20", "vcredist_x86", "vcredist_x64", "nntviewer"}
    if names & markers:
        return True
    try:
        if any(p.is_file() and p.suffix.lower() == ".exe" for p in root.iterdir()):
            return True
    except OSError:
        return False
    return False


def shallow_has_ct(path: Path, max_depth: int = 4) -> bool:
    if looks_like_viewer_disc(path):
        return True
    try:
        base_depth = len(path.parts)
        for child in path.rglob("*"):
            depth = len(child.parts) - base_depth
            if depth > max_depth:
                continue
            name = child.name.lower()
            if name.endswith(".dcm") or name in {"dicomdir", "dicom"}:
                return True
            if "nntviewer" in name or name.endswith(".exe"):
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
    Всегда копируем ВЕСЬ диск/носитель.
    ФИО — подсказка (может быть пустой/технической); админ подтверждает вручную.
    """
    try:
        entries = [p for p in disc_root.iterdir() if not p.name.startswith(".")]
    except OSError:
        return None

    patient_dirs = [p for p in entries if p.is_dir() and looks_like_patient_name(p.name)]
    patient_zips = [
        p
        for p in entries
        if p.is_file() and p.suffix.lower() == ".zip" and looks_like_patient_name(p.name)
    ]
    has_ct = bool(
        patient_dirs
        or patient_zips
        or looks_like_viewer_disc(disc_root)
        or shallow_has_ct(disc_root)
    )
    if not has_ct:
        return None

    # Всегда весь носитель — на диске Setup, DotNet, exe и данные вместе
    source = disc_root

    fio_folder = display = birth = None
    for candidate in (*patient_dirs[:1], *patient_zips[:1], *entries):
        fio_folder, display, birth = _identity_from_path_name(candidate)
        if fio_folder:
            break

    if not fio_folder:
        fio_folder, display, birth = identity_from_dicom_tree(disc_root)

    if not fio_folder:
        label = volume_label(disc_root)
        if label and not is_garbage_name(label):
            fio_folder, display, birth = extract_identity_from_text(label)

    if fio_folder and is_garbage_name(fio_folder):
        fio_folder = display = None

    if birth is None:
        _, _, birth = identity_from_dicom_tree(disc_root)

    return DetectedScan(
        fio_folder=fio_folder or "",
        display_name=display or "",
        birth_date=birth,
        source=source,
    )


def parse_autorun_exe(root: Path) -> Optional[Path]:
    inf = root / "Autorun.inf"
    if not inf.exists():
        inf = root / "autorun.inf"
    if not inf.exists():
        return None
    try:
        text = inf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        try:
            text = inf.read_text(encoding="cp1251", errors="ignore")
        except OSError:
            return None
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip().lower() in {"open", "shellexecute"}:
            rel = val.strip().strip('"')
            if not rel:
                continue
            cand = root / rel
            if cand.exists() and cand.suffix.lower() == ".exe":
                return cand
    return None


def find_viewer_exe(folder: Path) -> Optional[Path]:
    """Ищет .exe просмотрщика КТ в скопированной папке."""
    if not folder.exists():
        return None

    autorun = parse_autorun_exe(folder)
    if autorun:
        return autorun

    def score(p: Path) -> tuple[int, int]:
        name = p.stem.lower()
        # чем меньше — тем лучше
        penalty = 0
        if any(s in name for s in SKIP_EXE_NAMES):
            penalty += 100
        if name in {"setup", "install"}:
            penalty += 50
        # предпочитаем корень
        depth = len(p.relative_to(folder).parts)
        try:
            size = -p.stat().st_size  # больший exe чуть предпочтительнее
        except OSError:
            size = 0
        return (penalty, depth, size)

    exes: list[Path] = []
    try:
        for p in folder.rglob("*.exe"):
            if not p.is_file():
                continue
            # не уходим слишком глубоко в redistributables
            rel = p.relative_to(folder).parts
            if any(part.lower() in {"vcredist_x86", "vcredist_x64", "dotnet20"} for part in rel[:-1]):
                continue
            exes.append(p)
    except OSError:
        pass

    if not exes:
        return None
    exes.sort(key=score)
    best = exes[0]
    if score(best)[0] >= 100 and len(exes) == 1:
        return best
    if score(best)[0] >= 100:
        # только инсталляторы — лучше открыть папку
        return None
    return best


def collect_files(source: Path) -> list[Path]:
    files: list[Path] = []
    if source.is_file():
        return [source]
    try:
        for p in source.rglob("*"):
            if p.is_file():
                files.append(p)
    except OSError:
        pass
    return files


def copy_tree_with_progress(
    source: Path,
    dest: Path,
    on_progress=None,
) -> None:
    """
    Копирует всё содержимое source → dest.
    on_progress(done, total, current_relpath)
    """
    import shutil
    import zipfile

    if source.is_file() and source.suffix.lower() == ".zip":
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as zf:
            names = zf.namelist()
            total = max(len(names), 1)
            for i, name in enumerate(names, 1):
                zf.extract(name, dest)
                if on_progress:
                    on_progress(i, total, name)
        return

    files = collect_files(source)
    total = max(len(files), 1)
    dest.mkdir(parents=True, exist_ok=True)

    is_drive_root = len(source.parts) == 1 or str(source).endswith(":\\")
    # для корня диска и обычной папки — копируем содержимое внутрь dest
    for i, src_file in enumerate(files, 1):
        try:
            rel = src_file.relative_to(source)
        except ValueError:
            rel = Path(src_file.name)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, target)
        if on_progress:
            on_progress(i, total, str(rel))
    # на всякий случай создаём пустые каталоги верхнего уровня
    if source.is_dir():
        try:
            for child in source.iterdir():
                if child.is_dir():
                    (dest / child.name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    _ = is_drive_root  # поведение одинаковое: содержимое → dest


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
