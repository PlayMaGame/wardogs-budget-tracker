"""Season archives for the Wardogs cash tracker.

The live files stay at ``data/history.json`` and ``data/cash_events.csv``.
When a season is archived they are moved to ``data/seasons/<name>/`` and a
fresh, empty current season is created.  Archived seasons are read-only; the
dashboard only reads their files.

Keeping this in its own module means both the tracker and the standalone
dashboard can use the same path rules and the same name validation.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from datetime import datetime, timezone

from history_store import HistoryStore

MAX_NAME_LENGTH = 40
RESERVED_NAMES = {"current"}
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Keep in sync with cash_tracker.EVENTS_HEADER.  Importing cash_tracker here
# would create a circular import (it imports dashboard, which imports us).
EVENTS_HEADER = [
    "time_utc",
    "match_id",
    "session_id",
    "match_uid",
    "event",
    "amount",
    "match_balance",
    "session_balance",
    "faction",
]


class SeasonError(Exception):
    """Base class for season archive errors."""


class InvalidSeasonName(SeasonError, ValueError):
    """The requested archive name cannot safely be used as a folder name."""


class SeasonExistsError(SeasonError, FileExistsError):
    """An archive with that name already exists."""


class SeasonNotFound(SeasonError, FileNotFoundError):
    """The requested archived season does not exist."""


def sanitize_name(value):
    """Return a safe archive folder name, or raise InvalidSeasonName."""
    if not isinstance(value, str):
        raise InvalidSeasonName("season name must be text")
    name = value.strip()
    if not name:
        raise InvalidSeasonName("enter a name or number for the archived season")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidSeasonName(
            f"season name must be {MAX_NAME_LENGTH} characters or fewer"
        )
    if name in (".", "..") or set(name) == {"."}:
        raise InvalidSeasonName("season name cannot be dots only")
    if name.startswith("."):
        raise InvalidSeasonName("season name cannot start with a dot")
    if name.endswith("."):
        raise InvalidSeasonName("season name cannot end with a dot")
    if INVALID_CHARS.search(name):
        raise InvalidSeasonName('season name cannot contain < > : " / \\ | ? *')
    if name.lower() in RESERVED_NAMES:
        raise InvalidSeasonName('"Current" is reserved for the live season')
    return name


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normal_summary(summary):
    summary = summary if isinstance(summary, dict) else {}
    return {
        "sessions": _int(summary.get("sessions")),
        "matches": _int(summary.get("matches")),
        "balance": _int(summary.get("balance")),
        "spent": _int(summary.get("spent")),
        "gained": _int(summary.get("gained")),
    }


def _summary_from_history(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    summary = _normal_summary(data.get("lifetime"))
    if not any(summary.values()):
        sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
        matches = data.get("matches") if isinstance(data.get("matches"), list) else []
        summary = {
            "sessions": len(sessions),
            "matches": len(matches),
            "balance": sum(_int(m.get("balance")) for m in matches),
            "spent": sum(_int(m.get("spent")) for m in matches),
            "gained": sum(_int(m.get("gained")) for m in matches),
        }
    return summary


class SeasonStore:
    def __init__(self, data_dir):
        self.data_dir = os.path.abspath(data_dir)
        self.seasons_dir = os.path.join(self.data_dir, "seasons")
        self.current_history_path = os.path.join(self.data_dir, "history.json")
        self.current_events_path = os.path.join(self.data_dir, "cash_events.csv")

    # ------------------------------------------------------------- current
    def current_summary(self):
        return _summary_from_history(self.current_history_path)

    def current_descriptor(self):
        return {
            "id": None,
            "name": "Current",
            "current": True,
            "archived_at": None,
            "summary": self.current_summary(),
        }

    def snapshot(self):
        return {"current": self.current_descriptor(), "seasons": self.archives()}

    # ------------------------------------------------------------- archive
    def archives(self):
        try:
            entries = [
                entry
                for entry in os.listdir(self.seasons_dir)
                if os.path.isdir(os.path.join(self.seasons_dir, entry))
            ]
        except OSError:
            return []

        result = []
        for entry in entries:
            try:
                name = sanitize_name(entry)
            except InvalidSeasonName:
                continue
            season_dir = os.path.join(self.seasons_dir, entry)
            meta = self._read_meta(season_dir)
            summary = _normal_summary(
                meta.get("summary") or _summary_from_history(
                    os.path.join(season_dir, "history.json")
                )
            )
            archived_at = meta.get("archived_at")
            if not archived_at:
                try:
                    archived_at = datetime.fromtimestamp(
                        os.path.getmtime(season_dir), timezone.utc
                    ).isoformat(timespec="seconds")
                except OSError:
                    archived_at = ""
            result.append(
                {
                    "id": name,
                    "name": str(meta.get("name") or name),
                    "current": False,
                    "archived_at": archived_at,
                    "summary": summary,
                    "path": f"seasons/{name}",
                }
            )
        result.sort(key=lambda item: item.get("archived_at") or "", reverse=True)
        return result

    @staticmethod
    def _read_meta(season_dir):
        try:
            with open(os.path.join(season_dir, "meta.json"), "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def validate_new(self, name):
        """Validate a new archive name and make sure it is not taken."""
        name = sanitize_name(name)
        season_dir = os.path.join(self.seasons_dir, name)
        if os.path.exists(season_dir):
            raise SeasonExistsError(f'season "{name}" already exists')
        try:
            existing = {
                entry.lower()
                for entry in os.listdir(self.seasons_dir)
                if os.path.isdir(os.path.join(self.seasons_dir, entry))
            }
        except OSError:
            existing = set()
        if name.lower() in existing:
            raise SeasonExistsError(f'season "{name}" already exists')
        return name

    def archive(self, name, summary=None):
        """Move the current files into a new archive folder and write meta."""
        name = self.validate_new(name)
        season_dir = os.path.join(self.seasons_dir, name)
        summary = _normal_summary(summary or self.current_summary())
        os.makedirs(self.seasons_dir, exist_ok=True)
        os.makedirs(season_dir)
        moved = []
        pairs = [
            (self.current_history_path, "history.json"),
            (self.current_history_path + ".bak", "history.json.bak"),
            (self.current_events_path, "cash_events.csv"),
            (self.current_events_path + ".bak", "cash_events.csv.bak"),
        ]
        try:
            for source, target_name in pairs:
                if not os.path.exists(source):
                    continue
                target = os.path.join(season_dir, target_name)
                os.replace(source, target)
                moved.append((source, target))
            meta = {
                "name": name,
                "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "summary": summary,
            }
            with open(
                os.path.join(season_dir, "meta.json"), "w", encoding="utf-8"
            ) as handle:
                json.dump(meta, handle, indent=2)
        except OSError:
            for source, target in reversed(moved):
                try:
                    os.replace(target, source)
                except OSError:
                    pass
            shutil.rmtree(season_dir, ignore_errors=True)
            raise

        return {
            "id": name,
            "name": name,
            "current": False,
            "archived_at": meta["archived_at"],
            "summary": summary,
            "path": f"seasons/{name}",
        }

    # --------------------------------------------------------------- paths
    def paths(self, season_id=None):
        """Return (history_path, events_path) for Current or an archive."""
        text = "" if season_id is None else str(season_id).strip()
        if not text or text.lower() == "current":
            return self.current_history_path, self.current_events_path

        name = sanitize_name(text)
        season_dir = os.path.abspath(os.path.join(self.seasons_dir, name))
        if os.path.normcase(os.path.commonpath([season_dir, self.seasons_dir])) != os.path.normcase(
            self.seasons_dir
        ):
            raise InvalidSeasonName("season path is outside the archive folder")
        if not os.path.isdir(season_dir):
            raise SeasonNotFound(f'season "{name}" was not found')
        return (
            os.path.join(season_dir, "history.json"),
            os.path.join(season_dir, "cash_events.csv"),
        )

    # --------------------------------------------------------------- reset
    def reset_current(self):
        """Create a fresh current history and CSV header after archiving."""
        os.makedirs(self.data_dir, exist_ok=True)
        HistoryStore(self.current_history_path).reset()
        for leftover in (
            self.current_history_path + ".bak",
            self.current_events_path + ".bak",
        ):
            try:
                os.remove(leftover)
            except OSError:
                pass
        with open(self.current_events_path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(EVENTS_HEADER)
        return self.current_descriptor()
