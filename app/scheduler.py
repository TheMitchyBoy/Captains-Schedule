"""
Scheduling constraints for captain/boat predictions.

Rules enforced when building daily forecasts:
  - A boat serves at most one cruise ship at a time (no overlapping assignments)
  - Multiple tour boats may serve the same cruise ship at the same time
  - Boats are considered in alphabetical order when assigning tours
  - Minimum 3-hour turnaround between the end of one tour and the start of the next
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

MIN_TURNAROUND_MINUTES = 180  # 3 hours between tours for the same boat

# (ship, weekday, checkin_time, return_time) -> expected tour boat count
SlotBoatCounts = dict[tuple[str, int, str, str], int]


@dataclass
class ShiftCandidate:
    """Internal candidate shift before constraint filtering."""

    boat_code: str
    schedule_date: date
    day_of_week: str
    ship: str
    checkin_time: str
    return_time: str
    confidence: float
    passenger_estimate: int | None
    busy_score: float
    source: str = "pattern"


def _time_to_minutes(time_str: str) -> int | None:
    """Parse a time string to minutes from midnight (supports HH:MM and AM/PM)."""
    if not time_str:
        return None
    text = time_str.strip()

    match = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute

    match = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", text, re.IGNORECASE)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        period = match.group(3).lower()
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute

    match = re.match(r"^(\d{1,2})\s*(am|pm)$", text, re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        period = match.group(2).lower()
        if period == "pm" and hour != 12:
            hour += 12
        elif period == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23:
            return hour * 60

    return None


def _intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    """Return True if two time intervals overlap."""
    return start_a < end_b and start_b < end_a


def _respects_turnaround(
    existing: list[tuple[int, int]],
    start: int,
    end: int,
    min_gap: int = MIN_TURNAROUND_MINUTES,
) -> bool:
    """Check that a new interval does not violate the minimum turnaround gap."""
    for existing_start, existing_end in existing:
        if _intervals_overlap(existing_start, existing_end, start, end):
            return False
        if start >= existing_end and start < existing_end + min_gap:
            return False
        if end <= existing_start and end > existing_start - min_gap:
            return False
    return True


def _can_assign_boat(
    boat_timeline: list[tuple[int, int]],
    start: int,
    end: int,
) -> bool:
    """A boat may only serve one ship at a time with 3h between consecutive tours."""
    return _respects_turnaround(boat_timeline, start, end)


def _schedule_day(
    candidates: list[ShiftCandidate],
    slot_boat_counts: SlotBoatCounts | None = None,
    day_of_week: int | None = None,
) -> list[ShiftCandidate]:
    """
    Build a feasible daily schedule from pattern candidates.

    Groups by ship/time slot, then assigns boats in alphabetical order.
    Larger ships may receive multiple boats concurrently when history shows
    they typically need more than one tour boat.
    """
    if not candidates:
        return []

    slots: dict[tuple[str, str, str], list[ShiftCandidate]] = defaultdict(list)
    for candidate in candidates:
        key = (candidate.ship, candidate.checkin_time, candidate.return_time)
        slots[key].append(candidate)

    sorted_slots = sorted(
        slots.items(),
        key=lambda item: (
            _time_to_minutes(item[0][1]) or 9999,
            item[0][0].lower(),
        ),
    )

    scheduled: list[ShiftCandidate] = []
    boat_timelines: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for (ship, checkin, return_time), slot_candidates in sorted_slots:
        start = _time_to_minutes(checkin)
        end = _time_to_minutes(return_time)
        if start is None or end is None:
            continue
        if end <= start:
            end += 24 * 60

        expected_boats = 1
        if slot_boat_counts is not None and day_of_week is not None:
            expected_boats = max(
                1,
                slot_boat_counts.get((ship, day_of_week, checkin, return_time), 1),
            )

        slot_candidates.sort(key=lambda c: (c.boat_code.lower(), -c.confidence))

        assigned = 0
        for candidate in slot_candidates:
            if assigned >= expected_boats:
                break
            if not _can_assign_boat(boat_timelines[candidate.boat_code], start, end):
                continue

            boat_timelines[candidate.boat_code].append((start, end))
            scheduled.append(candidate)
            assigned += 1

    scheduled.sort(
        key=lambda c: (
            _time_to_minutes(c.checkin_time) or 9999,
            c.ship.lower(),
            c.boat_code.lower(),
        )
    )
    return scheduled


def apply_scheduling_constraints(
    candidates: list[ShiftCandidate],
    slot_boat_counts: SlotBoatCounts | None = None,
) -> list[ShiftCandidate]:
    """Apply daily scheduling rules across all candidate predictions."""
    by_day: dict[date, list[ShiftCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_day[candidate.schedule_date].append(candidate)

    result: list[ShiftCandidate] = []
    for day in sorted(by_day):
        result.extend(
            _schedule_day(
                by_day[day],
                slot_boat_counts=slot_boat_counts,
                day_of_week=day.weekday(),
            )
        )
    return result
