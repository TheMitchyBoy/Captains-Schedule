"""
AI-assisted prediction helpers using the OpenAI API.

When OPENAI_API_KEY is configured, this module:
  1. Forecasts which ships will be in port on dates without uploaded schedule data
  2. Suggests captain/boat assignments for ships not covered by learned patterns

Results are cached in memory and cleared after each XML upload.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CaptainPattern, ScheduleEntry
from app.scheduler import ShiftCandidate
from app.ship_data import estimate_daily_passengers, get_ship_capacity, normalize_ship_name

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_ship_forecast_cache: dict[str, dict[date, list[dict[str, str]]]] = {}
_captain_suggestion_cache: dict[str, list[dict]] = {}


def clear_ai_prediction_cache() -> None:
    """Drop cached AI forecasts (called after new schedule data is uploaded)."""
    _ship_forecast_cache.clear()
    _captain_suggestion_cache.clear()


def _cache_key(dates: list[date]) -> str:
    if not dates:
        return "empty"
    return f"{min(dates).isoformat()}:{max(dates).isoformat()}:{len(dates)}"


def _call_openai_json(system: str, user: str) -> list | dict | None:
    """Send a chat completion request and parse JSON from the response."""
    settings = get_settings().openai
    if not settings.is_configured:
        return None

    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json={
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
            timeout=90.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
        return json.loads(content)
    except Exception:
        return None


def _build_history_summary(db: Session, max_entries: int = 80) -> str:
    """Compact text summary of uploaded schedule history for AI context."""
    entries = (
        db.query(ScheduleEntry)
        .order_by(ScheduleEntry.schedule_date.desc())
        .limit(max_entries)
        .all()
    )
    if not entries:
        return "No historical schedule data uploaded yet."

    by_weekday: dict[int, list[str]] = defaultdict(list)
    for entry in reversed(entries):
        dow = entry.schedule_date.weekday()
        line = (
            f"{entry.schedule_date.isoformat()} ({DAY_NAMES[dow]}): "
            f"{entry.ship} {entry.checkin_time}-{entry.return_time} [{entry.boat_codes}]"
        )
        by_weekday[dow].append(line)

    lines = ["Historical dispatch schedule (most recent uploads):"]
    for dow in range(7):
        if by_weekday[dow]:
            lines.append(f"\n{DAY_NAMES[dow]} patterns:")
            lines.extend(f"  - {row}" for row in by_weekday[dow][-12:])

    date_range = db.query(
        func.min(ScheduleEntry.schedule_date),
        func.max(ScheduleEntry.schedule_date),
    ).one()
    if date_range[0]:
        lines.append(f"\nData covers {date_range[0]} through {date_range[1]}.")
    return "\n".join(lines)


def _build_pattern_summary(db: Session, boat_codes: list[str]) -> str:
    """Summarize learned captain patterns for AI assignment suggestions."""
    patterns = db.query(CaptainPattern).order_by(CaptainPattern.confidence.desc()).all()
    if not patterns:
        return "No learned captain patterns yet."

    lines = ["Learned captain assignment patterns (boat → ship on weekday):"]
    for pattern in patterns[:40]:
        if boat_codes and pattern.boat_code not in boat_codes:
            continue
        lines.append(
            f"  - {pattern.boat_code} on {DAY_NAMES[pattern.day_of_week]}: "
            f"{pattern.ship} {pattern.checkin_time}-{pattern.return_time} "
            f"(confidence {pattern.confidence}, seen {pattern.occurrence_count}x)"
        )
    return "\n".join(lines)


def _ship_matches(expected: str, candidate: str) -> bool:
    """Fuzzy ship name match for filtering pattern candidates."""
    a = normalize_ship_name(expected)
    b = normalize_ship_name(candidate)
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.88


def ai_forecast_ships_for_dates(db: Session, dates: list[date]) -> dict[date, list[dict[str, str]]]:
    """
    Use AI to predict ships and tour windows for dates lacking uploaded schedule data.

    Returns {date: [{ship, checkin_time, return_time}, ...]}.
    """
    if not dates or not get_settings().openai.is_configured:
        return {}

    key = _cache_key(dates)
    if key in _ship_forecast_cache:
        return _ship_forecast_cache[key]

    history = _build_history_summary(db)
    date_list = ", ".join(d.isoformat() for d in sorted(dates)[:30])
    if len(dates) > 30:
        date_list += f" … and {len(dates) - 30} more dates through {max(dates).isoformat()}"

    system = (
        "You forecast Ketchikan, Alaska cruise port schedules for tour boat dispatch planning. "
        "Use weekday patterns from historical data. Return valid JSON only — no markdown."
    )
    user = (
        f"{history}\n\n"
        "Predict which cruise ships will be in port on these dates (no uploaded data yet). "
        "Use realistic Ketchikan arrival/departure times based on historical patterns for the same weekday.\n\n"
        f"Dates to forecast: {date_list}\n\n"
        "Return a JSON array:\n"
        '[{"date": "YYYY-MM-DD", "ships": [{"ship": "Ship Name", "checkin_time": "7:00 AM", "return_time": "3:00 PM"}]}]\n'
        "Include 2-8 ships per busy weekday, fewer on light days. Match ship names from history when possible."
    )

    data = _call_openai_json(system, user)
    result: dict[date, list[dict[str, str]]] = {}
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                target = date.fromisoformat(str(row.get("date", "")))
            except ValueError:
                continue
            ships = row.get("ships") or []
            parsed: list[dict[str, str]] = []
            for ship_row in ships:
                if not isinstance(ship_row, dict):
                    continue
                ship = str(ship_row.get("ship", "")).strip()
                checkin = str(ship_row.get("checkin_time", "")).strip()
                ret = str(ship_row.get("return_time", "")).strip()
                if ship and checkin and ret:
                    parsed.append({"ship": ship, "checkin_time": checkin, "return_time": ret})
            if parsed:
                result[target] = parsed

    _ship_forecast_cache[key] = result
    return result


def ai_suggest_captain_shifts(
    db: Session,
    target_date: date,
    ships_in_port: list[dict[str, str]],
    undercovered_ships: list[dict[str, str | int]],
    boat_codes: list[str],
) -> list[ShiftCandidate]:
    """
    Ask AI to suggest captain assignments for ships that still need more boats.

    undercovered_ships items include ship name and boats_needed count.
    Suggestions are returned as ShiftCandidates with source='ai' and moderate confidence.
    """
    if not undercovered_ships or not boat_codes or not get_settings().openai.is_configured:
        return []

    ship_names = [str(item.get("ship", "")).strip() for item in undercovered_ships if item.get("ship")]
    cache_key = f"{target_date.isoformat()}:{','.join(sorted(ship_names))}"
    if cache_key in _captain_suggestion_cache:
        cached = _captain_suggestion_cache[cache_key]
    else:
        history = _build_history_summary(db)
        patterns = _build_pattern_summary(db, boat_codes)
        ships_text = json.dumps(ships_in_port, indent=2)
        undercovered_text = json.dumps(undercovered_ships, indent=2)

        system = (
            "You assign tour boat operators to cruise ships in Ketchikan, Alaska. "
            "Large cruise ships often need multiple tour boats at the same time — "
            "some ships need more boats than others based on passenger capacity. "
            "Follow dispatch rules: each boat serves one ship at a time, "
            "alphabetical boat priority, 3-hour minimum turnaround between tours "
            "for the same boat. Return valid JSON only."
        )
        user = (
            f"{history}\n\n{patterns}\n\n"
            f"Date: {target_date.isoformat()} ({DAY_NAMES[target_date.weekday()]})\n"
            f"Ships in port: {ships_text}\n"
            f"Available boat codes (alphabetical dispatch order): {', '.join(sorted(boat_codes))}\n"
            f"Ships needing more boat assignments: {undercovered_text}\n\n"
            "Suggest additional tour boat assignments. Larger ships may need multiple "
            "boats simultaneously. Return JSON array — one object per boat assignment:\n"
            '[{"boat_code": "DrmC", "ship": "Ship Name", "checkin_time": "7:00 AM", '
            '"return_time": "3:00 PM", "confidence": 0.55, "reason": "brief reason"}]\n'
            "Use boat codes from the available list. Assign only the number of boats still "
            "needed per ship. Confidence 0.4-0.75 for AI suggestions."
        )

        data = _call_openai_json(system, user)
        cached = data if isinstance(data, list) else []
        _captain_suggestion_cache[cache_key] = cached

    dow = target_date.weekday()
    _, busy_score = estimate_daily_passengers(
        db, [s.get("ship", "") for s in ships_in_port if s.get("ship")]
    )

    undercovered_names = {str(item.get("ship", "")).strip() for item in undercovered_ships}

    candidates: list[ShiftCandidate] = []
    for row in cached:
        if not isinstance(row, dict):
            continue
        boat = str(row.get("boat_code", "")).strip()
        ship = str(row.get("ship", "")).strip()
        checkin = str(row.get("checkin_time", "")).strip()
        ret = str(row.get("return_time", "")).strip()
        if not boat or not ship or not checkin or not ret:
            continue
        if boat not in boat_codes:
            continue
        if not any(_ship_matches(ship, name) for name in undercovered_names):
            continue

        try:
            confidence = float(row.get("confidence", 0.55))
        except (TypeError, ValueError):
            confidence = 0.55
        confidence = max(0.35, min(0.75, confidence))

        candidates.append(
            ShiftCandidate(
                boat_code=boat,
                schedule_date=target_date,
                day_of_week=DAY_NAMES[dow],
                ship=ship,
                checkin_time=checkin,
                return_time=ret,
                confidence=round(confidence, 3),
                passenger_estimate=get_ship_capacity(db, ship).passenger_capacity,
                busy_score=busy_score,
                source="ai",
            )
        )

    return candidates


def get_ai_ship_names_for_date(
    db: Session,
    schedule_date: date,
    ai_forecasts: dict[date, list[dict[str, str]]],
) -> list[str]:
    """Resolve ship names for a date using AI forecast data."""
    forecast = ai_forecasts.get(schedule_date)
    if forecast:
        return [row["ship"] for row in forecast]
    return []

def dates_without_actual_data(db: Session, start: date, end: date) -> list[date]:
    """Return dates in range that have no uploaded schedule entries."""
    actual_dates = {
        row[0]
        for row in db.query(ScheduleEntry.schedule_date)
        .filter(ScheduleEntry.schedule_date >= start, ScheduleEntry.schedule_date <= end)
        .distinct()
        .all()
    }
    missing: list[date] = []
    current = start
    while current <= end:
        if current not in actual_dates:
            missing.append(current)
        current += timedelta(days=1)
    return missing
