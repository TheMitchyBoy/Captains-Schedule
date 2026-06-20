"""AI chat assistant for editing captain schedule predictions."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.ai_predictor import _build_history_summary, _call_openai_json, clear_ai_prediction_cache
from app.config import get_settings
from app.models import PredictionAdjustment
from app.mms_dispatch_parser import resolve_dispatch_ship_name
from app.prediction_adjustments import create_prediction_adjustment, list_prediction_adjustments
from app.predictor import predict_captain_schedule, rebuild_patterns
from app.schedule_update import create_schedule_entry
from app.schemas import CaptainPrediction

KNOWN_BOAT_CODES = (
    "BW, BWA, DrmC, 50/50, FNF, GH, HR, JR, LewE, LJ, ML, AriC, BF, BS, BoomA, SL, SR"
)


def _summarize_predictions(predictions: list[CaptainPrediction], limit: int = 40) -> str:
    if not predictions:
        return "No current forecast rows."
    lines = ["Current forecast (sample):"]
    for pred in predictions[:limit]:
        lines.append(
            f"- {pred.schedule_date.isoformat()} ({pred.day_of_week}): "
            f"{pred.boat_code} on {pred.ship} {pred.checkin_time}-{pred.return_time} "
            f"[{pred.source}, confidence {pred.confidence:.0%}]"
        )
    if len(predictions) > limit:
        lines.append(f"... and {len(predictions) - limit} more rows")
    return "\n".join(lines)


def _summarize_adjustments(adjustments: list[PredictionAdjustment]) -> str:
    if not adjustments:
        return "No saved prediction overrides yet."
    lines = ["Saved prediction overrides:"]
    for adj in adjustments[-20:]:
        if adj.action == "add":
            lines.append(
                f"- ADD {adj.schedule_date.isoformat()}: {adj.boat_code} on {adj.ship} "
                f"{adj.checkin_time}-{adj.return_time}"
            )
        else:
            lines.append(
                f"- REMOVE {adj.schedule_date.isoformat()}: {adj.boat_code} on {adj.ship}"
            )
    return "\n".join(lines)


def _extract_chat_payload(data: object) -> dict | None:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _parse_action(raw: dict, index: int) -> dict:
    action_type = str(raw.get("type", raw.get("action", ""))).strip().lower()
    if action_type not in {"add", "remove", "add_schedule", "learn_schedule"}:
        raise ValueError(f"Action {index}: unsupported type '{action_type}'")

    schedule_date_raw = raw.get("schedule_date") or raw.get("date")
    if not schedule_date_raw:
        raise ValueError(f"Action {index}: schedule_date is required")

    schedule_date = date.fromisoformat(str(schedule_date_raw))
    boat_code = str(raw.get("boat_code", raw.get("boat", ""))).strip()
    ship = str(raw.get("ship", raw.get("vessel", ""))).strip()
    if not boat_code:
        raise ValueError(f"Action {index}: boat_code is required")
    if not ship:
        raise ValueError(f"Action {index}: ship is required")

    return {
        "type": "add_schedule" if action_type in {"add_schedule", "learn_schedule"} else action_type,
        "schedule_date": schedule_date,
        "boat_code": boat_code,
        "ship": ship,
        "checkin_time": str(raw.get("checkin_time", raw.get("checkin", ""))).strip(),
        "return_time": str(raw.get("return_time", raw.get("return", ""))).strip(),
        "note": str(raw.get("note", "")).strip() or None,
    }


def _apply_actions(db: Session, actions: list[dict]) -> list[dict]:
    applied: list[dict] = []
    learned = False
    for action in actions:
        action_type = action["type"]
        if action_type == "add_schedule":
            entry = create_schedule_entry(
                db,
                schedule_date=action["schedule_date"],
                ship=action["ship"],
                checkin_time=action["checkin_time"],
                return_time=action["return_time"],
                boat_codes=action["boat_code"],
            )
            learned = True
            applied.append(
                {
                    "type": action_type,
                    "schedule_date": action["schedule_date"].isoformat(),
                    "boat_code": action["boat_code"],
                    "ship": entry.ship,
                    "checkin_time": entry.checkin_time,
                    "return_time": entry.return_time,
                }
            )
            continue

        adjustment = create_prediction_adjustment(
            db,
            action=action_type,
            schedule_date=action["schedule_date"],
            boat_code=action["boat_code"],
            ship=action["ship"],
            checkin_time=action["checkin_time"],
            return_time=action["return_time"],
            note=action.get("note"),
        )
        applied.append(
            {
                "type": action_type,
                "id": adjustment.id,
                "schedule_date": adjustment.schedule_date.isoformat(),
                "boat_code": adjustment.boat_code,
                "ship": adjustment.ship,
                "checkin_time": adjustment.checkin_time,
                "return_time": adjustment.return_time,
            }
        )

    if learned:
        rebuild_patterns(db)
        clear_ai_prediction_cache()

    return applied


def chat_with_prediction_assistant(
    db: Session,
    *,
    message: str,
    history: list[dict[str, str]] | None = None,
    days_ahead: int = 90,
) -> dict:
    """
    Send a user message to the AI assistant and apply any forecast changes it proposes.

    Returns assistant reply, applied actions, and a fresh forecast sample.
    """
    if not get_settings().openai.is_configured:
        return {
            "reply": "OpenAI is not configured. Set OPENAI_API_KEY to chat with the prediction assistant.",
            "actions_applied": [],
            "predictions_changed": 0,
            "predictions": [],
        }

    text = message.strip()
    if not text:
        raise ValueError("Message is required")

    predictions, _meta = predict_captain_schedule(db, days_ahead=days_ahead, use_ai=False)
    adjustments = list_prediction_adjustments(db)
    history = history or []

    today = date.today()
    end = today + timedelta(days=days_ahead)

    system = (
        "You are a Ketchikan Alaska cruise tour boat dispatch assistant embedded in a scheduling app. "
        "The user asks you to review or change captain/boat predictions. "
        "Return JSON only — no markdown — with this shape:\n"
        "{\n"
        '  "reply": "friendly explanation of what you did or why you could not",\n'
        '  "actions": [\n'
        "    {\n"
        '      "type": "add" | "remove" | "add_schedule",\n'
        '      "schedule_date": "YYYY-MM-DD",\n'
        '      "boat_code": "BW",\n'
        '      "ship": "Eurodam",\n'
        '      "checkin_time": "06:30",\n'
        '      "return_time": "10:45",\n'
        '      "note": "optional reason"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Use type=add to add or change a forecast row for a future date. "
        "Use type=remove to hide a forecast row (match by date, boat_code, and ship). "
        "Use type=add_schedule when the user wants to teach the system using a real historical "
        "dispatch row — that updates the database and improves future pattern learning. "
        "Times must be 24h HH:MM. Expand ship nicknames (C. Spirit -> Carnival Spirit). "
        f"Known boat codes include: {KNOWN_BOAT_CODES}. "
        "If the user only asks a question, return an empty actions array. "
        "When changing predictions, include concrete actions rather than telling the user to edit manually."
    )

    user_parts = [
        f"Today: {today.isoformat()}",
        f"Forecast horizon: {today.isoformat()} through {end.isoformat()}",
        _build_history_summary(db, max_entries=50),
        _summarize_predictions(predictions),
        _summarize_adjustments(adjustments),
    ]
    if history:
        user_parts.append("Recent chat:")
        for item in history[-8:]:
            role = item.get("role", "user")
            content = item.get("content", "")
            if content:
                user_parts.append(f"{role}: {content}")
    user_parts.append(f"User message: {text}")

    data = _call_openai_json(system, "\n\n".join(user_parts))
    payload = _extract_chat_payload(data)
    if payload is None:
        return {
            "reply": "I couldn't interpret that request. Try asking to add, remove, or move a boat on a specific date.",
            "actions_applied": [],
            "predictions_changed": 0,
            "predictions": predictions[:40],
        }

    reply = str(payload.get("reply", "")).strip() or "Done."
    raw_actions = payload.get("actions") or []
    parsed_actions: list[dict] = []
    action_errors: list[str] = []

    if isinstance(raw_actions, list):
        for index, raw in enumerate(raw_actions, start=1):
            if not isinstance(raw, dict):
                continue
            try:
                parsed = _parse_action(raw, index)
                parsed["ship"] = resolve_dispatch_ship_name(parsed["ship"])
                parsed_actions.append(parsed)
            except ValueError as exc:
                action_errors.append(str(exc))

    applied: list[dict] = []
    if parsed_actions:
        try:
            applied = _apply_actions(db, parsed_actions)
        except ValueError as exc:
            action_errors.append(str(exc))

    updated_predictions, _ = predict_captain_schedule(db, days_ahead=days_ahead, use_ai=False)
    if action_errors:
        reply += " Some changes could not be applied: " + "; ".join(action_errors[:3])

    return {
        "reply": reply,
        "actions_applied": applied,
        "predictions_changed": len(applied),
        "predictions": updated_predictions[:40],
    }
