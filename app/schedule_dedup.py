"""
Remove duplicate schedule rows created by repeated uploads.

Duplicates are rows with the same date, ship (including spelling variants),
check-in, and return times. The best row is kept and boat/berth data is merged.

Also merges port-schedule rows (berth only, no tour boats) into matching
tour dispatch rows for the same ship on the same day.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.berth_utils import is_placeholder_boat_code, merge_dispatch_codes, repair_boat_berth_value
from app.models import ScheduleEntry
from app.ship_identity import best_ship_display_name, canonical_ship_key


def _row_quality(entry: ScheduleEntry) -> tuple[int, int, int, int]:
    """Higher is better when picking the row to keep."""
    boat, berth = repair_boat_berth_value(entry.boat_codes, entry.berth)
    merged_boats = merge_dispatch_codes(boat)
    has_boats = 1 if merged_boats else 0
    has_berth = 1 if berth else 0
    not_placeholder = 0 if is_placeholder_boat_code(entry.boat_codes) else 1
    return (has_boats, not_placeholder, has_berth, entry.id or 0)


def _dedupe_key(entry: ScheduleEntry) -> tuple:
    return (
        entry.schedule_date,
        canonical_ship_key(entry.ship),
        entry.checkin_time,
        entry.return_time,
    )


def _merge_rows(keeper: ScheduleEntry, duplicate: ScheduleEntry) -> None:
    dup_boat, dup_berth = repair_boat_berth_value(duplicate.boat_codes, duplicate.berth)
    keeper.boat_codes = merge_dispatch_codes(keeper.boat_codes, dup_boat)[:255]
    if not keeper.berth and dup_berth:
        keeper.berth = dup_berth
    if not keeper.ship_count and duplicate.ship_count:
        keeper.ship_count = duplicate.ship_count


def _merge_exact_time_duplicates(db: Session, entries: list[ScheduleEntry]) -> tuple[int, int]:
    groups: dict[tuple, list[ScheduleEntry]] = defaultdict(list)
    for entry in entries:
        groups[_dedupe_key(entry)].append(entry)

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
            _merge_rows(keeper, duplicate)
            db.delete(duplicate)
            rows_deleted += 1

        db.flush()
        keeper.ship = best_ship_display_name([row.ship for row in rows])

        if not keeper.boat_codes:
            keeper.boat_codes = ""

    return duplicate_groups, rows_deleted


def _merge_port_schedule_rows(db: Session, entries: list[ScheduleEntry]) -> tuple[int, int]:
    """Merge berth-only port schedule rows into tour dispatch rows for the same ship/day."""
    groups: dict[tuple, list[ScheduleEntry]] = defaultdict(list)
    for entry in entries:
        groups[(entry.schedule_date, canonical_ship_key(entry.ship))].append(entry)

    duplicate_groups = 0
    rows_deleted = 0

    for rows in groups.values():
        if len(rows) <= 1:
            continue

        with_boats = []
        without_boats = []
        for row in rows:
            boat, _ = repair_boat_berth_value(row.boat_codes, row.berth)
            if merge_dispatch_codes(boat):
                with_boats.append(row)
            else:
                without_boats.append(row)

        if not with_boats or not without_boats:
            continue

        duplicate_groups += 1
        keeper = max(with_boats, key=_row_quality)

        for duplicate in without_boats:
            _merge_rows(keeper, duplicate)
            db.delete(duplicate)
            rows_deleted += 1

        db.flush()
        keeper.ship = best_ship_display_name([row.ship for row in with_boats + without_boats])

    return duplicate_groups, rows_deleted


def deduplicate_schedule_entries(db: Session) -> dict[str, int]:
    """
    Merge and delete duplicate schedule rows.

    Returns counts: duplicate_groups, rows_deleted, rows_remaining.
    """
    entries = db.query(ScheduleEntry).order_by(ScheduleEntry.id).all()

    exact_groups, exact_deleted = _merge_exact_time_duplicates(db, entries)
    if exact_deleted:
        db.flush()
        entries = db.query(ScheduleEntry).order_by(ScheduleEntry.id).all()

    port_groups, port_deleted = _merge_port_schedule_rows(db, entries)
    rows_deleted = exact_deleted + port_deleted

    if rows_deleted:
        db.commit()

    remaining = db.query(ScheduleEntry).count()
    return {
        "duplicate_groups": exact_groups + port_groups,
        "rows_deleted": rows_deleted,
        "rows_remaining": remaining,
    }
