"""
Scheduling constraints for captain/boat predictions.

Rules enforced when building daily forecasts:
  - A boat serves at most one cruise ship at a time (no overlapping assignments)
  - A captain/boat cannot be out twice simultaneously
  - Boats are considered in alphabetical order when assigning tours
  - Minimum 3-hour turnaround between the end of one tour and the start of the next
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

MIN_TURNAROUND_MINUTES = 180  # 3 hours between tours for the same boat


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
        # New tour starts too soon after a previous one ends.
        if start >= existing_end and start < existing_end + min_gap:
            return False
        # New tour ends too close before a later one starts.
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


def _can_assign_ship(
    ship_timeline: list[tuple[int, int]],
    start: int,
    end: int,
) -> bool:
    """A cruise ship is served by at most one boat at a time."""
    for existing_start, existing_end in ship_timeline:
        if _intervals_overlap(existing_start, existing_end, start, end):
            return False
    return True


def _schedule_day(candidates: list[ShiftCandidate]) -> list[ShiftCandidate]:
    """
    Build a feasible daily schedule from pattern candidates.

    Groups by ship/time slot, then assigns boats in alphabetical order subject
    to overlap and turnaround constraints.
    """
    if not candidates:
        return []

    slots: dict[tuple[str, str, str], list[ShiftCandidate]] = defaultdict(list)
    for candidate in candidates:
        key = (candidate.ship, candidate.checkin_time, candidate.return_time)
        slots[key].append(candidate)

    # Process ship tours chronologically.
    sorted_slots = sorted(
        slots.items(),
        key=lambda item: (
            _time_to_minutes(item[0][1]) or 9999,
            item[0][0].lower(),
        ),
    )

    scheduled: list[ShiftCandidate] = []
    boat_timelines: dict[str, list[tuple[int, int]]] = defaultdict(list)
    ship_timelines: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for (ship, checkin, return_time), slot_candidates in sorted_slots:
        start = _time_to_minutes(checkin)
        end = _time_to_minutes(return_time)
        if start is None or end is None:
            continue
        if end <= start:
            end += 24 * 60

        # Boats considered in alphabetical order; higher confidence wins ties.
        slot_candidates.sort(key=lambda c: (c.boat_code.lower(), -c.confidence))

        for candidate in slot_candidates:
            if not _can_assign_boat(boat_timelines[candidate.boat_code], start, end):
                continue
            if not _can_assign_ship(ship_timelines[ship], start, end):
                continue

            boat_timelines[candidate.boat_code].append((start, end))
            ship_timelines[ship].append((start, end))
            scheduled.append(candidate)
            break

    scheduled.sort(
        key=lambda c: (
            _time_to_minutes(c.checkin_time) or 9999,
            c.boat_code.lower(),
            c.ship.lower(),
        )
    )
    return scheduled


def apply_scheduling_constraints(candidates: list[ShiftCandidate]) -> list[ShiftCandidate]:
    """Apply daily scheduling rules across all candidate predictions."""
    by_day: dict[date, list[ShiftCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_day[candidate.schedule_date].append(candidate)

    result: list[ShiftCandidate] = []
    for day in sorted(by_day):
        result.extend(_schedule_day(by_day[day]))
    return result
