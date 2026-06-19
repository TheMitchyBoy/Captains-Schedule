"""
Remove duplicate schedule rows created by repeated uploads.

Duplicates are rows with the same date, ship, check-in, and return times but
different boat_codes values (legacy unique constraint included boat_codes).
The best row is kept and boat/berth data is merged from the rest.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.berth_utils import is_placeholder_boat_code, merge_dispatch_codes, repair_boat_berth_value
from app.models import ScheduleEntry


def _row_quality(entry: ScheduleEntry) -> tuple[int, int, int, int]:
    """Higher is better when picking the row to keep."""
    boat, berth = repair_boat_berth_value(entry.boat_codes, entry.berth)
    merged_boats = merge_dispatch_codes(boat)
    has_boats = 1 if merged_boats else 0
    has_berth = 1 if berth else 0
    not_placeholder = 0 if is_placeholder_boat_code(entry.boat_codes) else 1
    return (has_boats, not_placeholder, has_berth, entry.id or 0)


def deduplicate_schedule_entries(db: Session) -> dict[str, int]:
    """
    Merge and delete duplicate schedule rows.

    Returns counts: duplicate_groups, rows_deleted, rows_remaining.
    """
    entries = db.query(ScheduleEntry).order_by(ScheduleEntry.id).all()
    groups: dict[tuple, list[ScheduleEntry]] = defaultdict(list)

    for entry in entries:
        key = (
            entry.schedule_date,
            entry.ship.strip().lower(),
            entry.checkin_time,
            entry.return_time,
        )
        groups[key].append(entry)

    duplicate_groups = 0
    rows_deleted = 0

    for rows in groups.values():
        if len(rows) <= 1:
            continue

        duplicate_groups += 1
        rows.sort(key=_row_quality, reverse=True)
        keeper = rows[0]

        boat, berth = repair_boat_berth_value(keeper.boat_codes, keeper.berth)
        keeper.boat_codes = boat
        keeper.berth = berth

        for duplicate in rows[1:]:
            dup_boat, dup_berth = repair_boat_berth_value(duplicate.boat_codes, duplicate.berth)
            keeper.boat_codes = merge_dispatch_codes(keeper.boat_codes, dup_boat)[:255]
            if not keeper.berth and dup_berth:
                keeper.berth = dup_berth
            if not keeper.ship_count and duplicate.ship_count:
                keeper.ship_count = duplicate.ship_count
            db.delete(duplicate)
            rows_deleted += 1

        if not keeper.boat_codes:
            keeper.boat_codes = ""

    if rows_deleted:
        db.commit()

    remaining = db.query(ScheduleEntry).count()
    return {
        "duplicate_groups": duplicate_groups,
        "rows_deleted": rows_deleted,
        "rows_remaining": remaining,
    }
