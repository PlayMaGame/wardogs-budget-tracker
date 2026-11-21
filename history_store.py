"""Persistent cash history for the Wardogs tracker.

The tracker only sees the current match profit from Steam Rich Presence, so
this module stores what it observes: seasons (game launch -> game close),
matches, the live counters, and lifetime totals.

The JSON file is written atomically (temp file + os.replace) and a .bak copy
is kept.  If the file is unreadable the next launch falls back to the backup
instead of losing everything.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime, timezone

VERSION = 2
MATCH_LIMIT = 5000


def utcnow():
    return datetime.now(timezone.utc)


def iso(value=None):
    return (value or utcnow()).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _empty_lifetime():
    return {"sessions": 0, "matches": 0, "balance": 0, "spent": 0, "gained": 0}


def empty_state():
    return {
        "version": VERSION,
        "updated_at": iso(),
        "active_session": None,
        "active_match": None,
        "last_session": None,
        "last_match": None,
        "sessions": [],
        "matches": [],
        "lifetime": _empty_lifetime(),
    }


def _money(value, signed=False):
    value = _int(value)
    if signed:
        if value > 0:
            return f"+${value:,}"
        if value < 0:
            return f"-${abs(value):,}"
        return "$0"
    return f"${abs(value):,}"


def _duration(seconds):
    seconds = max(0, _int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def _when(value):
    parsed = parse_iso(value)
    if not parsed:
        return "?"
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


class HistoryStore:
    def __init__(self, path):
        self.path = path
        self.data = empty_state()
        self.load()

    # ------------------------------------------------------------- lifecycle
    def load(self):
        for candidate in (self.path, self.path + ".bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    self.data = self._normalize(data)
                    return
            except (OSError, ValueError, TypeError):
                continue

        if os.path.exists(self.path):
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                os.replace(self.path, f"{self.path}.corrupt-{stamp}")
            except OSError:
                pass
        self.data = empty_state()

    def _normalize(self, data):
        state = empty_state()
        state.update(data)
        state["version"] = VERSION
        if not isinstance(state.get("sessions"), list):
            state["sessions"] = []
        if not isinstance(state.get("matches"), list):
            state["matches"] = []
        lifetime = state.get("lifetime")
        if not isinstance(lifetime, dict):
            lifetime = _empty_lifetime()
        for key, value in _empty_lifetime().items():
            lifetime.setdefault(key, value)
        state["lifetime"] = lifetime
        for key in ("active_session", "active_match", "last_session", "last_match"):
            if state.get(key) is not None and not isinstance(state[key], dict):
                state[key] = None
        return state

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self.data["version"] = VERSION
            self.data["updated_at"] = iso()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
            if os.path.exists(self.path):
                try:
                    shutil.copy2(self.path, self.path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, self.path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def reset(self):
        """Clear all history and write a fresh, empty current file.

        The previous data is intentionally not kept as ``.bak``: callers use
        this after archiving the old files, and a stale backup would make the
        dashboard fall back to the old season if the new file was unreadable.
        """
        self.data = empty_state()
        self.save()
        try:
            os.remove(self.path + ".bak")
        except OSError:
            pass

    # --------------------------------------------------------------- access
    @property
    def active_session(self):
        return self.data.get("active_session")

    @property
    def active_match(self):
        return self.data.get("active_match")

    @property
    def last_session(self):
        return self.data.get("last_session")

    @property
    def last_match(self):
        return self.data.get("last_match")

    def set_pid(self, pid):
        session = self.active_session
        if session is not None and pid:
            session["game_pid"] = pid
            session["last_seen_at"] = iso()

    def match_gap_seconds(self):
        match = self.active_match
        if not match:
            return 0
        last = parse_iso(match.get("last_seen_at"))
        if not last:
            return 0
        return max(0, (utcnow() - last).total_seconds())

    def session_gap_seconds(self):
        session = self.active_session
        if not session:
            return 0
        last = parse_iso(session.get("last_seen_at"))
        if not last:
            return 0
        return max(0, (utcnow() - last).total_seconds())

    # -------------------------------------------------------------- session
    def start_session(self, pid=None):
        if self.active_session is not None:
            return self.active_session
        now = utcnow()
        session = {
            "id": f"{now.strftime('%Y%m%dT%H%M%SZ')}-{pid or 0}",
            "started_at": iso(now),
            "ended_at": None,
            "game_pid": pid,
            "balance": 0,
            "spent": 0,
            "gained": 0,
            "matches": 0,
            "active": True,
            "interrupted": False,
            "last_seen_at": iso(now),
        }
        self.data["active_session"] = session
        self.save()
        return session

    def update_session(self, spent, gained, balance):
        session = self.active_session
        if session is None:
            return
        session["spent"] = int(spent)
        session["gained"] = int(gained)
        session["balance"] = int(balance)
        session["last_seen_at"] = iso()

    def end_session(self, interrupted=False):
        if self.active_match is not None:
            self.end_match(interrupted=interrupted)
        session = self.active_session
        if session is None:
            return None
        now = utcnow()
        session["ended_at"] = iso(now)
        session["active"] = False
        session["interrupted"] = bool(interrupted)
        session["last_seen_at"] = iso(now)
        record = dict(session)
        self.data["sessions"].append(record)
        if len(self.data["sessions"]) > MATCH_LIMIT:
            self.data["sessions"] = self.data["sessions"][-MATCH_LIMIT:]
        self.data["last_session"] = record
        lifetime = self.data["lifetime"]
        lifetime["sessions"] = _int(lifetime.get("sessions")) + 1
        self.data["active_session"] = None
        self.save()
        return record

    def pause(self):
        session = self.active_session
        if session is not None:
            session["last_seen_at"] = iso()
        match = self.active_match
        if match is not None:
            match["last_seen_at"] = iso()
        self.save()

    # ---------------------------------------------------------------- match
    def start_match(self, faction, partial=False):
        if self.active_match is not None:
            return self.active_match
        if self.active_session is None:
            self.start_session()
        session = self.active_session
        session["matches"] = _int(session.get("matches")) + 1
        now = utcnow()
        match = {
            "id": f"{session['id']}-m{session['matches']}",
            "session_id": session["id"],
            "started_at": iso(now),
            "ended_at": None,
            "faction": faction or "?",
            "balance": 0,
            "spent": 0,
            "gained": 0,
            "last_profit": 0,
            "partial": bool(partial),
            "resumed": False,
            "active": True,
            "interrupted": False,
            "last_seen_at": iso(now),
            "duration_s": 0,
            "map": None,
            "map_raw": None,
            "lives": None,
            "spawns": 0,
            "role_xp": {},
            "role_xp_total": {},
            "role_xp_partial": False,
        }
        self.data["active_match"] = match
        self.save()
        return match

    def update_match(self, spent, gained, balance, last_profit=None):
        match = self.active_match
        if match is None:
            return
        match["spent"] = int(spent)
        match["gained"] = int(gained)
        match["balance"] = int(balance)
        if last_profit is not None:
            match["last_profit"] = int(last_profit)
        match["last_seen_at"] = iso()

    def update_match_meta(self, **fields):
        """Attach non-cash match metadata (map, lives, role XP, ...)."""
        match = self.active_match
        if match is None:
            return
        for key, value in fields.items():
            if value is not None:
                match[key] = value
        match["last_seen_at"] = iso()
        self.save()

    def mark_resumed(self):
        match = self.active_match
        if match is not None:
            match["resumed"] = True
            match["partial"] = True
            match["last_seen_at"] = iso()
            self.save()

    def end_match(self, interrupted=False):
        match = self.active_match
        if match is None:
            return None
        now = utcnow()
        started = parse_iso(match.get("started_at"))
        duration = int((now - started).total_seconds()) if started else 0
        match["ended_at"] = iso(now)
        match["duration_s"] = max(0, duration)
        match["active"] = False
        match["interrupted"] = bool(interrupted)
        match["last_seen_at"] = iso(now)
        record = dict(match)
        self.data["matches"].append(record)
        if len(self.data["matches"]) > MATCH_LIMIT:
            self.data["matches"] = self.data["matches"][-MATCH_LIMIT:]
        self.data["last_match"] = record
        lifetime = self.data["lifetime"]
        lifetime["matches"] = _int(lifetime.get("matches")) + 1
        lifetime["balance"] = _int(lifetime.get("balance")) + _int(record.get("balance"))
        lifetime["spent"] = _int(lifetime.get("spent")) + _int(record.get("spent"))
        lifetime["gained"] = _int(lifetime.get("gained")) + _int(record.get("gained"))
        self.data["active_match"] = None
        self.save()
        return record

    # -------------------------------------------------------------- display
    def render_history(self, limit=20):
        limit = max(1, int(limit))
        lines = ["Wardogs cash history", ""]
        matches = list(self.data.get("matches", []))
        active = self.active_match
        if active is not None:
            matches.append(active)
        matches = matches[-limit:]
        if matches:
            lines.append(
                f"{'WHEN':<16}  {'FACTION':<8} {'RESULT':>10}  "
                f"{'SPENT':>9}  {'GAINED':>9}  {'DURATION':>9}  FLAGS"
            )
            for match in reversed(matches):
                flags = []
                if match.get("active"):
                    flags.append("live")
                if match.get("partial"):
                    flags.append("partial")
                if match.get("resumed"):
                    flags.append("resumed")
                if match.get("interrupted"):
                    flags.append("interrupted")
                duration = match.get("duration_s")
                if match.get("active"):
                    started = parse_iso(match.get("started_at"))
                    duration = (
                        max(0, int((utcnow() - started).total_seconds()))
                        if started
                        else 0
                    )
                lines.append(
                    f"{_when(match.get('started_at')):<16}  "
                    f"{str(match.get('faction') or '?'):<8} "
                    f"{_money(match.get('balance'), signed=True):>10}  "
                    f"{_money(match.get('spent')):>9}  "
                    f"{_money(match.get('gained')):>9}  "
                    f"{_duration(duration):>9}  "
                    f"{', '.join(flags)}"
                )
        else:
            lines.append("No matches recorded yet.")

        sessions = list(self.data.get("sessions", []))
        active_session = self.active_session
        if active_session is not None:
            sessions.append(active_session)
        sessions = sessions[-limit:]
        if sessions:
            lines.append("")
            lines.append(
                f"{'SEASON START':<16}  {'ENDED':<16}  {'RESULT':>10}  "
                f"{'SPENT':>9}  {'GAINED':>9}  {'MATCHES':>7}  FLAGS"
            )
            for session in reversed(sessions):
                flags = []
                if session.get("active"):
                    flags.append("live")
                if session.get("interrupted"):
                    flags.append("interrupted")
                lines.append(
                    f"{_when(session.get('started_at')):<16}  "
                    f"{_when(session.get('ended_at')) if session.get('ended_at') else 'live':<16}  "
                    f"{_money(session.get('balance'), signed=True):>10}  "
                    f"{_money(session.get('spent')):>9}  "
                    f"{_money(session.get('gained')):>9}  "
                    f"{_int(session.get('matches')):>7}  "
                    f"{', '.join(flags)}"
                )

        lifetime = self.data.get("lifetime", {})
        lines.append("")
        lines.append(
            "Lifetime: "
            f"{_int(lifetime.get('matches'))} matches, "
            f"{_int(lifetime.get('sessions'))} seasons, "
            f"net {_money(lifetime.get('balance'), signed=True)}, "
            f"spent {_money(lifetime.get('spent'))}, "
            f"gained {_money(lifetime.get('gained'))}"
        )
        if self.active_session is not None:
            lines.append(
                "Active: "
                f"season {_money(self.active_session.get('balance'), signed=True)}, "
                f"{_int(self.active_session.get('matches'))} matches"
            )
        return "\n".join(lines)

    # -------------------------------------------------------------- migration
    def migrate_csv(self, csv_path):
        """Import the old event log once, if there is no JSON history yet."""
        if not os.path.exists(csv_path):
            return False
        if self.data.get("sessions") or self.data.get("active_session"):
            return False

        sessions = []
        matches = []
        active_session = None
        active_match = None
        last_time = None
        session_seq = 0
        match_seq = 0

        def new_session(when):
            nonlocal session_seq
            session_seq += 1
            return {
                "id": f"csv-{when.strftime('%Y%m%dT%H%M%SZ')}-{session_seq}",
                "started_at": iso(when),
                "ended_at": None,
                "game_pid": None,
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "matches": 0,
                "active": True,
                "interrupted": False,
                "last_seen_at": iso(when),
            }

        def new_match(when, faction):
            nonlocal match_seq
            match_seq += 1
            active_session["matches"] = _int(active_session.get("matches")) + 1
            return {
                "id": f"{active_session['id']}-m{match_seq}",
                "session_id": active_session["id"],
                "started_at": iso(when),
                "ended_at": None,
                "faction": faction or "?",
                "balance": 0,
                "spent": 0,
                "gained": 0,
                "last_profit": 0,
                "partial": False,
                "resumed": False,
                "active": True,
                "interrupted": False,
                "last_seen_at": iso(when),
                "duration_s": 0,
                "map": None,
                "map_raw": None,
                "lives": None,
                "spawns": 0,
                "role_xp": {},
                "role_xp_total": {},
                "role_xp_partial": False,
            }

        def close_match(when, interrupted=False, balance=None):
            nonlocal active_match
            if active_match is None:
                return
            active_match["ended_at"] = iso(when)
            active_match["active"] = False
            active_match["interrupted"] = bool(interrupted)
            if balance is not None:
                active_match["balance"] = int(balance)
            started = parse_iso(active_match.get("started_at"))
            active_match["duration_s"] = (
                max(0, int((when - started).total_seconds())) if started else 0
            )
            record = dict(active_match)
            matches.append(record)
            self.data["last_match"] = record
            active_match = None

        def close_session(when, interrupted=False, balance=None):
            nonlocal active_session
            if active_match is not None:
                close_match(when, interrupted=interrupted)
            if active_session is None:
                return
            active_session["ended_at"] = iso(when)
            active_session["active"] = False
            active_session["interrupted"] = bool(interrupted)
            if balance is not None:
                active_session["balance"] = int(balance)
            record = dict(active_session)
            sessions.append(record)
            self.data["last_session"] = record
            active_session = None

        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    when = parse_iso(row.get("time_utc")) or utcnow()
                    last_time = when
                    event = (row.get("event") or "").strip()
                    amount = _int(row.get("amount"))
                    match_balance = _int(row.get("match_balance"))
                    session_balance = _int(row.get("session_balance"))
                    faction = row.get("faction") or "?"

                    if event == "session_start":
                        if active_session is not None:
                            close_session(when, interrupted=True)
                        active_session = new_session(when)
                        match_seq = 0
                    elif event == "match_start":
                        if active_session is None:
                            active_session = new_session(when)
                        if active_match is not None:
                            close_match(when, interrupted=True)
                        active_match = new_match(when, faction)
                    elif event in ("spent", "gained"):
                        if active_session is None:
                            active_session = new_session(when)
                        if active_match is None:
                            active_match = new_match(when, faction)
                        if event == "spent":
                            active_match["spent"] += abs(amount)
                            active_session["spent"] += abs(amount)
                        else:
                            active_match["gained"] += max(0, amount)
                            active_session["gained"] += max(0, amount)
                        active_match["balance"] = match_balance
                        active_match["last_profit"] = match_balance
                        active_session["balance"] = session_balance
                        active_match["last_seen_at"] = iso(when)
                        active_session["last_seen_at"] = iso(when)
                    elif event == "match_end":
                        close_match(when, interrupted=False, balance=match_balance)
                    elif event == "session_end":
                        close_session(when, interrupted=False, balance=session_balance)
        except OSError:
            return False

        if last_time is None:
            return False

        if active_session is not None:
            active_session["last_seen_at"] = iso(last_time)
        if active_match is not None:
            active_match["last_seen_at"] = iso(last_time)

        self.data["sessions"] = sessions
        self.data["matches"] = matches[-MATCH_LIMIT:]
        self.data["active_session"] = active_session
        self.data["active_match"] = active_match
        self.data["last_session"] = (
            sessions[-1] if sessions else (dict(active_session) if active_session else None)
        )
        self.data["last_match"] = (
            matches[-1] if matches else (dict(active_match) if active_match else None)
        )
        self.data["lifetime"] = {
            "sessions": len(sessions),
            "matches": len(matches),
            "balance": sum(_int(m.get("balance")) for m in matches),
            "spent": sum(_int(m.get("spent")) for m in matches),
            "gained": sum(_int(m.get("gained")) for m in matches),
        }
        self.data["updated_at"] = iso(last_time)
        self.save()
        return True
