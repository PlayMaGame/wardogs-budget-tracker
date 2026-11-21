"""Read role XP totals from the local Wardogs save file.

The game keeps absolute role XP in ``PlayerRoleProgress.sav`` as UTF-16LE
JSON.  The outer object is keyed by an unstable save slot; the inner keys
look like ``(TagName="Meta.Role.Wardog")``.

Only totals are stored, so per-match gains are computed by diffing a
snapshot taken at match start against one taken at match end.
"""

from __future__ import annotations

import json
import os
import re

DEFAULT_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Wardogs",
    "Saved",
    "SaveGames",
    "PlayerRoleProgress.sav",
)

ROLE_RE = re.compile(r"Meta\.Role\.(\w+)")

ROLES = (
    "Wardog",
    "Infantry",
    "Medic",
    "Driver",
    "Pilot",
    "Support",
    "Recon",
)


def read_role_xp(path=None):
    """Return ``{role: total_xp}`` from the save file, or ``{}``.

    Missing files, unreadable files and malformed JSON are all treated as
    "no data" so the tracker can keep running without the save file.
    """
    if path is None:
        path = DEFAULT_PATH
    try:
        with open(path, "r", encoding="utf-16-le") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}

    if not isinstance(raw, dict):
        return {}
    inner = next(iter(raw.values()), {})
    if not isinstance(inner, dict):
        return {}

    totals = {}
    for key, value in inner.items():
        match = ROLE_RE.search(str(key))
        if not match:
            continue
        try:
            totals[match.group(1)] = int(value)
        except (TypeError, ValueError):
            continue
    return totals
