"""
XML parsing and schedule import pipeline.

Responsible for:
  1. Reading uploaded XML bytes (dispatch schedule exports)
  2. Normalizing element names to the expected schema
  3. Parsing dispatch date headers into concrete dates
  4. Persisting rows to the database with deduplication

Expected XML structure (element aliases also accepted — see FIELD_ALIASES):

    <schedules>
      <schedule>
        <date_header>Thursday 6/4 - 6 ships</date_header>
        <ship>Symphony of the Seas</ship>
        <checkin_time>7:00 AM</checkin_time>
        <return_time>4:30 PM</return_time>
        <boat_codes>CPT-A / OP-12</boat_codes>
      </schedule>
    </schedules>
"""

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.schedule_import import persist_schedule_rows

# Canonical field names every valid dispatch XML entry must provide.
REQUIRED_FIELDS = {
    "date_header",
    "ship",
    "checkin_time",
    "return_time",
    "boat_codes",
}

# Maps canonical names to common element-name variants.
FIELD_ALIASES: dict[str, list[str]] = {
    "date_header": [
        "date_header",
        "date",
        "dispatch_date",
        "header",
        "date_line",
        "schedule_date",
    ],
    "ship": [
        "ship",
        "vessel",
        "cruise_ship",
        "ship_name",
        "cruise",
    ],
    "checkin_time": [
        "checkin_time",
        "check_in_time",
        "checkin",
        "check_in",
        "start_time",
        "start",
        "report_time",
    ],
    "return_time": [
        "return_time",
        "return",
        "checkout_time",
        "check_out_time",
        "end_time",
        "end",
    ],
    "boat_codes": [
        "boat_codes",
        "boat_code",
        "boat",
        "boats",
        "operator",
        "operator_code",
        "captain",
        "captain_code",
        "codes",
    ],
}

# Container/row element names that hold individual schedule entries.
ENTRY_TAGS = {"schedule", "entry", "row", "dispatch", "assignment"}
ROOT_TAGS = {"schedules", "dispatch_list", "schedule_list", "root", "data"}

# Matches dispatch date lines like "Thursday 6/4 - 6 ships".
DATE_HEADER_PATTERN = re.compile(
    r"(?P<weekday>[A-Za-z]+)\s+(?P<month>\d{1,2})/(?P<day>\d{1,2})"
    r"(?:\s*-\s*(?P<ship_count>\d+)\s*ships?)?",
    re.IGNORECASE,
)


def _normalize_tag(tag: str) -> str:
    """Strip XML namespace and normalize an element tag to snake_case."""
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    return tag.strip().lower().replace(" ", "_").replace("-", "_")


def _resolve_field_name(tag: str) -> str | None:
    """Map an XML element tag to a canonical field name, if recognized."""
    normalized = _normalize_tag(tag)
    if normalized in REQUIRED_FIELDS:
        return normalized
    for target, aliases in FIELD_ALIASES.items():
        alias_norms = {a.lower().replace(" ", "_").replace("-", "_") for a in aliases}
        if normalized in alias_norms:
            return target
    return None


def _element_text(element: ET.Element) -> str:
    """Return trimmed text content from an XML element."""
    if element.text is None:
        return ""
    return element.text.strip()


def _extract_entry_fields(entry: ET.Element) -> dict[str, str]:
    """Read child elements (or attributes) from one schedule entry into a field dict."""
    fields: dict[str, str] = {}

    for child in entry:
        field = _resolve_field_name(child.tag)
        if field:
            fields[field] = _element_text(child)

    # Fall back to attributes when fields are provided as attrs instead of child elements.
    for attr, value in entry.attrib.items():
        field = _resolve_field_name(attr)
        if field and field not in fields and value.strip():
            fields[field] = value.strip()

    return fields


def parse_date_from_header(date_header: str, reference_year: int | None = None) -> tuple[date | None, int | None]:
    """
    Extract a calendar date and optional ship count from a dispatch header line.

    Example input: "Thursday 6/4 - 6 ships" → date(2026, 6, 4), 6
    """
    if not date_header or not isinstance(date_header, str):
        return None, None

    match = DATE_HEADER_PATTERN.search(date_header.strip())
    if not match:
        return None, None

    month = int(match.group("month"))
    day = int(match.group("day"))
    ship_count = int(match.group("ship_count")) if match.group("ship_count") else None
    year = reference_year or datetime.now().year

    try:
        parsed = date(year, month, day)
    except ValueError:
        return None, ship_count

    return parsed, ship_count


def infer_reference_year(rows: list[dict]) -> int:
    """
    Choose the most likely year for dates that omit a year in the header.

    Dispatch files often use "6/4" without a year. We try the previous, current,
    and next calendar year and pick whichever keeps parsed dates closest to today.
    """
    today = date.today()
    candidates: set[int] = set()
    for row in rows:
        header = row.get("date_header", "")
        match = DATE_HEADER_PATTERN.search(str(header))
        if not match:
            continue
        month = int(match.group("month"))
        day = int(match.group("day"))
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                date(year, month, day)
                candidates.add(year)
            except ValueError:
                continue

    if not candidates:
        return today.year

    def score(year: int) -> float:
        total = 0.0
        count = 0
        for row in rows:
            header = row.get("date_header", "")
            match = DATE_HEADER_PATTERN.search(str(header))
            if not match:
                continue
            month = int(match.group("month"))
            day = int(match.group("day"))
            try:
                d = date(year, month, day)
                total += abs((d - today).days)
                count += 1
            except ValueError:
                continue
        return total / max(count, 1)

    return min(candidates, key=score)


def _find_schedule_entries(root: ET.Element) -> list[ET.Element]:
    """Locate schedule entry elements regardless of root/container naming."""
    root_tag = _normalize_tag(root.tag)

    # Direct children that look like entries.
    entries = [child for child in root if _normalize_tag(child.tag) in ENTRY_TAGS]
    if entries:
        return entries

    # Root is a single entry element.
    if root_tag in ENTRY_TAGS:
        return [root]

    # Search one level deeper inside a container wrapper.
    for child in root:
        child_tag = _normalize_tag(child.tag)
        if child_tag in ROOT_TAGS or child_tag.endswith("list"):
            nested = [item for item in child if _normalize_tag(item.tag) in ENTRY_TAGS]
            if nested:
                return nested

    # Last resort: any element that contains all required child fields.
    found: list[ET.Element] = []
    for elem in root.iter():
        fields = _extract_entry_fields(elem)
        if REQUIRED_FIELDS.issubset(fields):
            found.append(elem)
    return found


def _parse_standard_xml_entries(content: bytes, filename: str) -> tuple[list[dict], list[str]]:
    """Parse structured <schedule> XML entries."""
    errors: list[str] = []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        return [], [f"Could not read XML: {exc}"]

    entries = _find_schedule_entries(root)
    if not entries:
        return [], []

    raw_rows: list[dict] = []
    for idx, entry in enumerate(entries, start=1):
        fields = _extract_entry_fields(entry)
        missing = REQUIRED_FIELDS - set(fields)
        if missing:
            tag = _normalize_tag(entry.tag)
            errors.append(f"Entry {idx} (<{tag}>): missing fields: {', '.join(sorted(missing))}")
            continue
        raw_rows.append(fields)

    if not raw_rows:
        return [], errors

    reference_year = infer_reference_year(raw_rows)
    parsed_rows: list[dict] = []

    for idx, row in enumerate(raw_rows, start=1):
        date_header = row.get("date_header", "").strip()
        schedule_date, ship_count = parse_date_from_header(date_header, reference_year)

        if schedule_date is None:
            errors.append(f"Entry {idx}: could not parse date from '{date_header}'")
            continue

        ship = row.get("ship", "").strip()
        if not ship:
            errors.append(f"Entry {idx}: ship name is empty")
            continue

        parsed_rows.append(
            {
                "date_header": date_header,
                "schedule_date": schedule_date,
                "ship": ship,
                "checkin_time": row.get("checkin_time", "").strip(),
                "return_time": row.get("return_time", "").strip(),
                "boat_codes": row.get("boat_codes", "").strip(),
                "ship_count": ship_count,
            }
        )

    return parsed_rows, errors


def parse_xml_content(content: bytes, filename: str = "upload.xml") -> tuple[list[dict], list[str]]:
    """
    Parse raw XML bytes into validated schedule row dicts.

    Extracts both structured <schedule> entries and MMS dispatch message tours.
    Returns (parsed_rows, errors). Individual bad entries produce error messages
    but do not fail the entire file — valid entries are still returned.
    """
    errors: list[str] = []

    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        # Still attempt MMS/text dispatch extraction from malformed XML.
        from app.mms_dispatch_parser import extract_mms_dispatch_rows

        dispatch_rows, dispatch_errors = extract_mms_dispatch_rows(content)
        errors.append(f"Could not read XML: {exc}")
        errors.extend(dispatch_errors)
        if dispatch_rows:
            return dispatch_rows, errors
        return [], errors

    parsed_rows, std_errors = _parse_standard_xml_entries(content, filename)
    errors.extend(std_errors)

    from app.mms_dispatch_parser import extract_mms_dispatch_rows, merge_dispatch_row_sets

    dispatch_rows, dispatch_errors = extract_mms_dispatch_rows(content)
    errors.extend(dispatch_errors)
    if dispatch_rows:
        parsed_rows = merge_dispatch_row_sets(parsed_rows, dispatch_rows)

    if not parsed_rows:
        return [], errors or ["No schedule entries found in XML. Expected <schedule> elements or MMS dispatch tour lines."]

    return parsed_rows, errors


def import_schedules(db: Session, content: bytes, filename: str) -> tuple[str, int, int, list[str]]:
    """
    Parse an XML file and persist schedule rows to the database.

    Deduplication strategy:
      - Within the same upload batch: skip duplicate keys in-memory
      - Across uploads: update existing rows instead of inserting duplicates

    Returns (batch_id, rows_imported, rows_skipped, parse_errors).
    """
    from app.xml_cleaner import clean_xml_bytes

    cleaned_content, clean_report = clean_xml_bytes(content)
    if cleaned_content:
        content = cleaned_content

    rows, errors = parse_xml_content(content, filename)

    if clean_report.repairs:
        repair_note = (
            f"Auto-repaired {clean_report.analysis.times_normalized} times, "
            f"{clean_report.analysis.boat_fields_repaired} boat fields"
        )
        errors = [repair_note, *errors]

    return persist_schedule_rows(db, rows, filename, errors)
