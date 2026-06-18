"""
Captain schedule prediction engine.

Learns recurring assignment patterns from historical dispatch data and projects
them forward to answer: "When will captain X likely work next?"

Core approach:
  1. After each XML upload, aggregate historical shifts by (captain, weekday, ship, times)
  2. Compute confidence = how often this assignment occurs vs. all shifts for that captain on that weekday
  3. For each future date, apply matching weekday patterns and attach busy-day scores
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CaptainPattern, ScheduleEntry
from app.schemas import CaptainPrediction, CaptainSummary
from app.ship_data import estimate_daily_passengers, get_ship_capacity

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _split_boat_codes(boat_codes: str) -> list[str]:
    """Split a boat_codes field into individual operator codes (handles comma and slash separators)."""
    return [c.strip() for c in boat_codes.replace("/", ",").split(",") if c.strip()]


def rebuild_patterns(db: Session) -> int:
    """
    Recompute all captain patterns from the full schedule history.

    Called after every XML upload so predictions always reflect the complete
    dataset. Deletes existing patterns and rebuilds from scratch.
    """
    db.query(CaptainPattern).delete()
    db.commit()

    entries = db.query(ScheduleEntry).order_by(ScheduleEntry.schedule_date).all()
    if not entries:
        return 0

    aggregates: dict[tuple, dict] = defaultdict(
        lambda: {"count": 0, "last_seen": date.min}
    )

    for entry in entries:
        dow = entry.schedule_date.weekday()
        for code in _split_boat_codes(entry.boat_codes):
            key = (code, dow, entry.ship, entry.checkin_time, entry.return_time)
            aggregates[key]["count"] += 1
            if entry.schedule_date > aggregates[key]["last_seen"]:
                aggregates[key]["last_seen"] = entry.schedule_date

    total_by_captain_dow: dict[tuple[str, int], int] = defaultdict(int)
    for (code, dow, _ship, _ci, _rt), data in aggregates.items():
        total_by_captain_dow[(code, dow)] += data["count"]

    patterns_added = 0
    # Confidence = this assignment's count / all assignments for this captain on this weekday
    for (code, dow, ship, checkin, return_time), data in aggregates.items():
        total = total_by_captain_dow[(code, dow)]
        confidence = round(data["count"] / max(total, 1), 3)
        pattern = CaptainPattern(
            boat_code=code,
            day_of_week=dow,
            ship=ship,
            checkin_time=checkin,
            return_time=return_time,
            occurrence_count=data["count"],
            confidence=confidence,
            last_seen=data["last_seen"],
        )
        db.add(pattern)
        patterns_added += 1

    db.commit()
    return patterns_added


def _ships_on_date(db: Session, schedule_date: date) -> list[str]:
    """Return distinct ship names that have actual schedule data for a given date."""
    rows = (
        db.query(ScheduleEntry.ship)
        .filter(ScheduleEntry.schedule_date == schedule_date)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _predict_ships_for_date(db: Session, schedule_date: date) -> list[str]:
    """
    Determine which ships are expected in port on a date.

    Uses actual uploaded data when available; otherwise falls back to the most
    frequently seen ships on the same weekday in historical data.
    """
    actual = _ships_on_date(db, schedule_date)
    if actual:
        return actual

    dow = schedule_date.weekday()
    # SQLite strftime('%w'): 0=Sunday … 6=Saturday; Python weekday(): 0=Monday … 6=Sunday
    historical = (
        db.query(ScheduleEntry.ship, func.count(ScheduleEntry.id).label("cnt"))
        .filter(func.strftime("%w", ScheduleEntry.schedule_date) == str((dow + 1) % 7))
        .group_by(ScheduleEntry.ship)
        .order_by(func.count(ScheduleEntry.id).desc())
        .limit(8)
        .all()
    )
    return [ship for ship, _ in historical]


def predict_captain_schedule(
    db: Session,
    boat_code: str | None = None,
    days_ahead: int = 90,
    min_confidence: float = 0.15,
) -> list[CaptainPrediction]:
    """
    Generate future captain shift predictions for the next N days.

    For each day, matches learned weekday patterns and attaches ship capacity
    and busy-day scores. Optionally filters to a single boat_code and minimum
    confidence threshold.
    """
    patterns = db.query(CaptainPattern).all()
    if not patterns:
        rebuild_patterns(db)
        patterns = db.query(CaptainPattern).all()
    if not patterns:
        return []

    today = date.today()
    predictions: list[CaptainPrediction] = []

    for offset in range(days_ahead + 1):
        target = today + timedelta(days=offset)
        dow = target.weekday()
        day_patterns = [p for p in patterns if p.day_of_week == dow]
        if boat_code:
            day_patterns = [p for p in day_patterns if p.boat_code == boat_code]

        ships = _predict_ships_for_date(db, target)
        passenger_total, busy_score = estimate_daily_passengers(db, ships)

        for pattern in day_patterns:
            if pattern.confidence < min_confidence:
                continue

            ship_capacity = get_ship_capacity(db, pattern.ship).passenger_capacity
            predictions.append(
                CaptainPrediction(
                    boat_code=pattern.boat_code,
                    schedule_date=target,
                    day_of_week=DAY_NAMES[dow],
                    ship=pattern.ship,
                    checkin_time=pattern.checkin_time,
                    return_time=pattern.return_time,
                    confidence=pattern.confidence,
                    passenger_estimate=ship_capacity,
                    busy_score=busy_score,
                    source="pattern",
                )
            )

    predictions.sort(key=lambda p: (p.schedule_date, -p.confidence, p.boat_code))
    return predictions


def get_captain_summaries(db: Session, days_ahead: int = 90) -> list[CaptainSummary]:
    """
    Build a per-captain overview: historical shift count, predicted count, and next shift.

    Used by the dashboard "Captain Overview" tab.
    """
    codes = (
        db.query(CaptainPattern.boat_code)
        .distinct()
        .order_by(CaptainPattern.boat_code)
        .all()
    )
    if not codes:
        rebuild_patterns(db)
        codes = (
            db.query(CaptainPattern.boat_code)
            .distinct()
            .order_by(CaptainPattern.boat_code)
            .all()
        )

    summaries: list[CaptainSummary] = []
    for (code,) in codes:
        historical = (
            db.query(func.sum(CaptainPattern.occurrence_count))
            .filter(CaptainPattern.boat_code == code)
            .scalar()
            or 0
        )
        preds = predict_captain_schedule(db, boat_code=code, days_ahead=days_ahead)
        next_shift = preds[0] if preds else None
        summaries.append(
            CaptainSummary(
                boat_code=code,
                total_historical_shifts=int(historical),
                predicted_shifts=len(preds),
                next_shift=next_shift,
            )
        )

    return summaries


def get_busy_calendar(db: Session, days_ahead: int = 90) -> list[dict]:
    """
    Build a day-by-day calendar of estimated port busyness.

    Each entry includes ship count, passenger estimate, busy score (0–1), and
    whether actual uploaded schedule data exists for that date.
    """
    today = date.today()
    calendar: list[dict] = []

    for offset in range(days_ahead + 1):
        target = today + timedelta(days=offset)
        ships = _predict_ships_for_date(db, target)
        passenger_total, busy_score = estimate_daily_passengers(db, ships)
        ship_count = len(ships)

        actual_count = (
            db.query(func.count(ScheduleEntry.id))
            .filter(ScheduleEntry.schedule_date == target)
            .scalar()
            or 0
        )

        calendar.append(
            {
                "date": target.isoformat(),
                "day_of_week": DAY_NAMES[target.weekday()],
                "ship_count": ship_count,
                "passenger_estimate": passenger_total,
                "busy_score": busy_score,
                "has_actual_data": actual_count > 0,
            }
        )

    return calendar
