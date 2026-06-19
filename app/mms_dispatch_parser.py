"""
MMS dispatch message parser for Ketchikan tour boat assignments.

Dispatch exports often embed multiple tour rows inside a single MMS/message body:

    Eurodam — BS, BW, BWA — 06:30–10:45
    C. Spirit (Carnival Spirit) — JR, LewE — 7am-11:30am
    Koningsdam — AriC, BF, BS, BW — 11:00–15:15

Standard XML parsers that treat one message as one schedule entry miss most tours.
This module extracts every tour line from message bodies and structured XML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from app.berth_utils import merge_dispatch_codes
from app.ship_data import BUILTIN_SHIPS, _fuzzy_match_builtin, normalize_ship_name
from app.xml_cleaner import normalize_time_24h
from app.xml_parser import (
    FIELD_ALIASES,
    _extract_entry_fields,
    _normalize_tag,
    infer_reference_year,
    parse_date_from_header,
)

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DASH = r"(?:\u2014|\u2013|—|–|-)"
TIME_TOKEN = r"\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|\d{3,4}"
TIME_RANGE = rf"(?P<start>{TIME_TOKEN})\s*(?:{DASH}|to)\s*(?P<end>{TIME_TOKEN})"
TIME_AT = rf"at\s+(?P<start>{TIME_TOKEN})(?:\s*(?:{DASH}|to)\s*(?P<end>{TIME_TOKEN}))?"

# Common abbreviated ship names in Ketchikan MMS dispatch messages.
DISPATCH_SHIP_ALIASES: dict[str, str] = {
    "c spirit": "Carnival Spirit",
    "c miracle": "Carnival Miracle",
    "c luminosa": "Carnival Luminosa",
    "c legend": "Carnival Legend",
    "c freedom": "Carnival Freedom",
    "c sensation": "Carnival Sensation",
    "c valor": "Carnival Valor",
    "c dream": "Carnival Dream",
    "c breeze": "Carnival Breeze",
    "c magic": "Carnival Magic",
    "c elation": "Carnival Elation",
    "c paradise": "Carnival Paradise",
    "c conquest": "Carnival Conquest",
    "c glory": "Carnival Glory",
    "c liberty": "Carnival Liberty",
    "c pride": "Carnival Pride",
    "c sunshine": "Carnival Sunshine",
    "c horizon": "Carnival Horizon",
    "c vista": "Carnival Vista",
    "c panorama": "Carnival Panorama",
    "c celebration": "Carnival Celebration",
    "c jubilee": "Carnival Jubilee",
    "c mardi gras": "Carnival Mardi Gras",
    "c splendor": "Carnival Splendor",
    "c radiance": "Carnival Radiance",
    "c spirit ii": "Carnival Spirit",
}

TOUR_LINE_PATTERNS: list[re.Pattern[str]] = [
    # [date] Ship — boats — 7am-11:30am / 06:30–10:45
    re.compile(
        rf"^\s*"
        rf"(?:(?P<month>\d{{1,2}})/(?P<day>\d{{1,2}})\s+)?"
        rf"(?P<ship>.+?)\s*{DASH}\s*"
        rf"(?P<boats>.+?)\s*{DASH}\s*"
        rf"{TIME_RANGE}\s*$",
        re.IGNORECASE,
    ),
    # Ship — boats at 7am-11:30am
    re.compile(
        rf"^\s*"
        rf"(?:(?P<month>\d{{1,2}})/(?P<day>\d{{1,2}})\s+)?"
        rf"(?P<ship>.+?)\s*{DASH}\s*"
        rf"(?P<boats>.+?)\s+{TIME_AT}\s*$",
        re.IGNORECASE,
    ),
    # [date] Ship boats 7am-11:30am (no em-dash separators)
    re.compile(
        rf"^\s*"
        rf"(?:(?P<month>\d{{1,2}})/(?P<day>\d{{1,2}})\s+)?"
        rf"(?P<ship>[A-Za-z][A-Za-z0-9.\s&()]+?)\s+"
        rf"(?P<boats>(?:[A-Za-z][A-Za-z0-9]*(?:\s*,\s*[A-Za-z][A-Za-z0-9]*)+|50/50))\s+"
        rf"{TIME_RANGE}\s*$",
        re.IGNORECASE,
    ),
]

INLINE_DATE_PATTERN = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*"
    r"(?P<month>\d{1,2})/(?P<day>\d{1,2})",
    re.IGNORECASE,
)

MESSAGE_TAGS = {"message", "msg", "sms", "mms", "dispatch_message"}
BODY_FIELD_TAGS = {"body", "text", "content", "message_text", "payload", "data", "raw_text"}
DATE_FIELD_TAGS = {"date", "sent_date", "schedule_date", "dispatch_date", "message_date"}
STRUCTURED_TOUR_TAGS = {"tour", "dispatch_tour", "assignment", "dispatch", "schedule", "entry", "row"}


def looks_like_mms_dispatch(content: bytes | str) -> bool:
    """Return True when content likely contains MMS/text dispatch tour lines."""
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content
    lower = text.lower()
    if "<message" in lower or "<mms" in lower or "mms dispatch" in lower:
        return True
    return _count_tour_like_lines(text) >= 2


def _count_tour_like_lines(text: str) -> int:
    count = 0
    for line in text.splitlines():
        if _match_tour_line(line.strip()):
            count += 1
    return count


def _alias_key(name: str) -> str:
    return re.sub(r"[.\"']", "", normalize_ship_name(name))


def _clean_ship_text(raw: str) -> str:
    text = raw.strip()
    paren = re.match(r"^(?P<short>.+?)\s*\((?P<full>[^)]+)\)\s*$", text)
    if paren:
        return paren.group("full").strip()
    return text


def resolve_dispatch_ship_name(name: str) -> str:
    """Expand abbreviated dispatch ship names when uniquely identifiable."""
    text = _clean_ship_text(name.strip())
    alias = _alias_key(text)
    if alias in DISPATCH_SHIP_ALIASES:
        return DISPATCH_SHIP_ALIASES[alias]

    for key, full_name in DISPATCH_SHIP_ALIASES.items():
        if alias == key or alias.startswith(key + " "):
            return full_name

    normalized = normalize_ship_name(text)
    if normalized in BUILTIN_SHIPS:
        return _title_ship(normalized)

    prefix_matches = [
        key
        for key in BUILTIN_SHIPS
        if key == normalized
        or key.startswith(normalized + " ")
        or key.split()[0] == normalized
        or normalized in key.split()
    ]
    if len(prefix_matches) == 1:
        return _title_ship(prefix_matches[0])

    fuzzy = _fuzzy_match_builtin(text)
    if fuzzy:
        return _title_ship(fuzzy[0])
    return text


def _title_ship(normalized_key: str) -> str:
    return " ".join(word.capitalize() for word in normalized_key.split())


def _parse_inline_date(text: str, reference_year: int) -> date | None:
    match = INLINE_DATE_PATTERN.search(text)
    if not match:
        return None
    month, day = int(match.group("month")), int(match.group("day"))
    try:
        return date(reference_year, month, day)
    except ValueError:
        return None


def _build_date_header(schedule_date: date) -> str:
    return f"{DAY_NAMES[schedule_date.weekday()]} {schedule_date.month}/{schedule_date.day}"


def _normalize_single_time(value: str) -> str | None:
    normalized, _ = normalize_time_24h(value.strip())
    return normalized


def _normalize_tour_times(start: str, end: str | None) -> tuple[str | None, str | None, str | None]:
    checkin = _normalize_single_time(start)
    if not checkin:
        return None, None, f"Unrecognized start time: '{start}'"

    if end:
        return_time = _normalize_single_time(end)
        if not return_time:
            return None, None, f"Unrecognized end time: '{end}'"
        return checkin, return_time, None

    # Single check-in time only — assume a 4-hour tour window when end is omitted.
    hour, minute = map(int, checkin.split(":"))
    end_dt = datetime(2000, 1, 1, hour, minute) + timedelta(hours=4)
    return checkin, end_dt.strftime("%H:%M"), None


def _match_tour_line(line: str) -> re.Match[str] | None:
    for pattern in TOUR_LINE_PATTERNS:
        match = pattern.match(line)
        if match:
            return match
    return None


def _looks_like_date_header(line: str) -> bool:
    if _match_tour_line(line):
        return False
    if parse_date_from_header(line, 2026)[0]:
        return True
    if _parse_inline_date(line, 2026) and line.count("—") + line.count("–") + line.count("-") < 2:
        return True
    return False


def _row_from_tour_match(
    match: re.Match[str],
    *,
    current_date: date | None,
    reference_year: int,
    fixed_date: date | None = None,
) -> tuple[dict | None, str | None]:
    if fixed_date is not None:
        schedule_date = fixed_date
    else:
        month = match.groupdict().get("month")
        day = match.groupdict().get("day")
        schedule_date = current_date
        if month and day:
            try:
                schedule_date = date(reference_year, int(month), int(day))
            except ValueError:
                return None, f"invalid date {month}/{day}"
        elif schedule_date is None:
            return None, "tour line missing date"

    start = match.group("start")
    end = match.groupdict().get("end")
    checkin, return_time, time_err = _normalize_tour_times(start, end)
    if time_err or not checkin or not return_time:
        return None, time_err or "invalid times"

    ship = resolve_dispatch_ship_name(match.group("ship"))
    boats_raw = match.group("boats").strip().strip(",")

    return (
        {
            "date_header": _build_date_header(schedule_date),
            "schedule_date": schedule_date,
            "ship": ship,
            "checkin_time": checkin,
            "return_time": return_time,
            "boat_codes": boats_raw,
            "ship_count": None,
        },
        None,
    )


def parse_tour_lines_from_text(
    text: str,
    reference_year: int,
    *,
    default_date: date | None = None,
    fixed_date: date | None = None,
) -> tuple[list[dict], list[str]]:
    """Parse free-text or message-body dispatch lines into schedule row dicts."""
    errors: list[str] = []
    rows: list[dict] = []
    current_date = fixed_date or default_date

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("<!--"):
            continue

        if fixed_date is None and _looks_like_date_header(line):
            header_date, _ = parse_date_from_header(line, reference_year)
            if header_date:
                current_date = header_date
                continue
            inline = _parse_inline_date(line, reference_year)
            if inline:
                current_date = inline
                continue

        match = _match_tour_line(line)
        if not match:
            if re.search(TIME_TOKEN, line, re.I) and any(ch in line for ch in ("—", "–", "-", " at ")):
                errors.append(f"Line {line_no}: could not parse tour dispatch line: {line[:100]}")
            continue

        row, err = _row_from_tour_match(
            match,
            current_date=current_date,
            reference_year=reference_year,
            fixed_date=fixed_date,
        )
        if err:
            errors.append(f"Line {line_no}: {err}")
            continue
        if row:
            rows.append(row)

    return rows, errors


def _element_full_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tail:
            parts.append(child.tail)
    return "\n".join(p for p in parts if p and p.strip())


def _collect_message_blocks(root: ET.Element) -> list[tuple[str | None, str]]:
    """Return (optional_date_text, body_text) for each MMS/dispatch message."""
    blocks: list[tuple[str | None, str]] = []
    seen: set[str] = set()

    for elem in root.iter():
        tag = _normalize_tag(elem.tag)
        if tag not in MESSAGE_TAGS:
            continue

        date_text: str | None = None
        body_parts: list[str] = []

        for child in elem:
            child_tag = _normalize_tag(child.tag)
            value = (child.text or "").strip()
            if not value:
                value = _element_full_text(child).strip()
            if not value:
                continue
            if child_tag in DATE_FIELD_TAGS or child_tag in FIELD_ALIASES.get("schedule_date", []):
                date_text = value
            elif child_tag in BODY_FIELD_TAGS or child_tag in STRUCTURED_TOUR_TAGS:
                body_parts.append(value)

        if not body_parts:
            direct = (elem.text or "").strip()
            if direct:
                body_parts.append(direct)

        body = "\n".join(body_parts).strip()
        if body and body not in seen:
            seen.add(body)
            blocks.append((date_text, body))

    if blocks:
        return blocks

    for elem in root.iter():
        tag = _normalize_tag(elem.tag)
        if tag in BODY_FIELD_TAGS:
            body = (elem.text or "").strip()
            if body and body not in seen:
                seen.add(body)
                blocks.append((None, body))

    return blocks


def _extract_structured_tour_elements(root: ET.Element, reference_year: int) -> list[dict]:
    """Parse nested tour/dispatch elements that already have ship/time fields."""
    rows: list[dict] = []
    for elem in root.iter():
        tag = _normalize_tag(elem.tag)
        if tag not in STRUCTURED_TOUR_TAGS:
            continue
        fields = _extract_entry_fields(elem)
        ship = fields.get("ship", "").strip()
        checkin = fields.get("checkin_time", "").strip()
        ret = fields.get("return_time", "").strip()
        boats = fields.get("boat_codes", "").strip()
        if not ship or not checkin or not boats:
            continue

        date_header = fields.get("date_header", "")
        schedule_date, ship_count = parse_date_from_header(date_header, reference_year)
        if schedule_date is None:
            schedule_date = _parse_inline_date(date_header, reference_year)
        if schedule_date is None:
            continue

        checkin_norm, return_norm, _ = _normalize_tour_times(checkin, ret or None)
        if not checkin_norm or not return_norm:
            continue

        rows.append(
            {
                "date_header": date_header or _build_date_header(schedule_date),
                "schedule_date": schedule_date,
                "ship": resolve_dispatch_ship_name(ship),
                "checkin_time": checkin_norm,
                "return_time": return_norm,
                "boat_codes": boats,
                "ship_count": ship_count,
            }
        )
    return rows


def extract_mms_dispatch_rows(content: bytes | str) -> tuple[list[dict], list[str]]:
    """
    Extract all tour dispatch rows from MMS/XML dispatch content.

    Returns row dicts compatible with parse_xml_content / persist_schedule_rows.
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content

    errors: list[str] = []
    all_rows: list[dict] = []

    pseudo_headers = [{"date_header": line} for line in text.splitlines() if "/" in line][:50]
    reference_year = infer_reference_year(pseudo_headers) if pseudo_headers else date.today().year

    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(content, str) else content)
        for date_text, body in _collect_message_blocks(root):
            default_date = None
            if date_text:
                default_date, _ = parse_date_from_header(date_text, reference_year)
                if default_date is None:
                    default_date = _parse_inline_date(date_text, reference_year)
            body_rows, body_errors = parse_tour_lines_from_text(
                body, reference_year, default_date=default_date
            )
            all_rows.extend(body_rows)
            errors.extend(body_errors)

        all_rows.extend(_extract_structured_tour_elements(root, reference_year))
    except ET.ParseError:
        pass

    text_rows, text_errors = parse_tour_lines_from_text(text, reference_year)
    errors.extend(text_errors)

    seen_keys = {
        (r["schedule_date"], r["ship"].lower(), r["checkin_time"], r["return_time"], r["boat_codes"])
        for r in all_rows
    }
    for row in text_rows:
        key = (
            row["schedule_date"],
            row["ship"].lower(),
            row["checkin_time"],
            row["return_time"],
            row["boat_codes"],
        )
        if key not in seen_keys:
            seen_keys.add(key)
            all_rows.append(row)

    return all_rows, errors


def merge_dispatch_row_sets(
    primary: list[dict],
    dispatch_rows: list[dict],
) -> list[dict]:
    """
    Merge standard port-schedule rows with MMS dispatch tour rows.

    Matching rows (same date, ship, times) merge boat_codes. Dispatch-only rows
    are appended.
    """
    if not dispatch_rows:
        return primary

    index: dict[tuple, dict] = {}
    merged = list(primary)

    for row in primary:
        key = (
            row["schedule_date"],
            row["ship"].lower(),
            row["checkin_time"],
            row["return_time"],
        )
        index[key] = row

    for row in dispatch_rows:
        key = (
            row["schedule_date"],
            row["ship"].lower(),
            row["checkin_time"],
            row["return_time"],
        )
        if key in index:
            existing = index[key]
            boats = merge_dispatch_codes(existing.get("boat_codes", ""), row.get("boat_codes", ""))
            if boats:
                existing["boat_codes"] = boats
        else:
            merged.append(row)
            index[key] = row

    return merged
