"""
CSV schedule import pipeline.

Accepts cruise port / dispatch schedule CSV exports with flexible column names.
Typical columns: date, ship, arrival/check-in, departure/return, berth/boat code.

Example:

    date,ship,arrival,departure,berth
    2026-06-04,Norwegian Bliss,6:00 AM,1:15 PM,WW
    2026-06-04,Eurodam,6:30 AM,1:00 PM,1
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.berth_utils import is_captain_boat_code, looks_like_berth_code, normalize_berth, repair_boat_berth_value
from app.schedule_import import clear_schedule_data, persist_schedule_rows
from app.xml_cleaner import normalize_time_24h
from app.xml_parser import (
    DATE_HEADER_PATTERN,
    FIELD_ALIASES,
    infer_reference_year,
    parse_date_from_header,
)

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Extra CSV column aliases beyond the XML field aliases.
CSV_COLUMN_ALIASES: dict[str, list[str]] = {
    **FIELD_ALIASES,
    "date_header": [
        "date_header",
        "dispatch_date",
        "header",
        "header_line",
        "dispatch_header",
    ],
    "schedule_date": [
        "schedule_date",
        "date",
        "port_date",
        "arrival_date",
        "call_date",
        "day_date",
    ],
    "ship": [
        *FIELD_ALIASES["ship"],
        "ship_name",
        "vessel_name",
        "cruise_ship_name",
    ],
    "checkin_time": [
        *FIELD_ALIASES["checkin_time"],
        "arrival",
        "arrival_time",
        "toa",
        "time_of_arrival",
        "check_in",
        "checkin",
        "start",
    ],
    "return_time": [
        *FIELD_ALIASES["return_time"],
        "departure",
        "departure_time",
        "tod",
        "time_of_departure",
        "check_out",
        "return",
        "end",
    ],
    "boat_codes": [
        "boat_codes",
        "boat_code",
        "boat",
        "boats",
        "captain",
        "captain_code",
        "tour_boat",
        "tour_boat_code",
        "operator",
        "operator_code",
        "dispatch_boat",
        "dispatch",
    ],
    "berth": [
        "berth",
        "berth_code",
        "berth_location",
        "dock",
        "pier",
        "dock_code",
        "port_berth",
    ],
}

DATE_VALUE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m-%d-%y",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%d %H:%M:%S",
)


def _normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _resolve_csv_column(header: str) -> str | None:
    """
    Map a CSV header to a canonical field.

    When a header could match multiple fields, prefer tour-boat columns over
    port-berth columns (e.g. 'boat' → boat_codes, not berth).
    """
    normalized = _normalize_header(header)

    # Explicit tour-boat headers first — never treat 'boat' as a berth column.
    boat_headers = {_normalize_header(a) for a in CSV_COLUMN_ALIASES["boat_codes"]}
    if normalized in boat_headers:
        return "boat_codes"

    berth_headers = {_normalize_header(a) for a in CSV_COLUMN_ALIASES["berth"]}
    if normalized in berth_headers:
        return "berth"

    for field, aliases in CSV_COLUMN_ALIASES.items():
        if field in ("boat_codes", "berth"):
            continue
        alias_norms = {_normalize_header(a) for a in aliases}
        if normalized in alias_norms or normalized == field:
            return field
    return None


def _decode_csv_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_date_value(value: str, reference_year: int) -> date | None:
    text = value.strip()
    if not text:
        return None

    # ISO date portion when datetime is included.
    if " " in text:
        text = text.split(" ", 1)[0].strip()

    for fmt in DATE_VALUE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # Month/day without year: 6/4 or 6-4
    match = re.match(r"^(\d{1,2})[/-](\d{1,2})$", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        try:
            return date(reference_year, month, day)
        except ValueError:
            return None

    # Try dispatch header embedded in a date column.
    parsed, _ = parse_date_from_header(text, reference_year)
    return parsed


def _normalize_time_field(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if not text:
        return None, "Time value is empty"
    normalized, err = normalize_time_24h(text)
    if normalized:
        return normalized, None
    return text[:32], err


def _build_date_header(schedule_date: date, ship_count: int | None = None) -> str:
    header = f"{DAY_NAMES[schedule_date.weekday()]} {schedule_date.month}/{schedule_date.day}"
    if ship_count is not None:
        label = "ship" if ship_count == 1 else "ships"
        header += f" - {ship_count} {label}"
    return header


def _resolve_boat_and_berth(row: dict[str, str]) -> tuple[str, str | None]:
    """
    Separate port berth codes from captain/tour-boat dispatch codes.

    Port berth values (2, 3, WW, AN3) must never become boat_codes.
    Tour boat codes (BW, BWA, FNF, 50/50, SR, SL) belong in boat_codes.
    """
    raw_boat = row.get("boat_codes", "").strip()
    raw_berth = row.get("berth", "").strip()

    boat_codes, berth = repair_boat_berth_value(raw_boat or None, raw_berth or None)
    return boat_codes, berth


def _assign_boat_codes_by_day(rows: list[dict]) -> None:
    """Fill missing boat codes with alphabetical CPT-* assignments per day."""
    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in rows:
        if not row.get("boat_codes"):
            by_day[row["schedule_date"]].append(row)

    for schedule_date, day_rows in by_day.items():
        day_rows.sort(key=lambda r: r["ship"].lower())
        for idx, row in enumerate(day_rows):
            row["boat_codes"] = f"CPT-{chr(ord('A') + idx)}"


def _apply_ship_counts(rows: list[dict]) -> None:
    """Set ship_count and enrich date_header when count is known."""
    counts: dict[date, int] = defaultdict(int)
    for row in rows:
        counts[row["schedule_date"]] += 1

    for row in rows:
        count = counts[row["schedule_date"]]
        row["ship_count"] = count
        if not DATE_HEADER_PATTERN.search(row.get("date_header", "")):
            row["date_header"] = _build_date_header(row["schedule_date"], count)


def parse_csv_content(content: bytes, filename: str = "upload.csv") -> tuple[list[dict], list[str]]:
    """
    Parse CSV bytes into validated schedule row dicts.

    Returns (parsed_rows, errors).
    """
    errors: list[str] = []
    text = _decode_csv_bytes(content)
    if not text.strip():
        return [], ["CSV file is empty"]

    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return [], ["CSV has no header row"]

    column_map: dict[str, str] = {}
    for header in reader.fieldnames:
        if header is None:
            continue
        field = _resolve_csv_column(header)
        if field:
            column_map[header] = field

    if "ship" not in column_map.values():
        return [], [
            "Could not find a ship column. Expected headers like: ship, vessel, cruise_ship"
        ]

    has_date_header = "date_header" in column_map.values()
    has_schedule_date = "schedule_date" in column_map.values()
    if not has_date_header and not has_schedule_date:
        return [], [
            "Could not find a date column. Expected headers like: date, schedule_date, date_header"
        ]

    raw_rows: list[dict[str, str]] = []
    for idx, row in enumerate(reader, start=2):
        if not any(str(v).strip() for v in row.values() if v is not None):
            continue
        mapped: dict[str, str] = {}
        for header, value in row.items():
            if header is None:
                continue
            field = column_map.get(header)
            if field and value is not None and str(value).strip():
                mapped[field] = str(value).strip()
        if mapped.get("ship"):
            raw_rows.append(mapped)
        else:
            errors.append(f"Row {idx}: missing ship name")

    if not raw_rows:
        return [], errors or ["No data rows found in CSV"]

    pseudo_headers = [{"date_header": r.get("date_header", r.get("schedule_date", ""))} for r in raw_rows]
    reference_year = infer_reference_year(pseudo_headers)

    parsed_rows: list[dict] = []
    for idx, row in enumerate(raw_rows, start=2):
        ship = row.get("ship", "").strip()
        if not ship:
            errors.append(f"Row {idx}: ship name is empty")
            continue

        schedule_date: date | None = None
        ship_count: int | None = None
        date_header = row.get("date_header", "").strip()

        if date_header:
            schedule_date, ship_count = parse_date_from_header(date_header, reference_year)

        if schedule_date is None and row.get("schedule_date"):
            schedule_date = _parse_date_value(row["schedule_date"], reference_year)

        if schedule_date is None and date_header:
            schedule_date = _parse_date_value(date_header, reference_year)

        if schedule_date is None:
            errors.append(
                f"Row {idx}: could not parse date from "
                f"'{date_header or row.get('schedule_date', '')}'"
            )
            continue

        checkin_raw = row.get("checkin_time", "")
        return_raw = row.get("return_time", "")
        if not checkin_raw or not return_raw:
            errors.append(f"Row {idx} ({ship}): missing arrival or departure time")
            continue

        checkin_time, checkin_err = _normalize_time_field(checkin_raw)
        return_time, return_err = _normalize_time_field(return_raw)
        if checkin_err:
            errors.append(f"Row {idx} ({ship}): {checkin_err}")
        if return_err:
            errors.append(f"Row {idx} ({ship}): {return_err}")
        if not checkin_time or not return_time:
            continue

        boat_codes, berth = _resolve_boat_and_berth(row)

        if not date_header:
            date_header = _build_date_header(schedule_date)

        parsed_rows.append(
            {
                "date_header": date_header,
                "schedule_date": schedule_date,
                "ship": ship,
                "checkin_time": checkin_time,
                "return_time": return_time,
                "boat_codes": boat_codes,
                "berth": berth,
                "ship_count": ship_count,
            }
        )

    _assign_boat_codes_by_day(parsed_rows)
    _apply_ship_counts(parsed_rows)
    return parsed_rows, errors


def import_csv_schedules(
    db: Session,
    content: bytes,
    filename: str,
    *,
    replace_existing: bool = False,
) -> tuple[str, int, int, list[str]]:
    """
    Parse a CSV schedule file and persist rows to the database.

    When replace_existing=True, all prior schedule entries are deleted first.
    """
    if replace_existing:
        clear_schedule_data(db)

    rows, errors = parse_csv_content(content, filename)
    return persist_schedule_rows(db, rows, filename, errors)


def looks_like_csv(filename: str | None, content_type: str | None, content: bytes) -> bool:
    """Determine whether uploaded bytes are likely a CSV schedule file."""
    if filename and filename.strip().lower().endswith(".csv"):
        return True

    if content_type:
        ct = content_type.lower().split(";")[0].strip()
        if ct in ("text/csv", "application/csv", "application/vnd.ms-excel"):
            return True

    if not content:
        return False

    sample = _decode_csv_bytes(content[:4096]).strip().lower()
    if not sample or sample.startswith("<?xml") or sample.startswith("<"):
        return False

    first_line = sample.splitlines()[0]
    if "," not in first_line and ";" not in first_line and "\t" not in first_line:
        return False

    headers = re.split(r"[,;\t|]", first_line)
    normalized = {_normalize_header(h) for h in headers}
    ship_aliases = {_normalize_header(a) for a in CSV_COLUMN_ALIASES["ship"]}
    date_aliases = {_normalize_header(a) for a in CSV_COLUMN_ALIASES["schedule_date"] + CSV_COLUMN_ALIASES["date_header"]}
    return bool(normalized & ship_aliases) and bool(normalized & date_aliases)
