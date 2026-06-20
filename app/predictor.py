"""
Captain schedule prediction engine.

Learns recurring assignment patterns from historical dispatch data and projects
them forward to answer: "When will captain X likely work next?"

Core approach:
  1. After each XML upload, aggregate historical shifts by (captain, weekday, ship, times)
  2. Compute confidence = how often this assignment occurs vs. all shifts for that captain on that weekday
  3. For each future date, apply matching weekday patterns and attach busy-day scores
  4. Enforce scheduling constraints: multiple boats may serve the same ship,
     no boat overlap, alphabetical boat dispatch order, and 3-hour minimum
     turnaround between tours for the same boat
  5. Optionally use OpenAI to forecast ships in port and suggest captain assignments
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai_predictor import (
    ai_forecast_ships_for_dates,
    ai_suggest_captain_shifts,
    clear_ai_prediction_cache,
    dates_without_actual_data,
    get_ai_ship_names_for_date,
)
from app.config import get_settings
from app.database import sql_day_of_week
from app.models import CaptainPattern, ScheduleEntry
from app.scheduler import ShiftCandidate, apply_scheduling_constraints
from app.schemas import CaptainPrediction, CaptainSummary
from app.ship_data import estimate_daily_passengers, get_ship_capacity, normalize_ship_name

from app.berth_utils import split_dispatch_codes

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def learn_expected_boat_counts(db: Session) -> dict[tuple[str, int, str, str], int]:
    """
    Learn how many tour boats each cruise ship typically needs per time slot.

    Uses the maximum observed boat count per (ship, weekday, checkin, return).
    Larger ships that need more boats will have higher counts.
    """
    per_slot: dict[tuple[str, int, str, str], list[int]] = defaultdict(list)
    for entry in db.query(ScheduleEntry).all():
        codes = split_dispatch_codes(entry.boat_codes)
        if not codes:
            continue
        key = (
            entry.ship,
            entry.schedule_date.weekday(),
            entry.checkin_time,
            entry.return_time,
        )
        per_slot[key].append(len(codes))

    return {key: max(counts) for key, counts in per_slot.items()}


def _expected_boats_for_ship_on_day(
    slot_boat_counts: dict[tuple[str, int, str, str], int],
    ship: str,
    dow: int,
) -> int:
    """Return the typical boat count for a ship on a weekday (max across its slots)."""
    counts = [
        count
        for (slot_ship, slot_dow, _checkin, _return_time), count in slot_boat_counts.items()
        if slot_ship == ship and slot_dow == dow
    ]
    return max(counts) if counts else 1


def _boats_assigned_to_ship(
    candidates: list[ShiftCandidate],
    target_date: date,
    ship: str,
) -> int:
    """Count predicted boats already assigned to a ship on a given date."""
    return sum(
        1
        for candidate in candidates
        if candidate.schedule_date == target_date
        and _ship_matches_expected(ship, candidate.ship)
    )


def ensure_prediction_patterns(db: Session) -> int:
    """
    Rebuild learned captain patterns from stored schedule rows when needed.

    Returns the current pattern count after ensuring patterns exist.
    """
    entry_count = db.query(func.count(ScheduleEntry.id)).scalar() or 0
    if not entry_count:
        return 0

    pattern_count = db.query(func.count(CaptainPattern.id)).scalar() or 0
    if pattern_count == 0:
        return rebuild_patterns(db)
    return pattern_count


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
        for code in split_dispatch_codes(entry.boat_codes):
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


def _ship_matches_expected(expected: str, candidate: str) -> bool:
    """Return True when two ship names likely refer to the same vessel."""
    a = normalize_ship_name(expected)
    b = normalize_ship_name(candidate)
    if a == b or a in b or b in a:
        return True
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio() >= 0.88


def _predict_ships_for_date(
    db: Session,
    schedule_date: date,
    ai_forecasts: dict[date, list[dict[str, str]]] | None = None,
) -> list[str]:
    """
    Determine which ships are expected in port on a date.

    Uses actual uploaded data when available; otherwise AI forecasts (if provided),
    then falls back to the most frequently seen ships on the same weekday.
    """
    actual = _ships_on_date(db, schedule_date)
    if actual:
        return actual

    if ai_forecasts:
        ai_ships = get_ai_ship_names_for_date(db, schedule_date, ai_forecasts)
        if ai_ships:
            return ai_ships

    dow = schedule_date.weekday()
    historical = (
        db.query(ScheduleEntry.ship, func.count(ScheduleEntry.id).label("cnt"))
        .filter(sql_day_of_week(ScheduleEntry.schedule_date, dow))
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
    use_ai: bool = True,
) -> tuple[list[CaptainPrediction], dict]:
    """
    Generate future captain shift predictions for the next N days.

    Raw pattern matches are filtered through scheduling constraints so that:
      - Each boat serves only one cruise ship at a time
      - Multiple boats may serve the same cruise ship when history shows it needs them
      - Boats are assigned in alphabetical order when multiple are eligible
      - At least 3 hours separate consecutive tours for the same boat

    When OpenAI is configured and use_ai=True, the API also:
      - Forecasts ships in port for dates without uploaded schedule data
      - Suggests captain assignments for ships not covered by learned patterns

    Returns (predictions, metadata) where metadata includes ai_assisted flag.
    """
    ai_meta = {"ai_assisted": False, "ai_ship_forecasts": 0, "ai_captain_suggestions": 0, "message": None}

    patterns = db.query(CaptainPattern).all()
    if not patterns:
        ensure_prediction_patterns(db)
        patterns = db.query(CaptainPattern).all()
    if not patterns:
        return [], ai_meta

    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    ai_forecasts: dict[date, list[dict[str, str]]] = {}
    ai_enabled = use_ai and get_settings().openai.is_configured

    if ai_enabled:
        missing_dates = dates_without_actual_data(db, today, end_date)
        if missing_dates:
            ai_forecasts = ai_forecast_ships_for_dates(db, missing_dates)
            if ai_forecasts:
                ai_meta["ai_assisted"] = True
                ai_meta["ai_ship_forecasts"] = len(ai_forecasts)
                ai_meta["message"] = f"AI forecast ships for {len(ai_forecasts)} dates"

    all_boat_codes = sorted({p.boat_code for p in patterns})
    slot_boat_counts = learn_expected_boat_counts(db)
    candidates: list[ShiftCandidate] = []

    for offset in range(days_ahead + 1):
        target = today + timedelta(days=offset)
        dow = target.weekday()
        day_patterns = [p for p in patterns if p.day_of_week == dow]
        if boat_code:
            day_patterns = [p for p in day_patterns if p.boat_code == boat_code]

        ships = _predict_ships_for_date(db, target, ai_forecasts)
        passenger_total, busy_score = estimate_daily_passengers(db, ships)

        for pattern in day_patterns:
            if pattern.confidence < min_confidence:
                continue
            # When we know which ships are in port, prefer patterns for those vessels.
            if ships and not any(_ship_matches_expected(ship, pattern.ship) for ship in ships):
                continue

            ship_capacity = get_ship_capacity(db, pattern.ship).passenger_capacity
            candidates.append(
                ShiftCandidate(
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

        # AI captain suggestions for ships that still need more boats.
        if ai_enabled and ships:
            undercovered: list[dict[str, str | int]] = []
            for ship in ships:
                expected = _expected_boats_for_ship_on_day(slot_boat_counts, ship, dow)
                assigned = _boats_assigned_to_ship(candidates, target, ship)
                if assigned < expected:
                    undercovered.append(
                        {
                            "ship": ship,
                            "boats_needed": expected - assigned,
                            "boats_expected": expected,
                        }
                    )
            if undercovered:
                ships_with_times = ai_forecasts.get(target) or [
                    {"ship": s, "checkin_time": "7:00 AM", "return_time": "3:00 PM"} for s in ships
                ]
                ai_candidates = ai_suggest_captain_shifts(
                    db,
                    target,
                    ships_with_times,
                    undercovered,
                    all_boat_codes if not boat_code else [boat_code],
                )
                if ai_candidates:
                    ai_meta["ai_assisted"] = True
                    ai_meta["ai_captain_suggestions"] += len(ai_candidates)
                    if not ai_meta["message"]:
                        ai_meta["message"] = "AI suggested captain assignments for under-covered ships"
                    candidates.extend(ai_candidates)

    scheduled = apply_scheduling_constraints(candidates, slot_boat_counts=slot_boat_counts)

    predictions = [
        CaptainPrediction(
            boat_code=c.boat_code,
            schedule_date=c.schedule_date,
            day_of_week=c.day_of_week,
            ship=c.ship,
            checkin_time=c.checkin_time,
            return_time=c.return_time,
            confidence=c.confidence,
            passenger_estimate=c.passenger_estimate,
            busy_score=c.busy_score,
            source=c.source,
        )
        for c in scheduled
    ]

    predictions.sort(key=lambda p: (p.schedule_date, p.checkin_time, p.boat_code))
    return predictions, ai_meta


def get_captain_summaries(
    db: Session, days_ahead: int = 90, use_ai: bool = True
) -> tuple[list[CaptainSummary], dict]:
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
    all_preds, combined_meta = predict_captain_schedule(db, days_ahead=days_ahead, use_ai=use_ai)
    preds_by_code: dict[str, list[CaptainPrediction]] = defaultdict(list)
    for pred in all_preds:
        preds_by_code[pred.boat_code].append(pred)

    for (code,) in codes:
        historical = (
            db.query(func.sum(CaptainPattern.occurrence_count))
            .filter(CaptainPattern.boat_code == code)
            .scalar()
            or 0
        )
        preds = sorted(
            preds_by_code.get(code, []),
            key=lambda p: (p.schedule_date, p.checkin_time, p.boat_code),
        )
        next_shift = preds[0] if preds else None
        summaries.append(
            CaptainSummary(
                boat_code=code,
                total_historical_shifts=int(historical),
                predicted_shifts=len(preds),
                next_shift=next_shift,
            )
        )

    return summaries, combined_meta


def get_busy_calendar(db: Session, days_ahead: int = 90, use_ai: bool = True) -> tuple[list[dict], dict]:
    """
    Build a day-by-day calendar of estimated port busyness.

    Each entry includes ship count, passenger estimate, busy score (0–1), and
    whether actual uploaded schedule data exists for that date.
    """
    today = date.today()
    end_date = today + timedelta(days=days_ahead)
    ai_meta = {"ai_assisted": False, "ai_ship_forecasts": 0, "message": None}
    ai_forecasts: dict[date, list[dict[str, str]]] = {}

    if use_ai and get_settings().openai.is_configured:
        missing_dates = dates_without_actual_data(db, today, end_date)
        if missing_dates:
            ai_forecasts = ai_forecast_ships_for_dates(db, missing_dates)
            if ai_forecasts:
                ai_meta["ai_assisted"] = True
                ai_meta["ai_ship_forecasts"] = len(ai_forecasts)
                ai_meta["message"] = f"AI forecast ships for {len(ai_forecasts)} dates"

    calendar: list[dict] = []

    for offset in range(days_ahead + 1):
        target = today + timedelta(days=offset)
        ships = _predict_ships_for_date(db, target, ai_forecasts)
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
                "ai_forecast": target in ai_forecasts and actual_count == 0,
            }
        )

    return calendar, ai_meta
