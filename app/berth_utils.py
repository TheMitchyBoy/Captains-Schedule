"""
Berth vs. captain/tour-boat code detection.

Ketchikan port schedules use berth codes (WW, WE, BW, BWA, 1, AN3) for where a
cruise ship docks. Tour boat dispatch uses captain codes (CPT-A, OP-12, etc.).
These must not be mixed up when learning prediction patterns.
"""

from __future__ import annotations

import re

# Common Ketchikan / CLAA berth codes (Ward Cove, downtown berths, anchor, tender).
BERTH_CODE_PATTERN = re.compile(
    r"^(?:"
    r"WW|WE|BW|BWA|WC|DT|D|"           # Ward Cove / downtown shorthand
    r"AN\d*|ANR|"                       # Anchored out
    r"B\d+T?|"                          # Berth 3 tender float
    r"\d{1,2}"                          # Berth 1–4
    r")$",
    re.IGNORECASE,
)

# Values that are clearly captain/tour-boat dispatch codes.
CAPTAIN_CODE_PATTERN = re.compile(
    r"^(?:CPT|OP|CAP|BOAT|TB|TOUR)[-_\s]?[A-Z0-9]+",
    re.IGNORECASE,
)


def normalize_berth(value: str) -> str:
    """Strip optional BERTH- prefix and whitespace."""
    text = value.strip()
    if text.upper().startswith("BERTH-"):
        return text[6:].strip()
    return text


def looks_like_berth_code(value: str) -> bool:
    """Return True when a value is a port berth/dock code, not a tour boat name."""
    if not value or not str(value).strip():
        return False
    text = normalize_berth(str(value).strip())
    if BERTH_CODE_PATTERN.match(text):
        return True
    # Short alphanumeric tokens without vowels are usually berth/dock codes.
    if len(text) <= 4 and text.isalnum() and not CAPTAIN_CODE_PATTERN.match(text):
        if text.isdigit():
            return True
        if text.upper() in {"WW", "WE", "BW", "BWA", "WC", "DT", "D", "ANR", "AN3", "B3T"}:
            return True
    return False


def is_captain_boat_code(value: str) -> bool:
    """Return True when a code should be used for captain/tour-boat predictions."""
    if not value or not str(value).strip():
        return False
    text = str(value).strip()
    if text.upper().startswith("BERTH-"):
        return False
    if looks_like_berth_code(text):
        return False
    if CAPTAIN_CODE_PATTERN.match(text):
        return True
    # Any non-berth code with letters and a hyphen is likely a dispatch code.
    if "-" in text and not looks_like_berth_code(text):
        return True
    return not looks_like_berth_code(text) and len(text) > 4


def split_dispatch_codes(boat_codes: str) -> list[str]:
    """Split boat_codes field and return only captain/tour-boat codes."""
    parts = [c.strip() for c in boat_codes.replace("/", ",").split(",") if c.strip()]
    return [code for code in parts if is_captain_boat_code(code)]
