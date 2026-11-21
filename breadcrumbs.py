"""Read Wardogs map and spawn breadcrumbs from the Sentry-native log.

Wardogs ships with the Sentry native crash reporter.  While the game runs it
writes MessagePack breadcrumbs under::

    <install>\\Wardogs\\.sentry-native\\<run-id>.run\\__sentry-breadcrumb*

The two records we care about are:

* ``Unreal / PostLoadMapWithWorld`` with ``data["Map"]``
* ``Player / Player Spawned`` with a UTC timestamp

The files are a rolling per-run buffer, so the tracker polls them while the
game is running.  Nothing here writes to the game or its files.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    import msgpack
except ImportError:  # pragma: no cover - optional dependency
    msgpack = None

from names import MAP_NAMES, display_map_name

APP_ID = 1867240
DEFAULT_STEAM_PATH = r"C:\Program Files (x86)\Steam"


def _steam_install_path():
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            if value:
                return str(value).replace("/", "\\")
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return DEFAULT_STEAM_PATH


def _steam_library_paths(steam_path=None):
    steam_path = steam_path or _steam_install_path()
    paths = [steam_path]
    vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped.startswith('"path"'):
                    continue
                parts = stripped.split('"')
                if len(parts) > 3 and parts[3]:
                    paths.append(parts[3].replace("\\\\", "\\"))
    except OSError:
        pass

    unique = []
    seen = set()
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def find_install_dir(steam_path=None):
    """Return the Wardogs install directory, or None."""
    for library in _steam_library_paths(steam_path):
        manifest = os.path.join(
            library, "steamapps", f"appmanifest_{APP_ID}.acf"
        )
        try:
            with open(manifest, "r", encoding="utf-8") as handle:
                for line in handle:
                    if '"installdir"' not in line:
                        continue
                    parts = line.split('"')
                    if len(parts) <= 3 or not parts[3]:
                        continue
                    candidate = os.path.join(
                        library, "steamapps", "common", parts[3]
                    )
                    if os.path.isdir(candidate):
                        return candidate
        except OSError:
            continue
    return None


def find_latest_run_dir(install_dir):
    if not install_dir:
        return None
    root = os.path.join(install_dir, "Wardogs", ".sentry-native")
    try:
        runs = [
            os.path.join(root, name)
            for name in os.listdir(root)
            if name.endswith(".run")
            and os.path.isdir(os.path.join(root, name))
        ]
    except OSError:
        return None
    if not runs:
        return None
    return max(runs, key=os.path.getmtime)


def find_breadcrumb_files(run_dir):
    if not run_dir:
        return []
    try:
        names = [
            name
            for name in os.listdir(run_dir)
            if name.startswith("__sentry-breadcrumb")
        ]
    except OSError:
        return []
    return [os.path.join(run_dir, name) for name in sorted(names)]


def read_breadcrumbs(path):
    """Read concatenated MessagePack objects, tolerating a partial tail."""
    if msgpack is None or not path:
        return []
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return []

    unpacker = msgpack.Unpacker(raw=False)
    try:
        unpacker.feed(raw)
    except Exception:
        return []

    items = []
    while True:
        try:
            item = unpacker.unpack()
        except Exception:
            break
        if isinstance(item, dict):
            items.append(item)
    return items


def parse_breadcrumb_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _crumb_key(crumb):
    return (
        crumb.get("timestamp"),
        crumb.get("category"),
        crumb.get("message"),
        repr(crumb.get("data")),
    )


class BreadcrumbReader:
    """Incrementally collect map and spawn breadcrumbs for the newest run."""

    def __init__(self, install_dir=None):
        self.install_dir = install_dir or find_install_dir()
        self._run_dir = None
        self._files = []
        self._seen = set()
        self._map_raw = None
        self._spawns = []

    @property
    def map_raw(self):
        return self._map_raw

    def _refresh(self):
        run_dir = find_latest_run_dir(self.install_dir)
        if run_dir != self._run_dir:
            self._run_dir = run_dir
            self._seen.clear()
            self._map_raw = None
            self._spawns = []
        self._files = find_breadcrumb_files(run_dir)

    def _apply(self, crumb):
        category = crumb.get("category")
        message = crumb.get("message")
        if category == "Unreal" and message == "PostLoadMapWithWorld":
            data = crumb.get("data") or {}
            raw = data.get("Map")
            if raw:
                self._map_raw = str(raw)
        elif category == "Player" and message == "Player Spawned":
            when = parse_breadcrumb_time(crumb.get("timestamp"))
            if when is not None:
                self._spawns.append(when)

    def poll(self):
        self._refresh()
        added = []
        for path in self._files:
            for crumb in read_breadcrumbs(path):
                key = _crumb_key(crumb)
                if key in self._seen:
                    continue
                self._seen.add(key)
                self._apply(crumb)
                added.append(crumb)
        return added

    def current_map(self):
        raw = self._map_raw
        if not raw or raw.strip().lower() == "frontend":
            return None
        return display_map_name(raw)

    def spawn_events(self):
        return sorted(self._spawns)

    def spawns_since(self, started_at):
        if started_at is None:
            return []
        return [spawn for spawn in self._spawns if spawn >= started_at]
