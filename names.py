"""Display names for the raw map and faction identifiers in Wardogs data."""

MAP_NAMES = {
    "europe": "Ozeti",
    "kavkazi": "Bakurani",
    "northamerica": "Zestafona",
    "russia": "Sabatulo",
}

FACTION_NAMES = {
    "alpha": "Lonestar",
    "bravo": "Valkyra",
    "charlie": "Manticore",
}

_MAP_DISPLAY = {name.lower(): name for name in MAP_NAMES.values()}
_FACTION_DISPLAY = {name.lower(): name for name in FACTION_NAMES.values()}


def display_map_name(raw):
    """Turn a raw map code into its in-game name.

    Display names pass through unchanged, so this is safe to apply more than
    once (for example to history rows that were already normalized).
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    key = text.lower()
    if key == "frontend":
        return None
    if key in ("unknown", "?"):
        return "Unknown"
    return MAP_NAMES.get(key) or _MAP_DISPLAY.get(key) or text


def display_faction(raw):
    """Turn a raw Steam team slot into its official faction name."""
    if not raw:
        return "Unknown"
    text = str(raw).strip()
    if not text or text.lower() in ("unknown", "?"):
        return "Unknown"
    key = text.lower()
    return FACTION_NAMES.get(key) or _FACTION_DISPLAY.get(key) or text
