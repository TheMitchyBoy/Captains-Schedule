"""
Repair schedule rows where port berth codes were stored as boat/captain codes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.berth_utils import repair_boat_berth_value
from app.models import ScheduleEntry


def repair_schedule_berth_mixups(db: Session) -> int:
    """
    Move misclassified berth codes out of boat_codes into the berth column.

    Legacy CSV imports stored values like BERTH-2 in boat_codes; this moves them
    to the berth column and assigns CPT-* placeholders when no tour boat is known.

    Returns the number of rows updated.
    """
    entries = db.query(ScheduleEntry).all()
    updated = 0

    for entry in entries:
        old_boat = (entry.boat_codes or "").strip()
        old_berth = entry.berth
        new_boat, new_berth = repair_boat_berth_value(entry.boat_codes, entry.berth)
        entry.boat_codes = new_boat
        entry.berth = new_berth
        if new_boat != old_boat or new_berth != old_berth:
            updated += 1

    by_day: dict[date, list[ScheduleEntry]] = defaultdict(list)
    for entry in entries:
        if not (entry.boat_codes or "").strip():
            by_day[entry.schedule_date].append(entry)

    for day_rows in by_day.values():
        day_rows.sort(key=lambda e: e.ship.lower())
        for idx, entry in enumerate(day_rows):
            new_boat = f"CPT-{chr(ord('A') + idx)}"
            if (entry.boat_codes or "").strip() != new_boat:
                entry.boat_codes = new_boat
                updated += 1

    if updated:
        db.commit()
    return updated
