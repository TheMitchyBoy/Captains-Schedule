"""Update individual schedule rows (boats and times)."""

from sqlalchemy.orm import Session

from app.models import ScheduleEntry
from app.xml_cleaner import normalize_time_24h


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

    conflict = (
        db.query(ScheduleEntry)
        .filter(
            ScheduleEntry.id != entry_id,
            ScheduleEntry.schedule_date == entry.schedule_date,
            ScheduleEntry.ship == entry.ship,
            ScheduleEntry.checkin_time == new_checkin,
            ScheduleEntry.return_time == new_return,
            ScheduleEntry.boat_codes == new_boats,
        )
        .first()
    )
    if conflict:
        raise ValueError("Another schedule row already exists with these values")

    entry.checkin_time = new_checkin
    entry.return_time = new_return
    entry.boat_codes = new_boats
    db.commit()
    db.refresh(entry)
    return entry
