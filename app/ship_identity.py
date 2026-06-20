"""
Canonical cruise ship identity for deduplication and matching.

Resolves spelling/capitalization/abbreviation variants (Coral → Coral Princess)
while avoiding unsafe merges for ambiguous single-word names like bare "Spirit".
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.mms_dispatch_parser import resolve_dispatch_ship_name
from app.ship_data import BUILTIN_SHIPS, _fuzzy_match_builtin, normalize_ship_name

# Single-word ship tokens that appear across multiple cruise lines — never expand
# via fuzzy/suffix rules (e.g. Spirit → Carnival Spirit vs other operators).
AMBIGUOUS_SINGLE_WORDS = frozenset(
    {
        "spirit",
        "legend",
        "magic",
        "dream",
        "wonder",
        "glory",
        "liberty",
        "freedom",
        "discovery",
        "explorer",
        "adventure",
        "radiance",
        "serenade",
        "jewel",
        "brilliance",
        "grandeur",
        "enchantment",
        "rhapsody",
        "vision",
        "princess",
        "pearl",
        "star",
        "sun",
        "sea",
        "sky",
        "jade",
        "gem",
        "bliss",
        "joy",
        "escape",
        "pride",
        "valor",
        "glory",
        "elation",
        "paradise",
        "summit",
        "infinity",
        "constellation",
        "millennium",
        "solstice",
        "equinox",
        "reflection",
        "silhouette",
        "edge",
        "beyond",
        "apex",
        "vista",
        "horizon",
        "panorama",
        "celebration",
        "jubilee",
        "miracle",
        "luminosa",
        "splendor",
    }
)

STRONG_FUZZY_THRESHOLD = 0.92


def canonical_ship_key(name: str) -> str:
    """
    Return a stable identity key for grouping duplicate schedule rows.

    Examples:
      Coral / Coral Princess / CORAL PRINCESS → coral princess
      C. Spirit / Carnival Spirit → carnival spirit
      Spirit (alone) → spirit  (not merged with carnival spirit)
    """
    raw = name.strip()
    raw_normalized = normalize_ship_name(raw)
    raw_tokens = raw_normalized.split()
    if len(raw_tokens) == 1 and raw_tokens[0] in AMBIGUOUS_SINGLE_WORDS:
        return raw_normalized

    resolved = resolve_dispatch_ship_name(raw)
    normalized = normalize_ship_name(resolved)

    if normalized in BUILTIN_SHIPS:
        return normalized

    tokens = normalized.split()
    if len(tokens) == 1:
        word = tokens[0]
        if word in AMBIGUOUS_SINGLE_WORDS:
            return normalized
        prefix_matches = sorted(k for k in BUILTIN_SHIPS if k.startswith(word + " "))
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        return normalized

    fuzzy = _fuzzy_match_builtin(resolved)
    if fuzzy:
        return fuzzy[0]

    return normalized


def ship_names_equivalent(a: str, b: str) -> bool:
    """Return True when two ship labels refer to the same vessel."""
    return canonical_ship_key(a) == canonical_ship_key(b)


def best_ship_display_name(names: list[str]) -> str:
    """Pick the most complete ship label after alias resolution."""
    if not names:
        return ""
    resolved = [resolve_dispatch_ship_name(name.strip()) for name in names if name.strip()]
    if not resolved:
        return ""
    return max(resolved, key=lambda name: (len(name.split()), len(name)))
