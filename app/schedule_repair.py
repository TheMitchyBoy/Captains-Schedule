"""
Repair schedule rows where port berth codes were stored as boat/captain codes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.berth_utils import is_placeholder_boat_code, repair_boat_berth_value
from app.models import ScheduleEntry


def repair_schedule_berth_mixups(db: Session) -> int:
    """
    Move misclassified berth codes out of boat_codes into the berth column.

    Legacy CSV imports stored values like BERTH-2 in boat_codes; this moves them
    to the berth column. Auto-generated CPT-* placeholders are cleared — only
    real tour boat names (DrmC, 50/50, BW, etc.) are kept.

    Returns the number of rows updated.
    """
    entries = db.query(ScheduleEntry).all()
    updated = 0

    for entry in entries:
        old_boat = (entry.boat_codes or "").strip()
        old_berth = entry.berth
        new_boat, new_berth = repair_boat_berth_value(entry.boat_codes, entry.berth)
        if is_placeholder_boat_code(new_boat):
            new_boat = ""
        entry.boat_codes = new_boat
        entry.berth = new_berth
        if new_boat != old_boat or new_berth != old_berth:
            updated += 1

    if updated:
        db.commit()
    return updated
