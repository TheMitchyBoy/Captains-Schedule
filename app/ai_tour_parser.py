"""AI-assisted decoding of free-form bulk tour paste text."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.ai_predictor import _call_openai_json
from app.config import get_settings
from app.mms_dispatch_parser import _build_date_header, resolve_dispatch_ship_name
from app.xml_cleaner import normalize_time_24h


def ai_parse_tour_lines(text: str, schedule_date: date) -> tuple[list[dict], str | None]:
    """
    Use OpenAI to extract tour rows from unstructured pasted dispatch text.

    Returns (row dicts compatible with bulk import, status message).
    """
    if not get_settings().openai.is_configured:
        return [], "OpenAI is not configured — set OPENAI_API_KEY to decode unstructured tour text"

    system = (
        "You extract Ketchikan Alaska cruise tour boat dispatch assignments from messy pasted text. "
        "Return a JSON array only. Each object must include:\n"
        '- "ship": full cruise ship name (expand "C. Spirit" to "Carnival Spirit", "Island" to "Island Princess")\n'
        '- "checkin_time": check-in time string (examples: "7am", "06:30", "7:00 AM")\n'
        '- "return_time": return/end time string\n'
        '- "boat_codes": comma-separated tour boat operator codes (examples: "JR, LewE", "BW, BWA, DrmC, 50/50")\n\n'
        "Tour boat codes are short codes like BW, BWA, DrmC, JR, LewE, FNF, 50/50 — not berth numbers. "
        "All tours belong to the schedule date provided by the user. "
        "Ignore headers, notes, and non-tour lines. Return [] when nothing is a tour assignment."
    )

    user = (
        f"Schedule date: {schedule_date.isoformat()} ({schedule_date.strftime('%A')})\n\n"
        f"Pasted text:\n{text[:8000]}\n\n"
        "Extract every tour boat assignment for this date."
    )

    data = _call_openai_json(system, user)
    if data is None:
        return [], "AI could not decode the pasted text — try one tour per line with ship, boats, and times"

    if not isinstance(data, list):
        return [], "AI returned an unexpected response format"

    rows: list[dict] = []
    row_errors: list[str] = []
    header = _build_date_header(schedule_date)

    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue

        ship_raw = str(item.get("ship", "")).strip()
        if not ship_raw:
            continue

        checkin_raw = str(item.get("checkin_time", item.get("checkin", ""))).strip()
        return_raw = str(item.get("return_time", item.get("return", ""))).strip()
        boats = str(item.get("boat_codes", item.get("boats", ""))).strip()

        if not checkin_raw:
            row_errors.append(f"AI row {index}: missing check-in time for {ship_raw}")
            continue

        checkin, checkin_err = normalize_time_24h(checkin_raw)
        if checkin_err or not checkin:
            row_errors.append(f"AI row {index}: invalid check-in time '{checkin_raw}'")
            continue

        if return_raw:
            return_norm, return_err = normalize_time_24h(return_raw)
            if return_err or not return_norm:
                row_errors.append(f"AI row {index}: invalid return time '{return_raw}'")
                continue
        else:
            hour, minute = map(int, checkin.split(":"))
            end_dt = datetime(2000, 1, 1, hour, minute) + timedelta(hours=4)
            return_norm = end_dt.strftime("%H:%M")

        rows.append(
            {
                "date_header": header,
                "schedule_date": schedule_date,
                "ship": resolve_dispatch_ship_name(ship_raw),
                "checkin_time": checkin,
                "return_time": return_norm,
                "boat_codes": boats,
                "ship_count": None,
            }
        )

    if not rows:
        message = "AI found no tour assignments in the pasted text"
        if row_errors:
            message += f" ({'; '.join(row_errors[:3])})"
        return [], message

    message = f"AI decoded {len(rows)} tour(s) from pasted text"
    if row_errors:
        message += f"; skipped {len(row_errors)} invalid row(s)"
    return rows, message
