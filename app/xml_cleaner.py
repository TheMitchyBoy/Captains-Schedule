"""
XML schedule cleaner and repair engine.

Accepts raw dispatch XML, re-parses entries, and applies data-driven repairs:

  1. Normalize checkin_time and return_time to 24-hour HH:MM format
  2. Detect boat_codes values corrupted with leaked time fragments (15am:, 30am:, etc.)
  3. Move those minutes back into check-in time (e.g. 7am + 30am: → 07:30)
  4. Optionally use an AI model (OPENAI_API_KEY) to recover entries from badly formed XML

The analysis layer scores each repair with confidence and produces a full audit report.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.config import get_settings

from app.xml_parser import (
    REQUIRED_FIELDS,
    _extract_entry_fields,
    _find_schedule_entries,
    _resolve_field_name,
)

# Minutes prefix leaked into boat_codes when export formatting breaks.
BOAT_TIME_LEAK_PATTERN = re.compile(
    r"^(?P<minutes>15|30)(?P<period>am|pm)\s*:\s*(?P<boats>.*)$",
    re.IGNORECASE,
)

# Flexible time formats seen in dispatch exports.
TIME_PARSE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<period>am|pm)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*$",
    ),
    re.compile(
        r"^\s*(?P<hour>\d{1,2})(?P<minute>\d{2})\s*(?P<period>am|pm)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<hour>\d{1,2})\s*(?P<period>am|pm)\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*(?P<military>\d{3,4})\s*$"),
]

# Regex recovery when ElementTree cannot parse malformed XML.
RAW_ENTRY_PATTERN = re.compile(
    r"<(?P<tag>schedule|entry|row|dispatch)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
RAW_FIELD_PATTERN = re.compile(
    r"<(?P<tag>[^>/\s]+)\s*(?:[^>]*)>\s*(?P<value>.*?)\s*</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class RepairRecord:
    """One automated fix applied to a schedule entry."""

    entry_index: int
    field: str
    issue: str
    before: str
    after: str
    confidence: float = 1.0


@dataclass
class AnalysisSummary:
    """Data analysis metrics produced while cleaning a file."""

    entries_found: int = 0
    times_normalized: int = 0
    boat_fields_repaired: int = 0
    ai_assisted: bool = False
    parse_method: str = "elementtree"
    hour_distribution: dict[str, int] = field(default_factory=dict)
    common_boat_prefixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CleanResult:
    """Full output from the XML cleaning pipeline."""

    cleaned_xml: str
    entries: list[dict[str, str]]
    repairs: list[RepairRecord]
    analysis: AnalysisSummary
    errors: list[str] = field(default_factory=list)


def _parse_time_components(time_str: str) -> tuple[int, int, str | None] | None:
    """Parse a time string into (hour_24, minute, original_period_hint)."""
    if not time_str or not time_str.strip():
        return None

    text = time_str.strip()

    for pattern in TIME_PARSE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue

        if "military" in match.groupdict() and match.group("military"):
            military = match.group("military")
            if len(military) == 3:
                hour = int(military[0])
                minute = int(military[1:])
            else:
                hour = int(military[:2])
                minute = int(military[2:])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute, None
            continue

        hour = int(match.group("hour"))
        minute = int(match.group("minute")) if match.groupdict().get("minute") else 0
        period = match.group("period").lower() if match.groupdict().get("period") else None

        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute, period
    return None


def normalize_time_24h(time_str: str) -> tuple[str | None, str | None]:
    """
    Convert any supported time string to 24-hour HH:MM.

    Returns (normalized_time, error_message).
    """
    parsed = _parse_time_components(time_str)
    if parsed is None:
        return None, f"Unrecognized time format: '{time_str}'"
    hour, minute, _ = parsed
    return f"{hour:02d}:{minute:02d}", None


def _apply_minutes_to_checkin(checkin: str, minutes: int, period_hint: str) -> tuple[str | None, str | None]:
    """
    Merge leaked minute prefix from boat_codes back into check-in time.

    Example: checkin='7am', leaked='30am:' → '07:30'
    """
    parsed = _parse_time_components(checkin)
    if parsed is None:
        # Try parsing checkin with explicit period from leak prefix.
        synthetic = f"{checkin.strip()}{period_hint}"
        parsed = _parse_time_components(synthetic)
        if parsed is None:
            return None, f"Cannot merge minutes into check-in time '{checkin}'"

    hour, _, _ = parsed
    period = period_hint.lower()
    if period == "pm" and hour < 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0

    if minutes not in (15, 30):
        return None, f"Unsupported leaked minutes value: {minutes}"

    if not (0 <= hour <= 23):
        return None, f"Invalid hour after repair: {hour}"

    return f"{hour:02d}:{minutes:02d}", None


def repair_boat_time_leak(
    checkin_time: str,
    boat_codes: str,
) -> tuple[str, str, RepairRecord | None]:
    """
    If boat_codes starts with 15am:/30am:/15pm:/30pm:, move minutes into check-in.

    Returns (new_checkin, new_boats, repair_record_or_none).
    """
    match = BOAT_TIME_LEAK_PATTERN.match(boat_codes.strip())
    if not match:
        return checkin_time, boat_codes, None

    minutes = int(match.group("minutes"))
    period = match.group("period").lower()
    remaining_boats = match.group("boats").strip()

    new_checkin, error = _apply_minutes_to_checkin(checkin_time, minutes, period)
    if error or new_checkin is None:
        return checkin_time, boat_codes, None

    repair = RepairRecord(
        entry_index=0,
        field="boat_codes/checkin_time",
        issue=f"Time fragment '{minutes}{period}:' leaked into boat_codes",
        before=f"checkin={checkin_time!r}, boat_codes={boat_codes!r}",
        after=f"checkin={new_checkin!r}, boat_codes={remaining_boats!r}",
        confidence=0.98,
    )
    return new_checkin, remaining_boats, repair


def clean_entry(entry: dict[str, str], entry_index: int) -> tuple[dict[str, str], list[RepairRecord]]:
    """Normalize times and repair one schedule entry dict."""
    cleaned = dict(entry)
    repairs: list[RepairRecord] = []

    # Step 1: repair leaked time fragments in boat_codes.
    new_checkin, new_boats, leak_repair = repair_boat_time_leak(
        cleaned.get("checkin_time", ""),
        cleaned.get("boat_codes", ""),
    )
    if leak_repair:
        leak_repair.entry_index = entry_index
        cleaned["checkin_time"] = new_checkin
        cleaned["boat_codes"] = new_boats
        repairs.append(leak_repair)

    # Step 2: normalize check-in and return times to HH:MM.
    for time_field in ("checkin_time", "return_time"):
        original = cleaned.get(time_field, "")
        normalized, error = normalize_time_24h(original)
        if normalized and normalized != original:
            repairs.append(
                RepairRecord(
                    entry_index=entry_index,
                    field=time_field,
                    issue="Converted to 24-hour HH:MM format",
                    before=original,
                    after=normalized,
                    confidence=0.99,
                )
            )
            cleaned[time_field] = normalized
        elif error and original.strip():
            repairs.append(
                RepairRecord(
                    entry_index=entry_index,
                    field=time_field,
                    issue=f"Could not normalize time: {error}",
                    before=original,
                    after=original,
                    confidence=0.0,
                )
            )

    return cleaned, repairs


def _regex_extract_entries(raw_text: str) -> list[dict[str, str]]:
    """Fallback parser for malformed XML using pattern matching."""
    entries: list[dict[str, str]] = []
    for block in RAW_ENTRY_PATTERN.finditer(raw_text):
        fields: dict[str, str] = {}
        for field_match in RAW_FIELD_PATTERN.finditer(block.group("body")):
            name = _resolve_field_name(field_match.group("tag"))
            if name:
                value = re.sub(r"\s+", " ", field_match.group("value")).strip()
                fields[name] = value
        if fields:
            entries.append(fields)
    return entries


def _ai_extract_entries(raw_text: str) -> list[dict[str, str]] | None:
    """
    AI-assisted recovery for severely malformed XML.

    Uses OPENAI_API_KEY from the environment. Returns None when AI is unavailable or fails.
    """
    settings = get_settings().openai
    if not settings.is_configured:
        return None

    prompt = (
        "Extract cruise dispatch schedule rows from this malformed XML/text. "
        "Return JSON array only, each object with keys: "
        "date_header, ship, checkin_time, return_time, boat_codes. "
        "Do not include markdown.\n\n"
        f"{raw_text[:12000]}"
    )

    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json={
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": "You extract structured schedule data and return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
        data = json.loads(content)
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    except Exception:
        return None
    return None


def _entry_key(entry: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        entry.get("date_header", "").strip(),
        entry.get("ship", "").strip().lower(),
        entry.get("checkin_time", "").strip(),
        entry.get("return_time", "").strip(),
    )


def _supplement_with_mms_dispatch_entries(
    text: str,
    entries: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Add tour rows from MMS message bodies that structured parsing missed."""
    from app.berth_utils import merge_dispatch_codes
    from app.mms_dispatch_parser import extract_mms_dispatch_rows

    mms_rows, _ = extract_mms_dispatch_rows(text)
    if not mms_rows:
        return entries

    index = {_entry_key(entry): entry for entry in entries}
    for row in mms_rows:
        entry = {
            "date_header": row["date_header"],
            "ship": row["ship"],
            "checkin_time": row["checkin_time"],
            "return_time": row["return_time"],
            "boat_codes": row["boat_codes"],
        }
        key = _entry_key(entry)
        if key in index:
            existing = index[key]
            boats = merge_dispatch_codes(existing.get("boat_codes", ""), entry.get("boat_codes", ""))
            if boats:
                existing["boat_codes"] = boats
        else:
            entries.append(entry)
            index[key] = entry
    return entries


def _entries_missing_fields(entries: list[dict[str, str]]) -> bool:
    """Return True if any entry is missing required dispatch fields."""
    for entry in entries:
        if REQUIRED_FIELDS - {k for k, v in entry.items() if str(v).strip()}:
            return True
    return False


def _extract_raw_entries(content: bytes | str) -> tuple[list[dict[str, str]], str, list[str]]:
    """
    Re-parse XML using ElementTree, regex fallback, and optional AI recovery.

    Returns (entries, parse_method, errors).
    """
    errors: list[str] = []
    if isinstance(content, bytes):
        text = content.decode("utf-8-sig", errors="replace")
    else:
        text = content

    # Primary: standards-compliant XML parser.
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(content, str) else content)
        elements = _find_schedule_entries(root)
        entries = [_extract_entry_fields(elem) for elem in elements]
        entries = [e for e in entries if e]
        if entries:
            entries = _supplement_with_mms_dispatch_entries(text, entries)
            return entries, "elementtree", errors
    except ET.ParseError as exc:
        errors.append(f"ElementTree parse failed: {exc}")

    # MMS / text dispatch messages (multiple tours per message body).
    from app.mms_dispatch_parser import extract_mms_dispatch_rows

    mms_rows, mms_errors = extract_mms_dispatch_rows(text)
    errors.extend(mms_errors)
    if mms_rows:
        mms_entries = [
            {
                "date_header": row["date_header"],
                "ship": row["ship"],
                "checkin_time": row["checkin_time"],
                "return_time": row["return_time"],
                "boat_codes": row["boat_codes"],
            }
            for row in mms_rows
        ]
        errors.append(f"Extracted {len(mms_entries)} tour dispatch rows from MMS/text messages.")
        return mms_entries, "mms", errors

    # Fallback: regex extraction for broken but readable XML.
    regex_entries = _regex_extract_entries(text)
    if regex_entries and not _entries_missing_fields(regex_entries):
        errors.append("Used regex fallback parser due to malformed XML structure.")
        return regex_entries, "regex", errors

    # AI recovery when XML is broken or regex produced incomplete entries.
    if get_settings().openai.is_configured:
        ai_entries = _ai_extract_entries(text)
        if ai_entries:
            note = "Used AI-assisted parser to recover schedule entries."
            if regex_entries:
                note = "AI-assisted parser replaced incomplete regex extraction."
            errors.append(note)
            return ai_entries, "ai", errors

    if regex_entries:
        errors.append("Used regex fallback parser (some entries may be incomplete).")
        return regex_entries, "regex", errors

    errors.append("Could not extract any schedule entries from input.")
    return [], "none", errors


def _analyze_entries(entries: list[dict[str, str]], repairs: list[RepairRecord], parse_method: str, ai_used: bool) -> AnalysisSummary:
    """Build data analysis summary from cleaned entries and repairs."""
    summary = AnalysisSummary(
        entries_found=len(entries),
        times_normalized=sum(1 for r in repairs if r.field in ("checkin_time", "return_time") and r.confidence > 0),
        boat_fields_repaired=sum(1 for r in repairs if "boat_codes" in r.field),
        ai_assisted=ai_used,
        parse_method=parse_method,
    )

    hour_counts: dict[str, int] = {}
    for entry in entries:
        for tf in ("checkin_time", "return_time"):
            value = entry.get(tf, "")
            if re.match(r"^\d{2}:\d{2}$", value):
                hour = value[:2]
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
    summary.hour_distribution = dict(sorted(hour_counts.items()))

    leak_prefixes = [
        r.before.split("boat_codes=")[-1].strip("'\"")
        for r in repairs
        if "boat_codes" in r.field and "leaked" in r.issue
    ]
    summary.common_boat_prefixes = leak_prefixes[:10]

    for repair in repairs:
        if repair.confidence == 0:
            summary.warnings.append(
                f"Entry {repair.entry_index}: unresolved issue on {repair.field} ({repair.issue})"
            )

    return summary


def entries_to_xml(entries: list[dict[str, str]], root_tag: str = "schedules", entry_tag: str = "schedule") -> str:
    """Serialize cleaned schedule entries back to pretty-printed XML."""
    from io import StringIO

    root = ET.Element(root_tag)
    field_order = ("date_header", "ship", "checkin_time", "return_time", "boat_codes")

    for entry in entries:
        item = ET.SubElement(root, entry_tag)
        for key in field_order:
            child = ET.SubElement(item, key)
            child.text = entry.get(key, "")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    buffer = StringIO()
    tree.write(buffer, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + buffer.getvalue()


def clean_xml_content(content: bytes | str) -> CleanResult:
    """
    Full cleaning pipeline: re-parse raw XML, repair entries, emit cleaned XML.

    This is the main entry point used by the API, CLI, and upload pre-processor.
    """
    raw_entries, parse_method, parse_errors = _extract_raw_entries(content)
    if not raw_entries:
        return CleanResult(
            cleaned_xml="",
            entries=[],
            repairs=[],
            analysis=AnalysisSummary(parse_method=parse_method, ai_assisted=parse_method == "ai"),
            errors=parse_errors,
        )

    cleaned_entries: list[dict[str, str]] = []
    all_repairs: list[RepairRecord] = []

    for idx, entry in enumerate(raw_entries, start=1):
        missing = REQUIRED_FIELDS - {k for k, v in entry.items() if str(v).strip()}
        if missing:
            parse_errors.append(f"Entry {idx}: missing fields after re-parse: {', '.join(sorted(missing))}")
            continue

        normalized_entry = {k: str(entry.get(k, "")).strip() for k in REQUIRED_FIELDS}
        cleaned, repairs = clean_entry(normalized_entry, idx)
        cleaned_entries.append(cleaned)
        all_repairs.extend(repairs)

    analysis = _analyze_entries(
        cleaned_entries,
        all_repairs,
        parse_method=parse_method,
        ai_used=parse_method == "ai",
    )

    cleaned_xml = entries_to_xml(cleaned_entries) if cleaned_entries else ""
    return CleanResult(
        cleaned_xml=cleaned_xml,
        entries=cleaned_entries,
        repairs=all_repairs,
        analysis=analysis,
        errors=parse_errors,
    )


def clean_xml_bytes(content: bytes) -> tuple[bytes, CleanResult]:
    """Convenience wrapper returning cleaned XML as bytes plus the full report."""
    result = clean_xml_content(content)
    return result.cleaned_xml.encode("utf-8") if result.cleaned_xml else b"", result
