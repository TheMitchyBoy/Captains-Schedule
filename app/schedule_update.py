"""Create and update individual schedule rows manually."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.berth_utils import merge_dispatch_codes
from app.models import ScheduleEntry
from app.ship_data import get_ship_capacity
from app.xml_cleaner import normalize_time_24h

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class BulkCreateResult:
    rows_parsed: int
    rows_created: int
    rows_merged: int
    rows_skipped: int
    errors: list[str]
    ai_assisted: bool = False
    ai_message: str | None = None


def _build_date_header(schedule_date: date) -> str:
    return f"{DAY_NAMES[schedule_date.weekday()]} {schedule_date.month}/{schedule_date.day}"


def _normalize_time_field(value: str, label: str) -> str:
    normalized, err = normalize_time_24h(value.strip())
    if err or not normalized:
        raise ValueError(err or f"Unrecognized {label}: '{value}'")
    return normalized


def _find_exact_duplicate(
    db: Session,
    *,
    schedule_date: date,
    ship: str,
    checkin_time: str,
    return_time: str,
    boat_codes: str,
    exclude_id: int | None = None,
) -> ScheduleEntry | None:
    q = db.query(ScheduleEntry).filter(
        ScheduleEntry.schedule_date == schedule_date,
        ScheduleEntry.ship == ship,
        ScheduleEntry.checkin_time == checkin_time,
        ScheduleEntry.return_time == return_time,
        ScheduleEntry.boat_codes == boat_codes,
    )
    if exclude_id is not None:
        q = q.filter(ScheduleEntry.id != exclude_id)
    return q.first()


def _upsert_schedule_entry(
    db: Session,
    *,
    schedule_date: date,
    ship: str,
    checkin_time: str,
    return_time: str,
    boat_codes: str = "",
    berth: str | None = None,
    date_header: str | None = None,
    upload_batch_id: str | None = None,
    raise_on_duplicate: bool = True,
) -> tuple[ScheduleEntry, str]:
    """
    Insert or merge a schedule row.

    Returns (entry, action) where action is created, merged, or skipped.
    """
    ship_name = ship.strip()
    if not ship_name:
        raise ValueError("Ship name is required")

    boats = merge_dispatch_codes(boat_codes) if (boat_codes or "").strip() else ""
    header = (date_header or _build_date_header(schedule_date)).strip()[:255]
    berth_value = berth.strip() if berth and berth.strip() else None
    batch_id = upload_batch_id or f"manual-{uuid.uuid4()}"

    existing_slot = (
        db.query(ScheduleEntry)
        .filter(
            ScheduleEntry.schedule_date == schedule_date,
            ScheduleEntry.ship == ship_name,
            ScheduleEntry.checkin_time == checkin_time,
            ScheduleEntry.return_time == return_time,
        )
        .first()
    )
    if existing_slot:
        merged_boats = merge_dispatch_codes(existing_slot.boat_codes, boats)[:255]
        berth_changed = berth_value is not None and berth_value != existing_slot.berth
        if merged_boats == existing_slot.boat_codes and not berth_changed:
            if raise_on_duplicate:
                raise ValueError("This tour already exists")
            return existing_slot, "skipped"

        existing_slot.boat_codes = merged_boats
        if berth_value:
            existing_slot.berth = berth_value
        existing_slot.date_header = header
        existing_slot.upload_batch_id = batch_id
        return existing_slot, "merged"

    if _find_exact_duplicate(
        db,
        schedule_date=schedule_date,
        ship=ship_name,
        checkin_time=checkin_time,
        return_time=return_time,
        boat_codes=boats,
    ):
        if raise_on_duplicate:
            raise ValueError("This tour already exists")
        existing = _find_exact_duplicate(
            db,
            schedule_date=schedule_date,
            ship=ship_name,
            checkin_time=checkin_time,
            return_time=return_time,
            boat_codes=boats,
        )
        assert existing is not None
        return existing, "skipped"

    get_ship_capacity(db, ship_name)

    entry = ScheduleEntry(
        date_header=header,
        schedule_date=schedule_date,
        ship=ship_name[:255],
        checkin_time=checkin_time[:32],
        return_time=return_time[:32],
        boat_codes=boats[:255],
        berth=berth_value,
        ship_count=None,
        upload_batch_id=batch_id,
    )
    db.add(entry)
    return entry, "created"


def create_schedule_entry(
    db: Session,
    *,
    schedule_date: date,
    ship: str,
    checkin_time: str,
    return_time: str,
    boat_codes: str = "",
    berth: str | None = None,
    date_header: str | None = None,
) -> ScheduleEntry:
    """Add a new tour row (or merge boats into an existing same-time slot)."""
    checkin = _normalize_time_field(checkin_time, "check-in time")
    return_norm = _normalize_time_field(return_time, "return time")

    entry, _action = _upsert_schedule_entry(
        db,
        schedule_date=schedule_date,
        ship=ship,
        checkin_time=checkin,
        return_time=return_norm,
        boat_codes=boat_codes,
        berth=berth,
        date_header=date_header,
        raise_on_duplicate=True,
    )
    db.commit()
    db.refresh(entry)
    return entry


def bulk_create_schedule_entries(
    db: Session,
    text: str,
    schedule_date: date,
    *,
    use_ai: bool = True,
) -> BulkCreateResult:
    """Parse dispatch-style tour lines for one day and add them to the database."""
    from app.ai_tour_parser import ai_parse_tour_lines
    from app.bulk_text_parser import (
        parse_relaxed_bulk_lines,
        rows_from_prose_fallback,
        rows_from_tabular_fallback,
    )

    rows, parse_errors = parse_relaxed_bulk_lines(text, schedule_date)
    ai_assisted = False
    ai_message: str | None = None

    if not rows:
        tabular_rows, tabular_errors = rows_from_tabular_fallback(text, schedule_date)
        if tabular_rows:
            rows = tabular_rows
            parse_errors = tabular_errors
        else:
            parse_errors.extend(tabular_errors)

    if not rows:
        prose_rows, prose_errors = rows_from_prose_fallback(text, schedule_date)
        if prose_rows:
            rows = prose_rows
            parse_errors = prose_errors
        else:
            parse_errors.extend(prose_errors)

    if not rows and use_ai:
        ai_rows, ai_message = ai_parse_tour_lines(text, schedule_date, db=db)
        if ai_rows:
            rows = ai_rows
            ai_assisted = True
            parse_errors = []
        elif ai_message:
            parse_errors.append(ai_message)

    batch_id = f"manual-bulk-{uuid.uuid4()}"

    created = merged = skipped = 0
    errors = list(parse_errors)

    if not rows and not errors:
        errors.append("No tour lines found in the pasted text")

    for row in rows:
        label = f"{row.get('ship', '?')} {row.get('schedule_date', '?')}"
        try:
            entry, action = _upsert_schedule_entry(
                db,
                schedule_date=row["schedule_date"],
                ship=row["ship"],
                checkin_time=row["checkin_time"],
                return_time=row["return_time"],
                boat_codes=row.get("boat_codes", ""),
                date_header=row.get("date_header"),
                upload_batch_id=batch_id,
                raise_on_duplicate=False,
            )
            if action == "created":
                created += 1
            elif action == "merged":
                merged += 1
            else:
                skipped += 1
            db.flush()
            db.refresh(entry)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")

    if created or merged:
        db.commit()

    return BulkCreateResult(
        rows_parsed=len(rows),
        rows_created=created,
        rows_merged=merged,
        rows_skipped=skipped,
        errors=errors,
        ai_assisted=ai_assisted,
        ai_message=ai_message,
    )


def update_schedule_entry(
    db: Session,
    entry_id: int,
    *,
    checkin_time: str | None = None,
    return_time: str | None = None,
    boat_codes: str | None = None,
) -> ScheduleEntry:
    """Apply user edits to one stored schedule row."""
    entry = db.query(ScheduleEntry).filter(ScheduleEntry.id == entry_id).first()
    if entry is None:
        raise ValueError(f"Schedule entry {entry_id} not found")

    if checkin_time is None and return_time is None and boat_codes is None:
        raise ValueError("Provide at least one field to update")

    new_checkin = entry.checkin_time
    new_return = entry.return_time
    new_boats = entry.boat_codes

    if checkin_time is not None:
        normalized, err = normalize_time_24h(checkin_time.strip())
        if err or not normalized:
            raise ValueError(err or f"Unrecognized check-in time: '{checkin_time}'")
        new_checkin = normalized

    if return_time is not None:
        normalized, err = normalize_time_24h(return_time.strip())
        if err or not normalized:
            raise ValueError(err or f"Unrecognized return time: '{return_time}'")
        new_return = normalized

    if boat_codes is not None:
        new_boats = merge_dispatch_codes(boat_codes) if boat_codes.strip() else ""

    conflict = _find_exact_duplicate(
        db,
        schedule_date=entry.schedule_date,
        ship=entry.ship,
        checkin_time=new_checkin,
        return_time=new_return,
        boat_codes=new_boats,
        exclude_id=entry_id,
    )
    if conflict:
        raise ValueError("Another schedule row already exists with these values")

    entry.checkin_time = new_checkin
    entry.return_time = new_return
    entry.boat_codes = new_boats
    db.commit()
    db.refresh(entry)
    return entry
