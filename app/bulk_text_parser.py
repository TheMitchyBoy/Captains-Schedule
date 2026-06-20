"""
Relaxed parsers for bulk tour paste text.

Converts common layouts (multi-line blocks, tabs, compact times) into
dispatch-style lines before standard or AI parsing.
"""

from __future__ import annotations

import re
from datetime import date

from app.mms_dispatch_parser import (
    _build_date_header,
    parse_tour_lines_from_text,
    resolve_dispatch_ship_name,
)
from app.xml_cleaner import normalize_time_24h

DASH = r"(?:\u2014|\u2013|—|–|-)"
TIME_TOKEN = r"\d{3,4}|\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?"
PROSE_UNTIL_RE = re.compile(
    rf"(?P<ship>[A-Za-z][A-Za-z0-9.\s&()'-]+?)\s+had\s+"
    rf"(?P<boats>.+?)\s+at\s+(?P<start>{TIME_TOKEN})\s+until\s+(?P<end>{TIME_TOKEN})",
    re.IGNORECASE,
)
PROSE_BOATS_AT_RE = re.compile(
    rf"(?P<ship>[A-Za-z][A-Za-z0-9.\s&()'-]+?)\s+"
    rf"(?P<boats>(?:[A-Za-z][A-Za-z0-9]*(?:\s*,\s*|\s+and\s+)[A-Za-z][A-Za-z0-9]*)+)\s+"
    rf"at\s+(?P<start>{TIME_TOKEN})(?:\s*(?:{DASH}|to|until)\s*(?P<end>{TIME_TOKEN}))?",
    re.IGNORECASE,
)

TIME_RANGE_RE = re.compile(
    rf"(?P<start>\d{{1,2}}(?::\d{{2}})?\s*(?:[ap]\.?m\.?)?|\d{{3,4}})\s*(?:{DASH}|to)\s*"
    rf"(?P<end>\d{{1,2}}(?::\d{{2}})?\s*(?:[ap]\.?m\.?)?|\d{{3,4}})",
    re.IGNORECASE,
)
TIME_ONLY_RE = re.compile(
    r"^\s*\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?\s*(?:-\s*\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?)?\s*$",
    re.IGNORECASE,
)
BOAT_CODE_RE = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9]*(?:\s*,\s*[A-Za-z][A-Za-z0-9]*)+|50/50)$"
)
SHIP_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\s&()'-]+$")
COMPACT_TIME_IN_LINE = re.compile(
    rf"\b(\d{{3,4}})\s*(?:{DASH}|to)\s*(\d{{3,4}})\b",
    re.IGNORECASE,
)


def expand_compact_time(token: str) -> str:
    """Expand 615 / 1030 style tokens into H:MM when possible."""
    text = token.strip().lower()
    if not text.isdigit() or len(text) not in (3, 4):
        return token.strip()

    if len(text) == 3:
        hour = int(text[0])
        minute = int(text[1:])
    else:
        hour = int(text[:2])
        minute = int(text[2:])

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour}:{minute:02d}"
    return token.strip()


def normalize_compact_time_range(value: str) -> str:
    """Normalize 615-1030 and 615am-1030am into parser-friendly ranges."""
    text = value.strip()
    match = TIME_RANGE_RE.search(text)
    if not match:
        return text

    start = expand_compact_time(match.group("start"))
    end = expand_compact_time(match.group("end"))
    return f"{start}-{end}"


def expand_compact_times_in_line(line: str) -> str:
    """Replace compact HHMM-HHMM sequences inside a line."""

    def repl(match: re.Match[str]) -> str:
        return f"{expand_compact_time(match.group(1))}-{expand_compact_time(match.group(2))}"

    return COMPACT_TIME_IN_LINE.sub(repl, line)


def _looks_like_boats_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if BOAT_CODE_RE.match(text):
        return True
    return bool(re.search(r",", text)) and not TIME_ONLY_RE.match(text)


def _looks_like_times_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if TIME_ONLY_RE.match(text):
        return True
    return bool(TIME_RANGE_RE.search(text))


def _looks_like_ship_line(line: str) -> bool:
    text = line.strip()
    if not text or _looks_like_times_line(text) or _looks_like_boats_line(text):
        return False
    return bool(SHIP_LINE_RE.match(text)) and len(text.split()) <= 6


def normalize_bulk_paste(text: str) -> str:
    """Rewrite common bulk paste layouts into dispatch-style lines."""
    raw_lines = [line.strip() for line in text.splitlines()]
    output: list[str] = []
    i = 0

    while i < len(raw_lines):
        line = raw_lines[i]
        if not line:
            i += 1
            continue

        if "\t" in line or "|" in line:
            parts = [part.strip() for part in re.split(r"[\t|]+", line) if part.strip()]
            if len(parts) >= 3:
                ship, boats, *rest = parts
                if len(rest) == 1:
                    times = normalize_compact_time_range(rest[0])
                else:
                    times = normalize_compact_time_range(f"{rest[0]}-{rest[1]}")
                output.append(f"{ship} — {boats} — {times}")
                i += 1
                continue

        if i + 2 < len(raw_lines):
            ship_line, boats_line, times_line = raw_lines[i], raw_lines[i + 1], raw_lines[i + 2]
            if (
                _looks_like_ship_line(ship_line)
                and _looks_like_boats_line(boats_line)
                and _looks_like_times_line(times_line)
            ):
                output.append(
                    f"{ship_line} — {boats_line} — {normalize_compact_time_range(times_line)}"
                )
                i += 3
                continue

        output.append(expand_compact_times_in_line(line))
        i += 1

    return "\n".join(output)


def _normalize_boat_list(raw: str) -> str:
    text = re.sub(r"\s+and\s+", ", ", raw.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*,\s*", ", ", text)


def rows_from_prose_fallback(text: str, schedule_date: date) -> tuple[list[dict], list[str]]:
    """Extract tours from short natural-language lines before calling AI."""
    rows: list[dict] = []
    errors: list[str] = []
    header = _build_date_header(schedule_date)

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        match = PROSE_UNTIL_RE.search(line) or PROSE_BOATS_AT_RE.search(line)
        if not match:
            continue

        checkin, checkin_err = normalize_time_24h(expand_compact_time(match.group("start")))
        end_raw = match.group("end")
        if end_raw:
            return_norm, return_err = normalize_time_24h(expand_compact_time(end_raw))
        else:
            return_norm, return_err = None, "missing return time"

        if checkin_err or return_err or not checkin or not return_norm:
            errors.append(f"Line {line_no}: invalid times")
            continue

        rows.append(
            {
                "date_header": header,
                "schedule_date": schedule_date,
                "ship": resolve_dispatch_ship_name(match.group("ship")),
                "checkin_time": checkin,
                "return_time": return_norm,
                "boat_codes": _normalize_boat_list(match.group("boats")),
                "ship_count": None,
            }
        )

    return rows, errors


def parse_relaxed_bulk_lines(
    text: str,
    schedule_date: date,
) -> tuple[list[dict], list[str]]:
    """Parse bulk paste using normalization plus dispatch line patterns."""
    normalized = normalize_bulk_paste(text)
    rows, errors = parse_tour_lines_from_text(
        normalized,
        schedule_date.year,
        fixed_date=schedule_date,
    )
    if rows:
        return rows, errors

    if normalized != text:
        rows, extra_errors = parse_tour_lines_from_text(
            text,
            schedule_date.year,
            fixed_date=schedule_date,
        )
        errors.extend(extra_errors)
        if rows:
            return rows, errors

    return [], errors


def rows_from_tabular_fallback(text: str, schedule_date: date) -> tuple[list[dict], list[str]]:
    """
    Last-resort row builder for simple tab/comma separated pastes:
    ship, boats, checkin, return
    """
    rows: list[dict] = []
    errors: list[str] = []
    header = _build_date_header(schedule_date)

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts = [part.strip() for part in re.split(r"[\t|,]+", line) if part.strip()]
        if len(parts) < 3:
            continue

        ship_raw = parts[0]
        if len(parts) >= 4:
            boats_raw = parts[1]
            checkin_raw = parts[2]
            return_raw = parts[3]
        else:
            boats_raw = parts[1]
            if TIME_RANGE_RE.search(parts[2]):
                match = TIME_RANGE_RE.search(parts[2])
                checkin_raw = match.group("start")
                return_raw = match.group("end")
            else:
                continue

        checkin, checkin_err = normalize_time_24h(expand_compact_time(checkin_raw))
        return_norm, return_err = normalize_time_24h(expand_compact_time(return_raw))
        if checkin_err or return_err or not checkin or not return_norm:
            errors.append(f"Line {line_no}: invalid times")
            continue

        rows.append(
            {
                "date_header": header,
                "schedule_date": schedule_date,
                "ship": resolve_dispatch_ship_name(ship_raw),
                "checkin_time": checkin,
                "return_time": return_norm,
                "boat_codes": boats_raw,
                "ship_count": None,
            }
        )

    return rows, errors
