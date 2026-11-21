"""Local cash dashboard for the Wardogs tracker.

Run it with ``python dashboard.py`` and open http://127.0.0.1:8765/.  It reads
the same ``data/history.json`` and ``data/cash_events.csv`` the tracker writes;
it never touches the game, Steam, or the input files.

The dashboard is read-only except for the explicit New Season action.  That
action asks for a name, archives the current files under ``data/seasons/`` and
starts a fresh current season.  It is accepted only from this computer.

The tracker starts this server automatically.  Use ``--host 0.0.0.0`` to make it
reachable from another PC on the same network.
"""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import os
import socket
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from history_store import empty_state, parse_iso
from gold_price import GoldPriceService
from names import display_faction, display_map_name
from seasons import (
    InvalidSeasonName,
    SeasonExistsError,
    SeasonNotFound,
    SeasonStore,
    sanitize_name,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_JSON = os.path.join(DATA_DIR, "history.json")
EVENTS_CSV = os.path.join(DATA_DIR, "cash_events.csv")
INDEX_HTML = os.path.join(BASE_DIR, "dashboard", "index.html")

EVENT_FIELDS = [
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


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cash_per_hour(balance, duration_s):
    try:
        duration_s = int(duration_s)
        balance = int(balance)
    except (TypeError, ValueError):
        return None
    if duration_s < 60:
        return None
    return int(round(balance * 3600 / duration_s))


def _lan_ip():
    """Best-effort local IPv4 address; no external traffic is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def is_loopback_address(value):
    """True only for addresses that can only come from this computer."""
    text = str(value or "").strip()
    if text.lower().startswith("::ffff:"):
        text = text[7:]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def dashboard_urls(host, port):
    """URLs a user can try for a server bound to *host*."""
    host = (host or "127.0.0.1").strip()
    if host in ("0.0.0.0", "::", ""):
        urls = [f"http://127.0.0.1:{port}/"]
        ip = _lan_ip()
        if ip and ip != "127.0.0.1":
            urls.append(f"http://{ip}:{port}/")
        return urls
    if host in ("127.0.0.1", "localhost"):
        return [f"http://127.0.0.1:{port}/"]
    return [f"http://{host}:{port}/"]


def _summary(match, now=None):
    if not match:
        return None
    item = dict(match)
    started = parse_iso(item.get("started_at"))
    ended = parse_iso(item.get("ended_at"))
    if item.get("active") or item.get("ended_at") is None:
        if started:
            item["duration_s"] = max(
                0, int(((now or datetime.now(timezone.utc)) - started).total_seconds())
            )
    else:
        item["duration_s"] = _int(item.get("duration_s"))
    item["map"] = (
        display_map_name(item.get("map_raw") or item.get("map")) or "Unknown"
    )
    item["faction"] = display_faction(item.get("faction"))
    item["net"] = _int(item.get("balance"))
    item["cash_per_hour"] = _cash_per_hour(item["net"], item.get("duration_s"))
    return item


class DashboardData:
    def __init__(self, history_path=HISTORY_JSON, events_path=EVENTS_CSV):
        self.history_path = history_path
        self.events_path = events_path

    # ------------------------------------------------------------- loading
    def _history(self):
        candidates = (self.history_path, self.history_path + ".bak")
        for attempt in range(5):
            for candidate in candidates:
                try:
                    with open(candidate, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                state = empty_state()
                state.update(data)
                for key in ("sessions", "matches"):
                    if not isinstance(state.get(key), list):
                        state[key] = []
                if not isinstance(state.get("lifetime"), dict):
                    state["lifetime"] = {}
                return state
            if attempt < 4:
                time.sleep(0.01)
        return empty_state()

    def _events(self):
        try:
            with open(self.events_path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
        except (OSError, ValueError, TypeError):
            return []
        events = []
        for row in rows:
            event = (row.get("event") or "").strip()
            if not event:
                continue
            match_id = _int(row.get("match_id"))
            session_id = (row.get("session_id") or "").strip()
            match_uid = (row.get("match_uid") or "").strip()
            if not match_uid and session_id and match_id:
                match_uid = f"{session_id}-m{match_id}"
            events.append(
                {
                    "time_utc": row.get("time_utc") or "",
                    "match_id": match_id,
                    "session_id": session_id,
                    "match_uid": match_uid,
                    "event": event,
                    "amount": _int(row.get("amount")),
                    "match_balance": _int(row.get("match_balance")),
                    "session_balance": _int(row.get("session_balance")),
                    "faction": row.get("faction") or "",
                }
            )
        return events

    def _all_matches(self):
        history = self._history()
        matches = list(history.get("matches") or [])
        active = history.get("active_match")
        if active and all(m.get("id") != active.get("id") for m in matches):
            active = dict(active)
            active["active"] = True
            matches.append(active)
        now = datetime.now(timezone.utc)
        result = [_summary(match, now=now) for match in matches]
        result.sort(key=lambda m: parse_iso(m.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return result

    @staticmethod
    def _session_summary(session, now=None):
        if not session:
            return None
        item = dict(session)
        started = parse_iso(item.get("started_at"))
        ended = parse_iso(item.get("ended_at"))
        if item.get("active") or ended is None:
            if started:
                item["duration_s"] = max(
                    0,
                    int(
                        ((now or datetime.now(timezone.utc)) - started).total_seconds()
                    ),
                )
            else:
                item["duration_s"] = 0
        elif started:
            item["duration_s"] = max(0, int((ended - started).total_seconds()))
        else:
            item["duration_s"] = _int(item.get("duration_s"))
        item["net"] = _int(item.get("balance"))
        return item

    def _all_sessions(self):
        history = self._history()
        sessions = [
            dict(session)
            for session in (history.get("sessions") or [])
            if isinstance(session, dict)
        ]
        active = history.get("active_session")
        if isinstance(active, dict):
            active = dict(active)
            active["active"] = True
            sessions = [
                session for session in sessions if session.get("id") != active.get("id")
            ]
            sessions.append(active)
        now = datetime.now(timezone.utc)
        result = [self._session_summary(session, now=now) for session in sessions]
        result.sort(
            key=lambda session: parse_iso(session.get("started_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return result

    def _session_curve(self, session, matches):
        started = session.get("started_at")
        curve = [{"time": started, "balance": 0}] if started else []
        offset = 0
        for match in reversed(matches):
            match_start = match.get("started_at")
            if match_start and (not curve or curve[-1]["time"] != match_start):
                curve.append({"time": match_start, "balance": offset})
            running = 0
            for event in self._match_events(match):
                if event.get("event") not in ("spent", "gained"):
                    continue
                event_time = event.get("time_utc")
                if not event_time:
                    continue
                amount = event.get("amount")
                if amount in (None, ""):
                    running = _int(event.get("match_balance"))
                else:
                    running += _int(amount)
                curve.append({"time": event_time, "balance": offset + running})
            offset += _int(match.get("net"))
            ended = match.get("ended_at")
            if ended and (not curve or curve[-1]["time"] != ended):
                curve.append({"time": ended, "balance": offset})
            elif match.get("active") and not ended:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                last = parse_iso(curve[-1]["time"]) if curve else None
                if last is None or last < parse_iso(now):
                    curve.append({"time": now, "balance": offset})
        ended = session.get("ended_at")
        if ended and (not curve or curve[-1]["time"] != ended):
            curve.append({"time": ended, "balance": offset})
        return curve

    # -------------------------------------------------------------- public
    def state(self):
        history = self._history()
        events = self._events()
        active_match = history.get("active_match")
        recent_matches = [
            _summary(match)
            for match in reversed((history.get("matches") or [])[-10:])
        ]
        recent_sessions = list(reversed((history.get("sessions") or [])[-20:]))
        return {
            "updated_at": history.get("updated_at"),
            "active_session": history.get("active_session"),
            "active_match": _summary(active_match) if active_match else None,
            "last_session": history.get("last_session"),
            "last_match": _summary(history.get("last_match")),
            "lifetime": history.get("lifetime") or {},
            "sessions": len(history.get("sessions") or []),
            "matches": len(history.get("matches") or []),
            "recent_events": events[-20:],
            "recent_matches": recent_matches,
            "recent_sessions": recent_sessions,
        }

    def sessions(self):
        return self._all_sessions()

    def session(self, uid):
        found = next(
            (session for session in self._all_sessions() if session.get("id") == uid),
            None,
        )
        if found is None:
            return None
        detail = dict(found)
        matches = [
            match for match in self._all_matches() if match.get("session_id") == uid
        ]
        matches.sort(
            key=lambda match: parse_iso(match.get("started_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        detail["matches"] = matches
        detail["curve"] = self._session_curve(detail, matches)
        return detail

    def matches(
        self,
        faction=None,
        map_name=None,
        result=None,
        since=None,
        q=None,
        limit=None,
    ):
        matches = self._all_matches()
        if faction:
            wanted = display_faction(faction).strip().lower()
            matches = [
                m for m in matches if str(m.get("faction") or "").lower() == wanted
            ]
        if map_name:
            wanted = (display_map_name(map_name) or "").strip().lower()
            matches = [m for m in matches if str(m.get("map") or "").lower() == wanted]
        if result:
            result = result.strip().lower()
            if result == "profit":
                matches = [m for m in matches if _int(m.get("net")) > 0]
            elif result == "loss":
                matches = [m for m in matches if _int(m.get("net")) < 0]
            elif result == "even":
                matches = [m for m in matches if _int(m.get("net")) == 0]
        if since:
            cutoff = parse_iso(since)
            if cutoff is None and str(since).isdigit():
                from datetime import timedelta

                cutoff = datetime.now(timezone.utc) - timedelta(days=int(since))
            if cutoff is not None:
                matches = [
                    m
                    for m in matches
                    if (parse_iso(m.get("started_at")) or cutoff) >= cutoff
                ]
        if q:
            needle = q.strip().lower()
            matches = [
                m
                for m in matches
                if needle
                in " ".join(
                    str(m.get(key) or "")
                    for key in ("id", "faction", "map", "started_at")
                ).lower()
            ]
        if limit:
            matches = matches[: max(0, _int(limit))]
        return matches

    def _match_events(self, match):
        events = self._events()
        uid = match.get("id")
        exact = [event for event in events if event.get("match_uid") == uid]
        if exact:
            return exact

        session_id = match.get("session_id")
        suffix = str(uid).rsplit("-m", 1)[-1] if uid else ""
        number = _int(suffix, None) if suffix.isdigit() else None
        started = parse_iso(match.get("started_at"))
        ended = parse_iso(match.get("ended_at"))
        fallback = []
        for event in events:
            if event.get("match_uid"):
                continue
            if event.get("session_id") and session_id and event["session_id"] != session_id:
                continue
            if number is not None and event.get("match_id") != number:
                continue
            when = parse_iso(event.get("time_utc"))
            if started and when and when < started.replace(microsecond=0):
                continue
            if ended and when and when > ended.replace(microsecond=0):
                continue
            fallback.append(event)
        return fallback

    def match(self, uid):
        found = next((m for m in self._all_matches() if m.get("id") == uid), None)
        if found is None:
            return None
        detail = dict(found)
        events = self._match_events(found)
        detail["events"] = events
        curve = [{"time": detail.get("started_at"), "balance": 0}]
        for event in events:
            if event.get("event") not in ("spent", "gained"):
                continue
            curve.append(
                {
                    "time": event.get("time_utc"),
                    "balance": _int(event.get("match_balance")),
                }
            )
        detail["curve"] = curve
        detail["role_xp"] = detail.get("role_xp") or {}
        return detail

    def insights(self):
        history = self._history()
        all_matches = self._all_matches()
        completed = [m for m in all_matches if not m.get("active")] or all_matches
        totals = self._metrics(completed)
        totals["sessions"] = len(history.get("sessions") or [])
        if history.get("active_session"):
            totals["sessions"] += 1
        totals["profitable"] = sum(1 for m in completed if _int(m.get("net")) > 0)
        totals["profitable_rate"] = (
            round(totals["profitable"] * 100 / totals["matches"])
            if totals["matches"]
            else 0
        )

        def grouped(key):
            groups = {}
            for match in completed:
                name = str(match.get(key) or "Unknown")
                groups.setdefault(name, []).append(match)
            return {
                name: self._metrics(items)
                for name, items in sorted(groups.items())
            }

        by_hour = {}
        for match in completed:
            started = parse_iso(match.get("started_at"))
            if not started:
                continue
            hour = started.astimezone().hour
            by_hour.setdefault(str(hour).zfill(2), []).append(match)
        per_hour = {
            hour: self._metrics(items) for hour, items in sorted(by_hour.items())
        }

        buckets = [
            ("< 15m", 0, 900),
            ("15-30m", 900, 1800),
            ("30-60m", 1800, 3600),
            ("60m+", 3600, None),
        ]
        length_buckets = []
        for label, low, high in buckets:
            items = [
                m
                for m in completed
                if _int(m.get("duration_s")) >= low
                and (high is None or _int(m.get("duration_s")) < high)
            ]
            entry = {"label": label}
            entry.update(self._metrics(items))
            length_buckets.append(entry)

        role_xp = {}
        for match in completed:
            for role, xp in (match.get("role_xp") or {}).items():
                role_xp[role] = role_xp.get(role, 0) + _int(xp)

        streaks = self._streaks(completed)
        best = max(completed, key=lambda m: _int(m.get("net")), default=None)
        worst = min(completed, key=lambda m: _int(m.get("net")), default=None)

        return {
            "totals": totals,
            "per_faction": grouped("faction"),
            "per_map": grouped("map"),
            "per_hour": per_hour,
            "length_buckets": length_buckets,
            "role_xp": dict(sorted(role_xp.items(), key=lambda item: -item[1])),
            "streaks": streaks,
            "best_match": best,
            "worst_match": worst,
            "top_gains": sorted(
                completed, key=lambda m: _int(m.get("gained")), reverse=True
            )[:5],
            "top_spends": sorted(
                completed, key=lambda m: _int(m.get("spent")), reverse=True
            )[:5],
            "facets": {
                "factions": sorted(
                    {str(m.get("faction") or "?") for m in all_matches}
                ),
                "maps": sorted({str(m.get("map") or "Unknown") for m in all_matches}),
            },
        }

    @staticmethod
    def _metrics(matches):
        count = len(matches)
        net = sum(_int(m.get("net")) for m in matches)
        spent = sum(_int(m.get("spent")) for m in matches)
        gained = sum(_int(m.get("gained")) for m in matches)
        duration = sum(_int(m.get("duration_s")) for m in matches)
        return {
            "matches": count,
            "net": net,
            "spent": spent,
            "gained": gained,
            "avg_net": int(round(net / count)) if count else 0,
            "duration_s": duration,
            "cash_per_hour": _cash_per_hour(net, duration),
        }

    @staticmethod
    def _streaks(matches):
        ordered = sorted(
            matches,
            key=lambda m: parse_iso(m.get("started_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        current = 0
        best = 0
        run = 0
        for match in ordered:
            if _int(match.get("net")) > 0:
                run += 1
                best = max(best, run)
                current = run
            else:
                run = 0
                current = 0
        # current should reflect the streak at the end of the list
        current = 0
        for match in reversed(ordered):
            if _int(match.get("net")) > 0:
                current += 1
            else:
                break
        return {"current": current, "best": best}

    def export_csv(self, **filters):
        matches = self.matches(**filters)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "session_id",
                "started_at",
                "ended_at",
                "duration_s",
                "faction",
                "map",
                "balance",
                "spent",
                "gained",
                "cash_per_hour",
                "partial",
                "resumed",
                "active",
                "lives",
                "role_xp",
            ]
        )
        for match in matches:
            writer.writerow(
                [
                    match.get("id"),
                    match.get("session_id"),
                    match.get("started_at"),
                    match.get("ended_at"),
                    match.get("duration_s"),
                    match.get("faction"),
                    match.get("map"),
                    match.get("net"),
                    match.get("spent"),
                    match.get("gained"),
                    match.get("cash_per_hour"),
                    bool(match.get("partial")),
                    bool(match.get("resumed")),
                    bool(match.get("active")),
                    match.get("lives"),
                    json.dumps(match.get("role_xp") or {}, ensure_ascii=False),
                ]
            )
        return output.getvalue()


def _one(query, key, default=None):
    values = query.get(key)
    if not values:
        return default
    return values[0]


class DashboardHandler(BaseHTTPRequestHandler):
    data = None
    seasons = None
    gold = None
    on_new_season = None
    index_path = INDEX_HTML

    def log_message(self, format, *args):
        return

    def _send(self, status, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, default=str),
            "application/json; charset=utf-8",
        )

    def _season_data(self, season_id):
        text = "" if season_id is None else str(season_id).strip()
        if not text or text.lower() == "current":
            return self.data
        if self.seasons is None:
            raise SeasonNotFound("season archives are not available")
        history_path, events_path = self.seasons.paths(text)
        return DashboardData(history_path, events_path)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                with open(self.index_path, "r", encoding="utf-8") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
                return
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            if path == "/api/seasons":
                can_archive = is_loopback_address(self.client_address[0])
                if self.seasons is not None:
                    payload = self.seasons.snapshot()
                    payload["can_archive"] = can_archive
                    self._json(payload)
                else:
                    self._json(
                        {
                            "current": {
                                "id": None,
                                "name": "Current",
                                "current": True,
                                "archived_at": None,
                                "summary": {},
                            },
                            "seasons": [],
                            "can_archive": can_archive,
                        }
                    )
                return

            if path == "/api/gold":
                if self.gold is not None:
                    self._json(self.gold.snapshot())
                else:
                    self._json(
                        {
                            "ok": False,
                            "offline": True,
                            "price": None,
                            "error": "gold price is not available",
                        }
                    )
                return

            data = self._season_data(_one(query, "season"))
            if path == "/api/state":
                self._json(data.state())
                return
            if path == "/api/matches":
                self._json(
                    data.matches(
                        faction=_one(query, "faction"),
                        map_name=_one(query, "map"),
                        result=_one(query, "result"),
                        since=_one(query, "since"),
                        q=_one(query, "q"),
                        limit=_one(query, "limit"),
                    )
                )
                return
            if path == "/api/insights":
                self._json(data.insights())
                return
            if path == "/api/sessions":
                self._json(data.sessions())
                return
            if path.startswith("/api/session/"):
                uid = unquote(path[len("/api/session/") :])
                session = data.session(uid)
                if session is None:
                    self._send(404, "not found", "text/plain; charset=utf-8")
                else:
                    self._json(session)
                return
            if path.startswith("/api/match/"):
                uid = unquote(path[len("/api/match/") :])
                match = data.match(uid)
                if match is None:
                    self._send(404, "not found", "text/plain; charset=utf-8")
                else:
                    self._json(match)
                return
            if path == "/api/export.csv":
                text = data.export_csv(
                    faction=_one(query, "faction"),
                    map_name=_one(query, "map"),
                    result=_one(query, "result"),
                    since=_one(query, "since"),
                    q=_one(query, "q"),
                )
                self._send(
                    200,
                    text,
                    "text/csv; charset=utf-8",
                )
                return
        except InvalidSeasonName as exc:
            self._json({"error": str(exc)}, status=400)
            return
        except SeasonNotFound as exc:
            self._json({"error": str(exc)}, status=404)
            return
        except Exception as exc:  # keep the dashboard alive on bad input
            self._send(500, f"dashboard error: {exc}", "text/plain; charset=utf-8")
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/new-season":
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        if not is_loopback_address(self.client_address[0]):
            self._json(
                {"error": "New Season can only be started from this computer."},
                status=403,
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            name = sanitize_name(payload.get("name"))
            if self.on_new_season is not None:
                result = self.on_new_season(name)
            else:
                if self.seasons is None:
                    raise RuntimeError("season archives are not available")
                descriptor = self.seasons.archive(name)
                self.seasons.reset_current()
                result = {"ok": True, "season": descriptor}
            self._json(result)
        except InvalidSeasonName as exc:
            self._json({"error": str(exc)}, status=400)
        except SeasonExistsError as exc:
            self._json({"error": str(exc)}, status=409)
        except SeasonNotFound as exc:
            self._json({"error": str(exc)}, status=404)
        except TimeoutError as exc:
            self._json({"error": str(exc)}, status=504)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._json({"error": f"could not start a new season: {exc}"}, status=500)


class DashboardServer:
    """Owns the HTTP server so the tracker can start and stop it."""

    def __init__(
        self,
        host="127.0.0.1",
        port=8765,
        data=None,
        index_path=INDEX_HTML,
        auto_open=False,
        season_store=None,
        on_new_season=None,
        gold=None,
    ):
        self.host = host
        self.port = int(port)
        self.data = data if data is not None else DashboardData()
        self.index_path = index_path
        self.auto_open = auto_open
        self.on_new_season = on_new_season
        self.gold = gold if gold is not None else GoldPriceService()
        self.season_store = season_store
        if self.season_store is None:
            history_path = getattr(self.data, "history_path", None)
            if history_path:
                self.season_store = SeasonStore(
                    os.path.dirname(os.path.abspath(history_path))
                )
        self.httpd = None
        self.thread = None
        self.error = None
        self._open_timer = None

    @property
    def local_url(self):
        return f"http://127.0.0.1:{self.port}/"

    def urls(self):
        return dashboard_urls(self.host, self.port)

    def start(self):
        if self.httpd is not None:
            return True
        self.error = None
        handler = type(
            "WardogsDashboardHandler",
            (DashboardHandler,),
            {
                "data": self.data,
                "seasons": self.season_store,
                "gold": self.gold,
                "on_new_season": self.on_new_season,
                "index_path": self.index_path,
            },
        )
        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            self.error = exc
            self.httpd = None
            return False

        self.port = self.httpd.server_port
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            name="wardogs-dashboard",
            daemon=True,
        )
        self.thread.start()
        if self.auto_open:
            self._open_timer = threading.Timer(
                0.6, lambda: webbrowser.open(self.local_url)
            )
            self._open_timer.daemon = True
            self._open_timer.start()
        return True

    def stop(self):
        if self._open_timer is not None:
            self._open_timer.cancel()
            self._open_timer = None
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        self.httpd = None
        self.thread = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Wardogs cash dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--history", default=HISTORY_JSON)
    parser.add_argument("--events", default=EVENTS_CSV)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)

    data = DashboardData(args.history, args.events)
    season_store = SeasonStore(os.path.dirname(os.path.abspath(args.history)))
    server = DashboardServer(
        host=args.host,
        port=args.port,
        data=data,
        season_store=season_store,
        auto_open=not args.no_open,
    )
    if not server.start():
        print(f"Wardogs cash dashboard could not start: {server.error}")
        return 1

    for url in server.urls():
        print(f"Wardogs cash dashboard: {url}")
    if server.host in ("0.0.0.0", "::"):
        print(
            "LAN access is enabled. If another PC cannot connect, allow Python "
            "on the Private network or run allow_dashboard_firewall.ps1 as "
            "Administrator."
        )
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
