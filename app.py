import os
import re
import socket
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pymysql
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pymysql.cursors import DictCursor

BASE_DIR = Path(os.getenv("IDENT_BRIDGE_DIR", r"C:\ident-bridge"))
load_dotenv(BASE_DIR / ".env", override=True)

app = FastAPI(title="IDENT <-> Yeastar S20 bridge")


def normalize_phone(value: Optional[str]) -> Optional[str]:
    """Приводит номер к виду, который ждёт IDENT: +7XXXXXXXXXX."""
    if not value:
        return None

    v = str(value).strip()
    if not v:
        return None
    if v.lower() in {"unknown", "<unknown>", "null", "s", "anonymous"}:
        return None

    digits = re.sub(r"\D+", "", v)
    if not digits:
        return None

    # Внутренние 3–4 знака оставляем как есть (1000, 200 и т.п.)
    if re.fullmatch(r"\d{3,4}", digits):
        return digits

    # Российские мобильные/городские: 8XXXXXXXXXX / 7XXXXXXXXXX → +7XXXXXXXXXX
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return f"+7{digits[1:]}"

    # Уже международный вид без плюса: 7491... (11+) или короткий DID
    if len(digits) >= 10:
        return f"+{digits}"

    return digits


IDENT_KEY = os.getenv("IDENT_KEY", "").strip()
SERVICE_TZ = ZoneInfo(os.getenv("SERVICE_TIMEZONE", "Europe/Moscow"))

YEASTAR_HOST = os.getenv("YEASTAR_HOST", "").strip()

AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USER = os.getenv("AMI_USER", "").strip()
AMI_PASS = os.getenv("AMI_PASS", "").strip()

MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "").strip()
MYSQL_PASS = os.getenv("MYSQL_PASS", "").strip()
MYSQL_DB = os.getenv("MYSQL_DB", "").strip()

INTERNAL_EXTENSIONS_REGEX = re.compile(
    os.getenv("INTERNAL_EXTENSIONS_REGEX", r"^\d{3,4}$")
)

CLINIC_PHONE = normalize_phone(os.getenv("CLINIC_PHONE", "").strip()) if os.getenv("CLINIC_PHONE") else None

# Формат:
# TRUNK_LINE_MAP=domru-770505:+74912770505,domru-205822:+74912205822
TRUNK_LINE_MAP_RAW = os.getenv("TRUNK_LINE_MAP", "").strip()

RECORDINGS_ROOT_RAW = os.getenv("RECORDINGS_ROOT", "").strip()
RECORDINGS_ROOT = Path(RECORDINGS_ROOT_RAW) if RECORDINGS_ROOT_RAW else None
RECORD_PUBLIC_BASE_URL = os.getenv("RECORD_PUBLIC_BASE_URL", "").strip().rstrip("/")


def parse_trunk_line_map(raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not raw:
        return result

    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        trunk_name, phone = chunk.split(":", 1)
        trunk_name = trunk_name.strip()
        phone = normalize_phone(phone.strip())
        if trunk_name and phone:
            result[trunk_name.lower()] = phone
    return result


TRUNK_LINE_MAP = parse_trunk_line_map(TRUNK_LINE_MAP_RAW)


def require_ident_key(header_value: Optional[str]) -> None:
    if not header_value or header_value != IDENT_KEY:
        raise HTTPException(status_code=403, detail="Invalid IDENT key")


def is_internal_number(value: Optional[str]) -> bool:
    if not value:
        return False
    pure = re.sub(r"\D+", "", str(value))
    return bool(INTERNAL_EXTENSIONS_REGEX.fullmatch(pure))


def localize_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SERVICE_TZ)
    return dt.astimezone(SERVICE_TZ)


def parse_ident_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SERVICE_TZ)
    return dt.astimezone(SERVICE_TZ)


def normalize_record_relpath(recordpath: Optional[str]) -> Optional[str]:
    if not recordpath:
        return None

    rp = str(recordpath).strip().replace("\\", "/")
    rp = rp.lstrip("/")
    low = rp.lower()

    markers = [
        "/mmc1/autorecords/",
        "mmc1/autorecords/",
        "/autorecords/",
        "autorecords/",
    ]

    for marker in markers:
        if marker in low:
            idx = low.index(marker) + len(marker)
            return rp[idx:].lstrip("/")

    return rp


def build_record_url(recordpath: Optional[str]) -> Optional[str]:
    rel = normalize_record_relpath(recordpath)
    if not rel or not RECORD_PUBLIC_BASE_URL:
        return None

    rel = rel.replace("\\", "/").lstrip("/")
    return f"{RECORD_PUBLIC_BASE_URL}/records/{quote(rel, safe='/')}"


def map_trunk_to_phone(trunk_name: Optional[str]) -> Optional[str]:
    if not trunk_name:
        return None
    return TRUNK_LINE_MAP.get(str(trunk_name).strip().lower())


def mysql_server_conn():
    return pymysql.connect(
        host=YEASTAR_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        charset="utf8",
        autocommit=True,
    )


def mysql_db_conn(database: str):
    return pymysql.connect(
        host=YEASTAR_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=database,
        charset="utf8",
        autocommit=True,
    )


def get_tables_in_db(conn, db_name: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"SHOW TABLES FROM `{db_name}` LIKE 'cdr%'")
    tables = [row[0] for row in cur.fetchall()]
    cur.close()

    monthly_tables = sorted(
        [t for t in tables if re.fullmatch(r"cdr_\d{6}", t.lower())]
    )
    if monthly_tables:
        return monthly_tables

    base_tables = sorted([t for t in tables if t.lower() == "cdr"])
    return base_tables


def discover_cdr_db_and_tables() -> Tuple[str, List[str]]:
    if MYSQL_DB:
        conn = mysql_server_conn()
        try:
            tables = get_tables_in_db(conn, MYSQL_DB)
            if not tables:
                raise RuntimeError(f"В базе {MYSQL_DB} не найдено таблиц cdr%")
            return MYSQL_DB, tables
        finally:
            conn.close()

    conn = mysql_server_conn()
    try:
        cur = conn.cursor()
        cur.execute("SHOW DATABASES")
        dbs = [row[0] for row in cur.fetchall()]
        cur.close()

        for db_name in dbs:
            tables = get_tables_in_db(conn, db_name)
            if tables:
                return db_name, tables

        raise RuntimeError("Не удалось найти БД с таблицами cdr%")
    finally:
        conn.close()


def build_finished_sql(db_name: str, tables: List[str]) -> str:
    parts = []
    for table in tables:
        parts.append(
            f"""
            SELECT
                datetime,
                src,
                dst,
                srctrunk,
                dstrunk,
                didnumber,
                extfield2,
                extfield3,
                extfield5,
                duration,
                billable,
                disposition,
                calltype,
                recordpath
            FROM `{db_name}`.`{table}`
            WHERE datetime >= %s AND datetime <= %s
            """
        )

    inner = " UNION ALL ".join(parts)
    return f"""
        SELECT *
        FROM ({inner}) AS all_cdr
        ORDER BY datetime ASC
        LIMIT %s OFFSET %s
    """


def choose_inbound_clinic_number(row: Dict, dst_number: Optional[str]) -> Optional[str]:
    srctrunk_phone = map_trunk_to_phone(row.get("srctrunk"))
    did_number = normalize_phone(row.get("didnumber"))
    return srctrunk_phone or did_number or CLINIC_PHONE or dst_number


def choose_outbound_clinic_number(row: Dict, src_number: Optional[str]) -> Optional[str]:
    dod_number = normalize_phone(row.get("extfield3"))
    dstrunk_phone = map_trunk_to_phone(row.get("dstrunk"))
    return dod_number or dstrunk_phone or CLINIC_PHONE or src_number


def cdr_row_to_ident(row: Dict) -> Optional[Dict]:
    calltype = (row.get("calltype") or "").strip().lower()

    src_number = normalize_phone(row.get("src"))
    dst_number = normalize_phone(row.get("dst"))
    dialed_external = normalize_phone(row.get("extfield5"))

    line_description = None
    if is_internal_number(src_number):
        line_description = src_number
    elif row.get("extfield2"):
        line_description = str(row.get("extfield2")).strip() or None

    if calltype == "inbound":
        direction = "in"
        phone_from = src_number
        phone_to = choose_inbound_clinic_number(row, dst_number)

    elif calltype == "outbound":
        direction = "out"
        phone_from = choose_outbound_clinic_number(row, src_number)
        phone_to = dialed_external or dst_number

    else:
        return None

    started = localize_dt(row["datetime"])
    duration = int(row.get("duration") or 0)
    billable = int(row.get("billable") or 0)

    wait_seconds = max(duration - billable, 0)
    talk_seconds = billable if billable > 0 else None

    return {
        "DateAndTime": started.isoformat(timespec="seconds"),
        "Direction": direction,
        "PhoneFrom": phone_from,
        "PhoneTo": phone_to,
        "WaitInSeconds": wait_seconds,
        "TalkInSeconds": talk_seconds,
        "LineDescription": line_description,
        "RecordUrl": build_record_url(row.get("recordpath")),
    }


def ami_send(sock: socket.socket, payload: str) -> None:
    sock.sendall(payload.encode())


def ami_read_messages(
    sock: socket.socket,
    stop_event: Optional[str] = None,
    action_id: Optional[str] = None,
    deadline_seconds: float = 5.0,
) -> List[Dict]:
    messages: List[Dict] = []
    buffer = ""
    deadline = time.time() + deadline_seconds

    while time.time() < deadline:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            break

        if not data:
            break

        buffer += data.decode(errors="ignore")

        while "\r\n\r\n" in buffer:
            raw, buffer = buffer.split("\r\n\r\n", 1)
            msg: Dict[str, str] = {}

            for line in raw.split("\r\n"):
                if ": " in line:
                    k, v = line.split(": ", 1)
                    msg[k] = v

            if msg:
                messages.append(msg)

                if stop_event and msg.get("Event") == stop_event:
                    if action_id is None or msg.get("ActionID") == action_id:
                        return messages

                if not stop_event and "Response" in msg:
                    return messages

    return messages


def detect_trunk_line_from_items(items: List[Dict]) -> Optional[str]:
    if not TRUNK_LINE_MAP:
        return None

    searchable_fields = [
        "Channel",
        "BridgedChannel",
        "DestChannel",
        "CallerIDName",
        "ConnectedLineName",
    ]

    haystacks = []
    for item in items:
        for field in searchable_fields:
            value = item.get(field)
            if value:
                haystacks.append(str(value).lower())

    for trunk_name, phone in TRUNK_LINE_MAP.items():
        for hay in haystacks:
            if trunk_name in hay:
                return phone

    return None


def ami_status() -> List[Dict]:
    action_id = f"ident-{uuid.uuid4()}"
    sock = socket.create_connection((YEASTAR_HOST, AMI_PORT), timeout=5)
    sock.settimeout(2)

    try:
        try:
            sock.recv(1024)
        except socket.timeout:
            pass

        ami_send(
            sock,
            (
                "Action: Login\r\n"
                f"Username: {AMI_USER}\r\n"
                f"Secret: {AMI_PASS}\r\n"
                "Events: off\r\n"
                "\r\n"
            ),
        )
        _ = ami_read_messages(sock, deadline_seconds=1.5)

        ami_send(
            sock,
            (
                "Action: Status\r\n"
                f"ActionID: {action_id}\r\n"
                "\r\n"
            ),
        )

        messages = ami_read_messages(
            sock,
            stop_event="StatusComplete",
            action_id=action_id,
            deadline_seconds=5.0,
        )

        try:
            ami_send(sock, "Action: Logoff\r\n\r\n")
        except Exception:
            pass

        return [m for m in messages if m.get("Event") == "Status"]
    finally:
        sock.close()


def clinic_phone_set() -> set:
    phones = set()
    if CLINIC_PHONE:
        phones.add(CLINIC_PHONE)
    phones.update(TRUNK_LINE_MAP.values())
    return phones


CHANNEL_EXT_RE = re.compile(r"^(?:PJSIP|SIP|IAX2)/(\d{3,4})-", re.IGNORECASE)

# Стабильный DateAndTime по Linkedid, иначе Ident каждый опрос видит «новый» звонок
_ONGOING_STARTED: Dict[str, datetime] = {}


def channel_extension(channel: Optional[str]) -> Optional[str]:
    if not channel:
        return None
    m = CHANNEL_EXT_RE.match(channel.strip())
    return m.group(1) if m else None


def channel_is_trunk(channel: Optional[str]) -> bool:
    if not channel:
        return False
    ch = channel.lower()
    if channel.startswith("Local/"):
        return False
    if "trunk" in ch:
        return True
    for trunk_name in TRUNK_LINE_MAP:
        if trunk_name in ch:
            return True
    # Yeastar часто: SIP/domru-770505-0000xxxx
    if CHANNEL_EXT_RE.match(channel):
        return False
    if ch.startswith(("sip/", "pjsip/", "iax2/")):
        return True
    return False


def detect_direction(items: List[Dict], external: str, internal: Optional[str]) -> str:
    """
    Важно для окна «Входящие звонки» в IDENT: нужен Direction=in.

    После ответа AMI на канале внутреннего часто:
      CallerIDNum=<внутренний>, ConnectedLineNum=<клиент>
    Старая эвристика ошибочно считала это исходящим — поэтому в UI
    текущий входящий не показывался, а в истории (CDR) появлялся постфактум.
    """
    trunk_items = [i for i in items if channel_is_trunk(i.get("Channel"))]
    ext_items = [i for i in items if channel_extension(i.get("Channel"))]

    for item in trunk_items:
        caller = normalize_phone(item.get("CallerIDNum"))
        if caller == external:
            return "in"

    # Только канал внутреннего, без транка в Status: смотрим, кто «звонящий»
    for item in ext_items:
        caller = normalize_phone(item.get("CallerIDNum"))
        connected = normalize_phone(item.get("ConnectedLineNum"))
        state = (item.get("ChannelStateDesc") or "").lower()
        # Входящий на аппарат: на канале 1000 CallerID часто уже номер клиента
        if caller == external:
            return "in"
        # Исходящий: внутренний набрал внешний
        if internal and caller == internal and connected == external:
            return "out"
        if state in {"ring", "ringing"} and connected == external:
            return "in"

    if trunk_items and not ext_items:
        return "in"
    if ext_items and not trunk_items:
        # Нет транка в снимке — по умолчанию исходящий только если caller=internal
        for item in ext_items:
            caller = normalize_phone(item.get("CallerIDNum"))
            if internal and caller == internal:
                return "out"
        return "in"

    # Есть и транк, и внутренний: если на транке caller не external — скорее out
    if trunk_items and ext_items:
        return "out"

    return "in"


def status_rows_to_ongoing(rows: List[Dict]) -> List[Dict]:
    by_linked: Dict[str, List[Dict]] = defaultdict(list)
    clinic_phones = clinic_phone_set()

    for row in rows:
        channel = row.get("Channel", "")
        if not channel or channel.startswith("Local/"):
            continue

        linked = (
            row.get("Linkedid")
            or row.get("LinkedID")
            or row.get("Uniqueid")
            or row.get("UniqueID")
            or channel
        )
        by_linked[linked].append(row)

    result = []
    now = datetime.now(SERVICE_TZ)
    seen_keys = set()

    for linked, items in by_linked.items():
        numbers = []
        for item in items:
            for field in (
                "CallerIDNum",
                "ConnectedLineNum",
                "EffectiveConnectedLineNum",
                "Exten",
            ):
                num = normalize_phone(item.get(field))
                if num:
                    numbers.append(num)

            ext_from_ch = channel_extension(item.get("Channel"))
            if ext_from_ch:
                numbers.append(ext_from_ch)

        numbers = list(dict.fromkeys(numbers))
        if not numbers:
            continue

        internal_list = [n for n in numbers if is_internal_number(n)]
        external = [
            n
            for n in numbers
            if not is_internal_number(n) and n not in clinic_phones
        ]
        if not external:
            continue

        client_num = external[0]
        int_num = internal_list[0] if internal_list else None
        # Предпочитаем внутренний из имени канала (PJSIP/1000-...)
        for item in items:
            ch_ext = channel_extension(item.get("Channel"))
            if ch_ext:
                int_num = ch_ext
                break

        trunk_line = detect_trunk_line_from_items(items)
        direction = detect_direction(items, client_num, int_num)

        max_seconds = 0
        any_up = False
        ringing = False
        for item in items:
            try:
                max_seconds = max(max_seconds, int(item.get("Seconds") or 0))
            except ValueError:
                pass

            state = (item.get("ChannelStateDesc") or "").lower()
            if state == "up":
                any_up = True
            if state in {"ring", "ringing"}:
                ringing = True

        cache_key = f"{linked}:{client_num}:{direction}"
        seen_keys.add(cache_key)
        if cache_key in _ONGOING_STARTED:
            started = _ONGOING_STARTED[cache_key]
        else:
            started = now - timedelta(seconds=max_seconds)
            _ONGOING_STARTED[cache_key] = started

        if direction == "in":
            phone_from = client_num
            phone_to = trunk_line or CLINIC_PHONE or client_num
        else:
            phone_from = trunk_line or CLINIC_PHONE or client_num
            phone_to = client_num

        # Пока гудки — разговора ещё нет (TalkInSeconds=null), иначе Ident
        # может считать вызов уже «завершённым по смыслу» для окна текущих.
        talk = None
        if any_up and not ringing:
            talk = max_seconds

        result.append(
            {
                "DateAndTime": started.isoformat(timespec="seconds"),
                "Direction": direction,
                "PhoneFrom": phone_from,
                "PhoneTo": phone_to,
                "WaitInSeconds": 0 if any_up else max_seconds,
                "TalkInSeconds": talk,
                "LineDescription": int_num,
                "RecordUrl": None,
            }
        )

    # Чистим кэш стартов для завершённых
    for key in list(_ONGOING_STARTED.keys()):
        if key not in seen_keys:
            _ONGOING_STARTED.pop(key, None)

    result.sort(key=lambda x: x["DateAndTime"])
    return result


@app.get("/records/{record_rel_path:path}")
def get_record(record_rel_path: str):
    if not RECORDINGS_ROOT:
        raise HTTPException(status_code=404, detail="Recordings root is not configured")

    rel = record_rel_path.replace("\\", "/").lstrip("/")
    rel_path = Path(rel)
    file_path = RECORDINGS_ROOT / rel_path

    if not file_path.exists() or not file_path.is_file():
        file_name = rel_path.name
        try:
            matches = list(RECORDINGS_ROOT.rglob(file_name))
        except Exception:
            matches = []

        if matches:
            file_path = matches[0]
        else:
            raise HTTPException(status_code=404, detail="Recording not found")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/GetFinishedCalls")
def get_finished_calls(
    dateTimeFrom: str = Query(...),
    dateTimeTo: str = Query(...),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    ident_integration_key: Optional[str] = Header(default=None, alias="IDENT-Integration-Key"),
):
    require_ident_key(ident_integration_key)

    dt_from = parse_ident_dt(dateTimeFrom).replace(tzinfo=None)
    dt_to = parse_ident_dt(dateTimeTo).replace(tzinfo=None)

    db_name, tables = discover_cdr_db_and_tables()
    if not tables:
        return []

    conn = mysql_db_conn(db_name)
    try:
        sql = build_finished_sql(db_name, tables)
        params = []
        for _ in tables:
            params.extend([dt_from, dt_to])
        params.extend([limit, offset])

        cur = conn.cursor(DictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()

        result = []
        for row in rows:
            item = cdr_row_to_ident(row)
            if item:
                result.append(item)
        return result
    finally:
        conn.close()


@app.get("/GetOngoingCalls")
def get_ongoing_calls(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    ident_integration_key: Optional[str] = Header(default=None, alias="IDENT-Integration-Key"),
):
    require_ident_key(ident_integration_key)

    try:
        rows = ami_status()
    except Exception as exc:
        # Не роняем IDENT пустым 500 — пишем причину в лог uvicorn
        print(f"[GetOngoingCalls] AMI error: {exc!r}")
        return []

    items = status_rows_to_ongoing(rows)
    if rows and not items:
        channels = [
            f"{r.get('Channel')}|{r.get('ChannelStateDesc')}|cid={r.get('CallerIDNum')}|cn={r.get('ConnectedLineNum')}"
            for r in rows[:8]
        ]
        print("[GetOngoingCalls] empty after parse; raw=" + " ; ".join(channels))
    if items:
        print(
            "[GetOngoingCalls] "
            + ", ".join(
                f"{i.get('Direction')} {i.get('PhoneFrom')}->{i.get('PhoneTo')} ext={i.get('LineDescription')} talk={i.get('TalkInSeconds')}"
                for i in items
            )
        )
    return items[offset: offset + limit]
