import io
import re
import uuid
from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.models import ScheduleEntry, UploadLog
from app.ship_data import get_ship_capacity

REQUIRED_COLUMNS = {
    "date_header",
    "ship",
    "checkin_time",
    "return_time",
    "boat_codes",
}

COLUMN_ALIASES: dict[str, list[str]] = {
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

DATE_HEADER_PATTERN = re.compile(
    r"(?P<weekday>[A-Za-z]+)\s+(?P<month>\d{1,2})/(?P<day>\d{1,2})"
    r"(?:\s*-\s*(?P<ship_count>\d+)\s*ships?)?",
    re.IGNORECASE,
)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]

    rename_map: dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        if target in df.columns:
            continue
        for alias in aliases:
            alias_norm = alias.lower().replace(" ", "_").replace("-", "_")
            if alias_norm in df.columns and alias_norm not in rename_map:
                rename_map[alias_norm] = target
                break

    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def _cell_str(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def parse_date_from_header(date_header: str, reference_year: int | None = None) -> tuple[date | None, int | None]:
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
    """Pick a year that keeps parsed dates near today (handles year rollover)."""
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
                d = date(year, month, day)
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


def parse_csv_content(content: bytes, filename: str = "upload.csv") -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    df = None

    for sep in (None, ",", "\t", ";"):
        try:
            df = pd.read_csv(io.BytesIO(content), sep=sep, engine="python" if sep else "c")
            if df is not None and len(df.columns) >= len(REQUIRED_COLUMNS):
                break
        except Exception:
            continue

    if df is None or df.empty:
        return [], ["Could not read CSV: unsupported format or empty file"]

    df = _normalize_columns(df)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return [], [f"Missing required columns: {', '.join(sorted(missing))}"]

    raw_rows = df[list(REQUIRED_COLUMNS)].to_dict(orient="records")
    reference_year = infer_reference_year(raw_rows)
    parsed_rows: list[dict] = []

    for idx, row in enumerate(raw_rows, start=2):
        date_header = _cell_str(row.get("date_header"))
        schedule_date, ship_count = parse_date_from_header(date_header, reference_year)

        if schedule_date is None:
            errors.append(f"Row {idx}: could not parse date from '{date_header}'")
            continue

        ship = _cell_str(row.get("ship"))
        if not ship:
            errors.append(f"Row {idx}: ship name is empty")
            continue

        parsed_rows.append(
            {
                "date_header": date_header,
                "schedule_date": schedule_date,
                "ship": ship,
                "checkin_time": _cell_str(row.get("checkin_time")),
                "return_time": _cell_str(row.get("return_time")),
                "boat_codes": _cell_str(row.get("boat_codes")),
                "ship_count": ship_count,
            }
        )

    return parsed_rows, errors


def import_schedules(db: Session, content: bytes, filename: str) -> tuple[str, int, int, list[str]]:
    batch_id = str(uuid.uuid4())
    rows, errors = parse_csv_content(content, filename)

    imported = 0
    skipped = 0
    seen_in_batch: set[tuple] = set()

    for row in rows:
        key = (
            row["schedule_date"],
            row["ship"],
            row["checkin_time"],
            row["return_time"],
            row["boat_codes"],
        )
        if key in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(key)

        existing = (
            db.query(ScheduleEntry)
            .filter(
                ScheduleEntry.schedule_date == row["schedule_date"],
                ScheduleEntry.ship == row["ship"],
                ScheduleEntry.checkin_time == row["checkin_time"],
                ScheduleEntry.return_time == row["return_time"],
                ScheduleEntry.boat_codes == row["boat_codes"],
            )
            .first()
        )
        if existing:
            existing.date_header = row["date_header"]
            existing.ship_count = row["ship_count"]
            existing.upload_batch_id = batch_id
            skipped += 1
            continue

        get_ship_capacity(db, row["ship"])

        entry = ScheduleEntry(
            date_header=row["date_header"][:255],
            schedule_date=row["schedule_date"],
            ship=row["ship"][:255],
            checkin_time=row["checkin_time"][:32],
            return_time=row["return_time"][:32],
            boat_codes=row["boat_codes"][:255],
            ship_count=row["ship_count"],
            upload_batch_id=batch_id,
        )
        db.add(entry)
        imported += 1

    if not rows and not errors:
        errors.append("No valid rows found in CSV")

    notes = "; ".join(errors[:10]) if errors else None
    if errors and len(errors) > 10:
        notes += f" ... and {len(errors) - 10} more"

    log = UploadLog(
        batch_id=batch_id,
        filename=filename,
        rows_imported=imported,
        rows_skipped=skipped,
        notes=notes,
    )
    db.add(log)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise ValueError(f"Database error while saving schedules: {exc}") from exc

    return batch_id, imported, skipped, errors
