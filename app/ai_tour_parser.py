"""AI-assisted decoding of free-form bulk tour paste text."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.ai_predictor import _build_history_summary, _call_openai_json
from app.bulk_text_parser import normalize_bulk_paste
from app.config import get_settings
from app.mms_dispatch_parser import _build_date_header, resolve_dispatch_ship_name
from app.xml_cleaner import normalize_time_24h

KNOWN_BOAT_CODES = (
    "BW, BWA, DrmC, 50/50, FNF, GH, HR, JR, LewE, LJ, ML, AriC, BF, BS, BoomA, SL, SR"
)


def _extract_ai_rows(data: object) -> list[dict]:
    """Accept several JSON shapes returned by the model."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in ("tours", "assignments", "rows", "entries", "schedules", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if all(key in data for key in ("ship", "checkin_time")):
            return [data]
    return []


def _row_from_ai_item(item: dict, schedule_date: date, header: str, index: int) -> tuple[dict | None, str | None]:
    ship_raw = str(item.get("ship", item.get("vessel", item.get("cruise_ship", "")))).strip()
    if not ship_raw:
        return None, f"AI row {index}: missing ship name"

    checkin_raw = str(
        item.get("checkin_time", item.get("checkin", item.get("start_time", item.get("start", ""))))
    ).strip()
    return_raw = str(
        item.get("return_time", item.get("return", item.get("end_time", item.get("end", ""))))
    ).strip()
    boats = str(item.get("boat_codes", item.get("boats", item.get("boat", "")))).strip()

    if not checkin_raw:
        return None, f"AI row {index}: missing check-in time for {ship_raw}"

    checkin, checkin_err = normalize_time_24h(checkin_raw)
    if checkin_err or not checkin:
        return None, f"AI row {index}: invalid check-in time '{checkin_raw}'"

    if return_raw:
        return_norm, return_err = normalize_time_24h(return_raw)
        if return_err or not return_norm:
            return None, f"AI row {index}: invalid return time '{return_raw}'"
    else:
        hour, minute = map(int, checkin.split(":"))
        end_dt = datetime(2000, 1, 1, hour, minute) + timedelta(hours=4)
        return_norm = end_dt.strftime("%H:%M")

    return (
        {
            "date_header": header,
            "schedule_date": schedule_date,
            "ship": resolve_dispatch_ship_name(ship_raw),
            "checkin_time": checkin,
            "return_time": return_norm,
            "boat_codes": boats,
            "ship_count": None,
        },
        None,
    )


def ai_parse_tour_lines(
    text: str,
    schedule_date: date,
    *,
    db=None,
) -> tuple[list[dict], str | None]:
    """
    Use OpenAI to extract tour rows from unstructured pasted dispatch text.

    Returns (row dicts compatible with bulk import, status message).
    """
    if not get_settings().openai.is_configured:
        return [], "OpenAI is not configured — set OPENAI_API_KEY to decode unstructured tour text"

    normalized = normalize_bulk_paste(text)
    history = _build_history_summary(db, max_entries=40) if db is not None else ""

    system = (
        "You extract Ketchikan Alaska cruise tour boat dispatch assignments from messy pasted text. "
        "Return a JSON array only — no markdown. Each object must include:\n"
        '- "ship": full cruise ship name (expand "C. Spirit" to "Carnival Spirit", "Coral" to "Coral Princess")\n'
        '- "checkin_time": tour check-in/start time (examples: "7am", "06:15", "615", "6:15 AM")\n'
        '- "return_time": tour end time\n'
        '- "boat_codes": comma-separated tour boat operator codes\n\n'
        f"Known boat codes include: {KNOWN_BOAT_CODES}. "
        "These are tour boat operators, not pier berths. "
        "Each line/block in the paste usually represents one ship assignment for the given date. "
        "If the paste lists ship name on one line, boats on the next, and times on the next, combine them. "
        "Do not return an empty array when any ship/time/boat information is present."
    )

    user = (
        f"Schedule date: {schedule_date.isoformat()} ({schedule_date.strftime('%A')})\n\n"
    )
    if history:
        user += f"Recent schedule examples from this port:\n{history}\n\n"
    user += f"Original pasted text:\n{text[:6000]}\n\n"
    if normalized != text:
        user += f"Normalized version:\n{normalized[:6000]}\n\n"
    user += "Extract every tour boat assignment for this date."

    data = _call_openai_json(system, user)
    if data is None:
        return [], "AI could not decode the pasted text — try ship, boats, and times on separate lines"

    items = _extract_ai_rows(data)
    if not items:
        return [], "AI returned no tour rows — include ship name, boat codes, and check-in/return times"

    rows: list[dict] = []
    row_errors: list[str] = []
    header = _build_date_header(schedule_date)

    for index, item in enumerate(items, start=1):
        row, err = _row_from_ai_item(item, schedule_date, header, index)
        if err:
            row_errors.append(err)
            continue
        if row:
            rows.append(row)

    if not rows:
        message = "AI found no valid tour assignments in the pasted text"
        if row_errors:
            message += f" ({'; '.join(row_errors[:3])})"
        return [], message

    message = f"AI decoded {len(rows)} tour(s) from pasted text"
    if row_errors:
        message += f"; skipped {len(row_errors)} invalid row(s)"
    return rows, message
