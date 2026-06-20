"""
Berth vs. captain/tour-boat code detection.

Ketchikan port schedules use numeric berth codes (1, 2, 3, 4) and dock codes
(WW, WE, AN3) for where a cruise ship docks.

Tour boat dispatch uses operator codes like DrmC, BW, BWA, FNF, 50/50, SR, SL.
These must not be mixed up with port berth codes or auto-generated placeholders.
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
        "DRMC",
    }
)

PLACEHOLDER_BOAT_CODE_PATTERN = re.compile(r"^CPT-[A-Z]$", re.IGNORECASE)

CAPTAIN_CODE_PATTERN = re.compile(
    r"^(?:OP|CAP|BOAT|TB|TOUR)[-_\s]?[A-Z0-9]+",
    re.IGNORECASE,
)

TOUR_BOAT_CODE_PATTERN = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9]{1,7}|\d+/\d+)$",
)


def normalize_berth(value: str) -> str:
    """Strip optional BERTH- prefix and whitespace."""
    text = value.strip()
    if text.upper().startswith("BERTH-"):
        return text[6:].strip()
    return text


def is_placeholder_boat_code(value: str) -> bool:
    """Return True for auto-generated CPT-* placeholders (not real tour boats)."""
    return bool(PLACEHOLDER_BOAT_CODE_PATTERN.match((value or "").strip()))


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

    if is_placeholder_boat_code(text):
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

    text = boat_codes.strip()
    text = re.sub(r"\b50\s+50\b", "50/50", text, flags=re.IGNORECASE)

    placeholders: dict[str, str] = {}
    for code in sorted(KNOWN_TOUR_BOAT_CODES, key=len, reverse=True):
        if "/" not in code:
            continue
        token = f"__SLASHCODE_{len(placeholders)}__"
        placeholders[token] = code
        text = re.sub(re.escape(code), token, text, flags=re.IGNORECASE)

    parts = re.split(r"[,;/]+|\s+and\s+|\s+", text, flags=re.IGNORECASE)
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        code = part.strip()
        if code in placeholders:
            code = placeholders[code]
        if not code or not is_captain_boat_code(code):
            continue
        key = code.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append(code)
    return result


def sort_boat_codes(codes: list[str]) -> list[str]:
    """Sort tour boat codes in standard alphabetical dispatch order."""
    return sorted(codes, key=lambda code: code.casefold())


def merge_dispatch_codes(*values: str | None) -> str:
    """Merge boat code strings, deduplicating and sorting alphabetically."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for code in split_dispatch_codes(value or ""):
            key = code.upper()
            if key in seen:
                continue
            seen.add(key)
            result.append(code)
    return ", ".join(sort_boat_codes(result))


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

    if is_placeholder_boat_code(boat):
        boat = ""

    if boat and _is_port_berth_token(boat) and not is_captain_boat_code(boat):
        dock = dock or normalize_berth(boat)
        boat = ""

    return boat, dock
