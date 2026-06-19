"""
Berth vs. captain/tour-boat code detection.

Ketchikan port schedules use numeric berth codes (1, 2, 3, 4) and dock codes
(WW, WE, AN3) for where a cruise ship docks.

Tour boat dispatch uses short operator codes like BW, BWA, FNF, 50/50, SR, SL,
plus CPT-A / OP-12 style labels. These must not be mixed up.
"""

from __future__ import annotations

import re

# Port / dock location codes (where the cruise ship ties up).
PORT_BERTH_CODES = frozenset(
    {
        "WW",
        "WE",
        "WC",
        "DT",
        "D",
        "ANR",
        "AN3",
        "B3T",
        "B3",
    }
)

# Known tour-boat operator codes used in Ketchikan dispatch.
KNOWN_TOUR_BOAT_CODES = frozenset(
    {
        "BW",
        "BWA",
        "FNF",
        "SR",
        "SL",
        "50/50",
    }
)

CAPTAIN_CODE_PATTERN = re.compile(
    r"^(?:CPT|OP|CAP|BOAT|TB|TOUR)[-_\s]?[A-Z0-9]+",
    re.IGNORECASE,
)

TOUR_BOAT_CODE_PATTERN = re.compile(
    r"^(?:[A-Z]{2,4}\d?|\d+/\d+)$",
    re.IGNORECASE,
)


def normalize_berth(value: str) -> str:
    """Strip optional BERTH- prefix and whitespace."""
    text = value.strip()
    if text.upper().startswith("BERTH-"):
        return text[6:].strip()
    return text


def _is_port_berth_token(text: str) -> bool:
    """Core berth/dock detection without calling captain-boat helpers."""
    normalized = normalize_berth(text.strip())
    upper = normalized.upper()

    if upper in KNOWN_TOUR_BOAT_CODES:
        return False

    if normalized.isdigit():
        return 1 <= int(normalized) <= 9

    if upper in PORT_BERTH_CODES:
        return True

    if re.match(r"^AN\d+$", normalized, re.IGNORECASE):
        return True

    if re.match(r"^B\d+T?$", normalized, re.IGNORECASE):
        return True

    return False


def looks_like_berth_code(value: str) -> bool:
    """Return True when a value is a port berth/dock code, not a tour boat operator."""
    if not value or not str(value).strip():
        return False

    text = normalize_berth(str(value).strip())

    if CAPTAIN_CODE_PATTERN.match(text):
        return False

    return _is_port_berth_token(text)


def is_captain_boat_code(value: str) -> bool:
    """Return True when a code is a tour-boat / captain dispatch operator."""
    if not value or not str(value).strip():
        return False

    text = str(value).strip()
    upper = text.upper()

    if upper.startswith("BERTH-"):
        return False

    if upper in KNOWN_TOUR_BOAT_CODES:
        return True

    if CAPTAIN_CODE_PATTERN.match(text):
        return True

    if "/" in text and not text.isdigit():
        return True

    if TOUR_BOAT_CODE_PATTERN.match(text) and not _is_port_berth_token(text):
        return True

    return False


def split_dispatch_codes(boat_codes: str) -> list[str]:
    """Split boat_codes field and return only tour-boat / captain codes."""
    if not boat_codes:
        return []
    parts = [c.strip() for c in boat_codes.split(",") if c.strip()]
    return [code for code in parts if is_captain_boat_code(code)]


def repair_boat_berth_value(boat_codes: str | None, berth: str | None) -> tuple[str, str | None]:
    """
    Fix a stored row where port berth codes were saved as boat_codes.

    Returns (cleaned_boat_codes, berth).
    """
    boat = (boat_codes or "").strip()
    dock = (berth or "").strip() or None

    if boat.upper().startswith("BERTH-"):
        dock = dock or normalize_berth(boat)
        boat = ""

    if boat and _is_port_berth_token(boat) and not is_captain_boat_code(boat):
        dock = dock or normalize_berth(boat)
        boat = ""

    return boat, dock
