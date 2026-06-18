"""
Cruise ship passenger capacity registry for busy-day estimation.

When predicting how busy a port day will be, we sum the passenger capacity of
all ships scheduled (or historically typical) for that date. This module:

  - Seeds a built-in registry of 100+ major cruise ships
  - Fuzzy-matches ship names from XML data to known vessels
  - Falls back to a default capacity estimate for unknown ships
"""

from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models import ShipCapacity

# Major cruise ships with approximate passenger capacity (double occupancy).
# Sources: cruise line specs, CruiseMapper, Wikipedia ship class data.
BUILTIN_SHIPS: dict[str, dict] = {
    "symphony of the seas": {"capacity": 6680, "line": "Royal Caribbean"},
    "harmony of the seas": {"capacity": 6687, "line": "Royal Caribbean"},
    "wonder of the seas": {"capacity": 6988, "line": "Royal Caribbean"},
    "icon of the seas": {"capacity": 7600, "line": "Royal Caribbean"},
    "oasis of the seas": {"capacity": 6780, "line": "Royal Caribbean"},
    "allure of the seas": {"capacity": 6780, "line": "Royal Caribbean"},
    "quantum of the seas": {"capacity": 4905, "line": "Royal Caribbean"},
    "anthem of the seas": {"capacity": 4905, "line": "Royal Caribbean"},
    "ovation of the seas": {"capacity": 4905, "line": "Royal Caribbean"},
    "spectrum of the seas": {"capacity": 5622, "line": "Royal Caribbean"},
    "odyssey of the seas": {"capacity": 4905, "line": "Royal Caribbean"},
    "freedom of the seas": {"capacity": 4375, "line": "Royal Caribbean"},
    "liberty of the seas": {"capacity": 4960, "line": "Royal Caribbean"},
    "independence of the seas": {"capacity": 4370, "line": "Royal Caribbean"},
    "mariner of the seas": {"capacity": 3114, "line": "Royal Caribbean"},
    "navigator of the seas": {"capacity": 3114, "line": "Royal Caribbean"},
    "explorer of the seas": {"capacity": 3114, "line": "Royal Caribbean"},
    "adventure of the seas": {"capacity": 3114, "line": "Royal Caribbean"},
    "voyager of the seas": {"capacity": 3114, "line": "Royal Caribbean"},
    "radiance of the seas": {"capacity": 2466, "line": "Royal Caribbean"},
    "serenade of the seas": {"capacity": 2466, "line": "Royal Caribbean"},
    "jewel of the seas": {"capacity": 2466, "line": "Royal Caribbean"},
    "brilliance of the seas": {"capacity": 2466, "line": "Royal Caribbean"},
    "grandeur of the seas": {"capacity": 2440, "line": "Royal Caribbean"},
    "enchantment of the seas": {"capacity": 2730, "line": "Royal Caribbean"},
    "rhapsody of the seas": {"capacity": 2435, "line": "Royal Caribbean"},
    "vision of the seas": {"capacity": 2435, "line": "Royal Caribbean"},
    "msc world america": {"capacity": 6762, "line": "MSC Cruises"},
    "msc world europa": {"capacity": 6762, "line": "MSC Cruises"},
    "msc seascape": {"capacity": 5877, "line": "MSC Cruises"},
    "msc seaside evo": {"capacity": 5642, "line": "MSC Cruises"},
    "msc seaside": {"capacity": 5179, "line": "MSC Cruises"},
    "msc meraviglia": {"capacity": 5714, "line": "MSC Cruises"},
    "msc bellissima": {"capacity": 5714, "line": "MSC Cruises"},
    "msc grandiosa": {"capacity": 6334, "line": "MSC Cruises"},
    "msc virtuosa": {"capacity": 6334, "line": "MSC Cruises"},
    "msc seashore": {"capacity": 5634, "line": "MSC Cruises"},
    "msc divina": {"capacity": 4363, "line": "MSC Cruises"},
    "msc splendida": {"capacity": 4363, "line": "MSC Cruises"},
    "msc fantasia": {"capacity": 4363, "line": "MSC Cruises"},
    "msc magnifica": {"capacity": 3223, "line": "MSC Cruises"},
    "msc lirica": {"capacity": 2679, "line": "MSC Cruises"},
    "carnival celebration": {"capacity": 5374, "line": "Carnival"},
    "carnival jubilee": {"capacity": 5374, "line": "Carnival"},
    "carnival mardi gras": {"capacity": 5208, "line": "Carnival"},
    "carnival panorama": {"capacity": 4008, "line": "Carnival"},
    "carnival horizon": {"capacity": 3960, "line": "Carnival"},
    "carnival vista": {"capacity": 3960, "line": "Carnival"},
    "carnival breeze": {"capacity": 3690, "line": "Carnival"},
    "carnival magic": {"capacity": 3690, "line": "Carnival"},
    "carnival dream": {"capacity": 3646, "line": "Carnival"},
    "carnival sunshine": {"capacity": 3002, "line": "Carnival"},
    "carnival elation": {"capacity": 2052, "line": "Carnival"},
    "carnival paradise": {"capacity": 2052, "line": "Carnival"},
    "carnival conquest": {"capacity": 2974, "line": "Carnival"},
    "carnival glory": {"capacity": 2974, "line": "Carnival"},
    "carnival liberty": {"capacity": 2974, "line": "Carnival"},
    "carnival valor": {"capacity": 2974, "line": "Carnival"},
    "norwegian aqua": {"capacity": 3573, "line": "Norwegian"},
    "norwegian prima": {"capacity": 3215, "line": "Norwegian"},
    "norwegian viva": {"capacity": 3215, "line": "Norwegian"},
    "norwegian encore": {"capacity": 3998, "line": "Norwegian"},
    "norwegian bliss": {"capacity": 4004, "line": "Norwegian"},
    "norwegian joy": {"capacity": 3883, "line": "Norwegian"},
    "norwegian escape": {"capacity": 4266, "line": "Norwegian"},
    "norwegian getaway": {"capacity": 3963, "line": "Norwegian"},
    "norwegian breakaway": {"capacity": 3963, "line": "Norwegian"},
    "norwegian gem": {"capacity": 2394, "line": "Norwegian"},
    "norwegian pearl": {"capacity": 2394, "line": "Norwegian"},
    "norwegian sky": {"capacity": 2002, "line": "Norwegian"},
    "disney wish": {"capacity": 4000, "line": "Disney"},
    "disney dream": {"capacity": 4000, "line": "Disney"},
    "disney fantasy": {"capacity": 4000, "line": "Disney"},
    "disney magic": {"capacity": 2700, "line": "Disney"},
    "disney wonder": {"capacity": 2700, "line": "Disney"},
    "disney treasure": {"capacity": 4000, "line": "Disney"},
    "celebrity apex": {"capacity": 2910, "line": "Celebrity"},
    "celebrity beyond": {"capacity": 3260, "line": "Celebrity"},
    "celebrity ascendant": {"capacity": 3260, "line": "Celebrity"},
    "celebrity edge": {"capacity": 2910, "line": "Celebrity"},
    "celebrity equinox": {"capacity": 2850, "line": "Celebrity"},
    "celebrity solstice": {"capacity": 2850, "line": "Celebrity"},
    "celebrity reflection": {"capacity": 3046, "line": "Celebrity"},
    "celebrity silhouette": {"capacity": 2850, "line": "Celebrity"},
    "celebrity millennium": {"capacity": 2138, "line": "Celebrity"},
    "celebrity infinity": {"capacity": 2138, "line": "Celebrity"},
    "celebrity summit": {"capacity": 2138, "line": "Celebrity"},
    "celebrity constellation": {"capacity": 2138, "line": "Celebrity"},
    "aidanova": {"capacity": 6600, "line": "AIDA"},
    "aidacosma": {"capacity": 6600, "line": "AIDA"},
    "aidaprima": {"capacity": 3300, "line": "AIDA"},
    "aidaperla": {"capacity": 3300, "line": "AIDA"},
    "costa smeralda": {"capacity": 6554, "line": "Costa"},
    "costa toscana": {"capacity": 6554, "line": "Costa"},
    "costa diadema": {"capacity": 4947, "line": "Costa"},
    "costa fascinosa": {"capacity": 3800, "line": "Costa"},
    "costa pacifica": {"capacity": 3780, "line": "Costa"},
    "queen mary 2": {"capacity": 2691, "line": "Cunard"},
    "queen elizabeth": {"capacity": 2092, "line": "Cunard"},
    "queen victoria": {"capacity": 2094, "line": "Cunard"},
    "seabourn encore": {"capacity": 604, "line": "Seabourn"},
    "seabourn ovation": {"capacity": 604, "line": "Seabourn"},
    "regent seven seas splendor": {"capacity": 750, "line": "Regent"},
    "regent seven seas grandeur": {"capacity": 750, "line": "Regent"},
    "oceania vista": {"capacity": 1200, "line": "Oceania"},
    "oceania allura": {"capacity": 1200, "line": "Oceania"},
    "viking venus": {"capacity": 930, "line": "Viking"},
    "viking mars": {"capacity": 930, "line": "Viking"},
    "viking neptune": {"capacity": 930, "line": "Viking"},
    "viking saturn": {"capacity": 930, "line": "Viking"},
    "silver moon": {"capacity": 596, "line": "Silversea"},
    "silver dawn": {"capacity": 596, "line": "Silversea"},
    "silver nova": {"capacity": 728, "line": "Silversea"},
    "silver whisper": {"capacity": 382, "line": "Silversea"},
    "le commandant charcot": {"capacity": 245, "line": "Ponant"},
    "le bellot": {"capacity": 184, "line": "Ponant"},
    # Alaska-season ships (Holland America, Princess, etc.)
    "eurodam": {"capacity": 2104, "line": "Holland America"},
    "koningsdam": {"capacity": 2650, "line": "Holland America"},
    "noordam": {"capacity": 1972, "line": "Holland America"},
    "nieuw amsterdam": {"capacity": 2106, "line": "Holland America"},
    "westerdam": {"capacity": 1972, "line": "Holland America"},
    "zaandam": {"capacity": 1432, "line": "Holland America"},
    "volendam": {"capacity": 1432, "line": "Holland America"},
    "royal princess": {"capacity": 3560, "line": "Princess"},
    "discovery princess": {"capacity": 3664, "line": "Princess"},
    "island princess": {"capacity": 1974, "line": "Princess"},
    "emerald princess": {"capacity": 3098, "line": "Princess"},
    "ruby princess": {"capacity": 3084, "line": "Princess"},
    "star princess": {"capacity": 4314, "line": "Princess"},
    "grand princess": {"capacity": 2606, "line": "Princess"},
    "coral princess": {"capacity": 1986, "line": "Princess"},
    "carnival spirit": {"capacity": 2134, "line": "Carnival"},
    "carnival miracle": {"capacity": 2124, "line": "Carnival"},
    "carnival luminosa": {"capacity": 2260, "line": "Carnival"},
    "msc poesia": {"capacity": 2550, "line": "MSC Cruises"},
    "azamara pursuit": {"capacity": 702, "line": "Azamara"},
    "norwegian jade": {"capacity": 2402, "line": "Norwegian"},
    "brilliant lady": {"capacity": 2770, "line": "Virgin Voyages"},
    "riviera": {"capacity": 1248, "line": "Oceania"},
    "wilderness discoverer": {"capacity": 76, "line": "UnCruise"},
    "viking orion": {"capacity": 930, "line": "Viking"},
    "star seeker": {"capacity": 224, "line": "Windstar"},
}

DEFAULT_CAPACITY = 2500  # Used when a ship name cannot be matched to any known vessel
SIMILARITY_THRESHOLD = 0.82  # Minimum string similarity for fuzzy ship name matching


def normalize_ship_name(name: str) -> str:
    """Normalize ship names for consistent lookup (lowercase, collapsed whitespace)."""
    return " ".join(name.lower().strip().split())


def _fuzzy_match_builtin(name: str) -> tuple[str, dict] | None:
    """Find the closest matching ship in the built-in registry by name similarity."""
    normalized = normalize_ship_name(name)
    if normalized in BUILTIN_SHIPS:
        return normalized, BUILTIN_SHIPS[normalized]

    best_key = None
    best_score = 0.0
    for key in BUILTIN_SHIPS:
        score = SequenceMatcher(None, normalized, key).ratio()
        if score > best_score:
            best_score = score
            best_key = key

    if best_key and best_score >= SIMILARITY_THRESHOLD:
        return best_key, BUILTIN_SHIPS[best_key]
    return None


def seed_ship_capacities(db: Session) -> int:
    """
    Populate the database with built-in ship capacity records on startup.

    Only inserts ships that are not already present — safe to call repeatedly.
    Returns the number of new records added.
    """
    added = 0
    for ship_name, info in BUILTIN_SHIPS.items():
        existing = db.query(ShipCapacity).filter(ShipCapacity.ship_name == ship_name).first()
        if existing:
            continue
        db.add(
            ShipCapacity(
                ship_name=ship_name,
                passenger_capacity=info["capacity"],
                cruise_line=info["line"],
                source="builtin",
            )
        )
        added += 1
    if added:
        db.commit()
    return added


def get_ship_capacity(db: Session, ship_name: str) -> ShipCapacity:
    """
    Look up passenger capacity for a ship, creating a record if needed.

    Lookup order: exact DB match → fuzzy DB match → fuzzy built-in registry
    → default estimate. New records are persisted so future lookups are fast.
    """
    normalized = normalize_ship_name(ship_name)

    record = db.query(ShipCapacity).filter(ShipCapacity.ship_name == normalized).first()
    if record:
        return record

    for row in db.query(ShipCapacity).all():
        if SequenceMatcher(None, normalized, row.ship_name).ratio() >= SIMILARITY_THRESHOLD:
            return row

    match = _fuzzy_match_builtin(ship_name)
    if match:
        key, info = match
        record = ShipCapacity(
            ship_name=normalized,
            passenger_capacity=info["capacity"],
            cruise_line=info["line"],
            source="builtin_match",
        )
    else:
        record = ShipCapacity(
            ship_name=normalized,
            passenger_capacity=DEFAULT_CAPACITY,
            cruise_line=None,
            source="estimated",
        )

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def estimate_daily_passengers(db: Session, ships: list[str]) -> tuple[int, float]:
    """
    Estimate total passengers and a normalized busy score (0–1) for a port day.

    The busy score compares the day's total capacity against ~25k passengers,
    representing a very busy day with multiple mega-ships in port.
    """
    if not ships:
        return 0, 0.0

    capacities = [get_ship_capacity(db, ship).passenger_capacity for ship in ships]
    total = sum(capacities)

    # Normalize against a busy port day (~25k passengers across multiple mega-ships)
    max_reference = 25000
    busy_score = min(1.0, total / max_reference)
    return total, round(busy_score, 3)


def lookup_ship_online(db: Session, ship_name: str) -> ShipCapacity | None:
    """
    Enrich or create a ship capacity record from the built-in registry.

    Intended as an extension point for external data sources (e.g. CruiseMapper).
    Currently performs fuzzy matching against the local ship list.
    """
    match = _fuzzy_match_builtin(ship_name)
    if not match:
        return None

    key, info = match
    normalized = normalize_ship_name(ship_name)
    record = db.query(ShipCapacity).filter(ShipCapacity.ship_name == normalized).first()
    if record:
        record.passenger_capacity = info["capacity"]
        record.cruise_line = info["line"]
        record.source = "online_registry"
        record.updated_at = datetime.utcnow()
    else:
        record = ShipCapacity(
            ship_name=normalized,
            passenger_capacity=info["capacity"],
            cruise_line=info["line"],
            source="online_registry",
        )
        db.add(record)
    db.commit()
    db.refresh(record)
    return record
