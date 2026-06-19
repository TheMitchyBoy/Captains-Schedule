"""Create and update individual schedule rows manually."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.berth_utils import merge_dispatch_codes
from app.models import ScheduleEntry
from app.ship_data import get_ship_capacity
from app.xml_cleaner import normalize_time_24h

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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
    ship_name = ship.strip()
    if not ship_name:
        raise ValueError("Ship name is required")

    checkin = _normalize_time_field(checkin_time, "check-in time")
    return_norm = _normalize_time_field(return_time, "return time")
    boats = boat_codes.strip()
    header = (date_header or _build_date_header(schedule_date)).strip()[:255]
    berth_value = berth.strip() if berth and berth.strip() else None

    existing_slot = (
        db.query(ScheduleEntry)
        .filter(
            ScheduleEntry.schedule_date == schedule_date,
            ScheduleEntry.ship == ship_name,
            ScheduleEntry.checkin_time == checkin,
            ScheduleEntry.return_time == return_norm,
        )
        .first()
    )
    if existing_slot:
        merged_boats = merge_dispatch_codes(existing_slot.boat_codes, boats)[:255]
        if merged_boats == existing_slot.boat_codes and (
            berth_value is None or berth_value == existing_slot.berth
        ):
            raise ValueError("This tour already exists")
        existing_slot.boat_codes = merged_boats
        if berth_value:
            existing_slot.berth = berth_value
        existing_slot.date_header = header
        db.commit()
        db.refresh(existing_slot)
        return existing_slot

    if _find_exact_duplicate(
        db,
        schedule_date=schedule_date,
        ship=ship_name,
        checkin_time=checkin,
        return_time=return_norm,
        boat_codes=boats,
    ):
        raise ValueError("This tour already exists")

    get_ship_capacity(db, ship_name)

    entry = ScheduleEntry(
        date_header=header,
        schedule_date=schedule_date,
        ship=ship_name[:255],
        checkin_time=checkin[:32],
        return_time=return_norm[:32],
        boat_codes=boats[:255],
        berth=berth_value,
        ship_count=None,
        upload_batch_id=f"manual-{uuid.uuid4()}",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


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
        new_boats = boat_codes.strip()

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
