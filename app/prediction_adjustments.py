"""Persist and apply user/AI forecast overrides."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models import PredictionAdjustment
from app.mms_dispatch_parser import resolve_dispatch_ship_name
from app.schemas import CaptainPrediction
from app.ship_data import normalize_ship_name
from app.xml_cleaner import normalize_time_24h

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _ships_match(a: str, b: str) -> bool:
    left = normalize_ship_name(a)
    right = normalize_ship_name(b)
    return left == right or left in right or right in left


def _prediction_matches_removal(pred: CaptainPrediction, adj: PredictionAdjustment) -> bool:
    if pred.schedule_date != adj.schedule_date:
        return False
    if pred.boat_code.upper() != adj.boat_code.upper():
        return False
    if adj.ship and not _ships_match(pred.ship, adj.ship):
        return False
    if adj.checkin_time and pred.checkin_time != adj.checkin_time:
        return False
    if adj.return_time and pred.return_time != adj.return_time:
        return False
    return True


def apply_prediction_adjustments(
    predictions: list[CaptainPrediction],
    adjustments: list[PredictionAdjustment],
) -> list[CaptainPrediction]:
    """Merge stored add/remove adjustments into a forecast list."""
    if not adjustments:
        return predictions

    removals = [adj for adj in adjustments if adj.action == "remove"]
    adds = [adj for adj in adjustments if adj.action == "add"]

    filtered = [
        pred
        for pred in predictions
        if not any(_prediction_matches_removal(pred, adj) for adj in removals)
    ]

    existing_keys = {
        (p.schedule_date, p.boat_code.upper(), normalize_ship_name(p.ship), p.checkin_time, p.return_time)
        for p in filtered
    }

    for adj in adds:
        key = (
            adj.schedule_date,
            adj.boat_code.upper(),
            normalize_ship_name(adj.ship),
            adj.checkin_time,
            adj.return_time,
        )
        if key in existing_keys:
            continue
        filtered.append(
            CaptainPrediction(
                boat_code=adj.boat_code,
                schedule_date=adj.schedule_date,
                day_of_week=DAY_NAMES[adj.schedule_date.weekday()],
                ship=adj.ship,
                checkin_time=adj.checkin_time,
                return_time=adj.return_time,
                confidence=1.0,
                passenger_estimate=None,
                busy_score=0.0,
                source="adjustment",
            )
        )
        existing_keys.add(key)

    filtered.sort(key=lambda p: (p.schedule_date, p.checkin_time, p.boat_code))
    return filtered


def list_prediction_adjustments(db: Session) -> list[PredictionAdjustment]:
    return (
        db.query(PredictionAdjustment)
        .order_by(PredictionAdjustment.schedule_date, PredictionAdjustment.boat_code)
        .all()
    )


def clear_prediction_adjustments(db: Session) -> int:
    count = db.query(PredictionAdjustment).count()
    db.query(PredictionAdjustment).delete()
    db.commit()
    return count


def _normalize_action_fields(
    *,
    schedule_date: date,
    boat_code: str,
    ship: str,
    checkin_time: str,
    return_time: str,
    action: str,
) -> tuple[date, str, str, str, str, str]:
    normalized_action = action.strip().lower()
    if normalized_action not in {"add", "remove"}:
        raise ValueError(f"Unsupported adjustment action: {action}")

    boat = boat_code.strip()
    if not boat:
        raise ValueError("boat_code is required")

    resolved_ship = resolve_dispatch_ship_name(ship.strip())
    if not resolved_ship:
        raise ValueError("ship is required")

    checkin = ""
    return_norm = ""
    if normalized_action == "add":
        checkin_value, checkin_err = normalize_time_24h(checkin_time.strip())
        return_value, return_err = normalize_time_24h(return_time.strip())
        if checkin_err or return_err or not checkin_value or not return_value:
            raise ValueError("Valid check-in and return times are required for add actions")
        checkin = checkin_value
        return_norm = return_value

    return normalized_action, schedule_date, boat, resolved_ship, checkin, return_norm


def create_prediction_adjustment(
    db: Session,
    *,
    action: str,
    schedule_date: date,
    boat_code: str,
    ship: str,
    checkin_time: str = "",
    return_time: str = "",
    note: str | None = None,
) -> PredictionAdjustment:
    normalized_action, sched, boat, resolved_ship, checkin, return_norm = _normalize_action_fields(
        schedule_date=schedule_date,
        boat_code=boat_code,
        ship=ship,
        checkin_time=checkin_time,
        return_time=return_time,
        action=action,
    )
    entry = PredictionAdjustment(
        action=normalized_action,
        schedule_date=sched,
        boat_code=boat,
        ship=resolved_ship,
        checkin_time=checkin,
        return_time=return_norm,
        note=note,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def delete_prediction_adjustment(db: Session, adjustment_id: int) -> None:
    entry = db.query(PredictionAdjustment).filter(PredictionAdjustment.id == adjustment_id).first()
    if entry is None:
        raise ValueError(f"Prediction adjustment {adjustment_id} not found")
    db.delete(entry)
    db.commit()
