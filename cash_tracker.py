"""cash_tracker.py - live Wardogs cash flow in the terminal.

Reads Wardogs' own Steam Rich Presence (`game_state`, `profit_loss`,
`faction`) through the game's steam_api64.dll.  The DLL is loaded into this
Python process; nothing is injected into the game and no game files are
written.

The game reports a single running number for the current match (the same
"+$150 Profit" shown in the Steam friends list).  Spent and gained are
derived from how that number moves:

    negative step -> spent
    positive step -> gained
    balance       -> the Steam number itself (always exact)

If a spend and a gain land in the same polling window they net out, so the
split can drift slightly.  The balance never does.

The top of the window always shows the season total and the current (or
last) match.  Below it, every change scrolls past as a live feed.

Season = one Wardogs launch (game process start) until it closes.
Match  = one match, from the first "playing" reads until the game returns
         to the menu.

Usage:
    python cash_tracker.py
    python cash_tracker.py --plain
    python cash_tracker.py --demo
    python cash_tracker.py --probe
    python cash_tracker.py --interval 1.5
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from fps_sampler import FpsSampler
from gold_price import GoldPriceService
from breadcrumbs import BreadcrumbReader
from dashboard import DashboardData, DashboardServer
from history_store import HistoryStore, parse_iso
from names import display_faction, display_map_name
from overlay import Overlay, enable_dpi_awareness
from role_xp import read_role_xp
from seasons import SeasonStore, sanitize_name

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EVENTS_CSV = os.path.join(DATA_DIR, "cash_events.csv")
HISTORY_JSON = os.path.join(DATA_DIR, "history.json")

APP_ID = 1867240
GAME_PROCESS = "WardogsClient-Win64-Shipping.exe"
DEFAULT_INTERVAL = 0.25
CONFIRM_READS = 2
MISS_THRESHOLD = 5
PROCESS_CHECK_EVERY = 5
AUTO_STOP_GRACE = 3.0
CREATE_NO_WINDOW = 0x08000000

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
OLD_EVENTS_HEADER = [
    "time_utc",
    "match_id",
    "event",
    "amount",
    "match_balance",
    "session_balance",
    "faction",
]


def ensure_events_csv(path=EVENTS_CSV):
    """Add the session_id/match_uid columns to an existing event log.

    Old rows are preserved with empty values in the two new columns.  The
    original file is copied to ``.bak`` before it is replaced.
    """
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return False
            if header == EVENTS_HEADER:
                return False
            if header != OLD_EVENTS_HEADER:
                return False
            rows = list(reader)
    except OSError:
        return False

    tmp = path + ".tmp"
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(EVENTS_HEADER)
            for row in rows:
                if len(row) == len(OLD_EVENTS_HEADER):
                    writer.writerow(
                        [
                            row[0],
                            row[1],
                            "",
                            "",
                            row[2],
                            row[3],
                            row[4],
                            row[5],
                            row[6],
                        ]
                    )
                elif len(row) == len(EVENTS_HEADER):
                    writer.writerow(row)
                else:
                    writer.writerow((row + [""] * len(EVENTS_HEADER))[: len(EVENTS_HEADER)])
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def match_curve_from_events(match_uid, started_utc, path=None):
    """Rebuild a match's cash curve from the persisted event log.

    This mirrors the dashboard's match chart so an in-progress match keeps its
    full shape after the tracker is restarted and resumes it.
    """
    if not match_uid or started_utc is None:
        return []
    if path is None:
        path = EVENTS_CSV
    if started_utc.tzinfo is None:
        started_utc = started_utc.replace(tzinfo=timezone.utc)
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = [
                row
                for row in csv.DictReader(handle)
                if (row.get("match_uid") or "").strip() == str(match_uid)
            ]
    except (OSError, ValueError, TypeError):
        return []

    points = [(0.0, 0.0)]
    running = 0
    for row in rows:
        event = (row.get("event") or "").strip()
        if event not in ("spent", "gained"):
            continue
        when = parse_iso(row.get("time_utc"))
        if when is None:
            continue
        balance = row.get("match_balance")
        if balance not in (None, ""):
            try:
                running = int(balance)
            except (TypeError, ValueError):
                pass
        else:
            amount = row.get("amount")
            if amount in (None, ""):
                continue
            try:
                running += int(amount)
            except (TypeError, ValueError):
                continue
        elapsed = max(0.0, (when - started_utc).total_seconds())
        points.append((elapsed, running))

    if len(points) < 2:
        return []
    points.sort(key=lambda point: point[0])
    curve = []
    for elapsed, balance in points:
        if curve and abs(curve[-1][0] - elapsed) < 1e-6:
            curve[-1] = (elapsed, balance)
        else:
            curve.append((elapsed, balance))
    return curve


DLL_CANDIDATES = [
    os.path.join(BASE_DIR, "steam_api64.dll"),
    r"E:\SteamLibrary\steamapps\common\Wardogs\Engine\Binaries\ThirdParty"
    r"\Steamworks\Steamv161\Win64\steam_api64.dll",
]

STEAM_ID_BASE = 76561197960265728


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def signed(value):
    if value > 0:
        return f"+${value:,}"
    if value < 0:
        return f"-${abs(value):,}"
    return "$0"


def cash_per_hour(balance, seconds):
    if balance is None or seconds is None or seconds < 60:
        return None
    return int(round(balance * 3600 / seconds))


def cash_per_hour_str(rate):
    if rate is None:
        return "--"
    return f"{signed(rate)}/h"


def spent_str(value):
    return f"-${value:,}" if value else "$0"


def amount_str(value):
    return f"${value:,}"


def format_duration(delta):
    seconds = int(delta.total_seconds())
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def match_duration(match):
    if not match:
        return "?"
    if match.get("duration"):
        return match["duration"]
    return format_duration(timedelta(seconds=int(match.get("duration_s") or 0)))


def parse_money(raw):
    """'-$5,440' -> -5440, '+$150' -> 150, None/unparseable -> None."""
    if not raw:
        return None
    match = re.match(r"\s*([+-]?)\s*\$?\s*([\d,]+)", raw)
    if not match:
        return None
    sign = -1 if match.group(1) == "-" else 1
    return sign * int(match.group(2).replace(",", ""))


def parse_local(value):
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return parsed.astimezone().replace(tzinfo=None)


def acquire_single_instance():
    """Prevent two trackers from writing the same history file."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\WardogsCashTracker")
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def game_pid():
    """PID of the running Wardogs client, or None."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {GAME_PROCESS}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) >= 2 and row[0].strip().lower() == GAME_PROCESS.lower():
                try:
                    return int(row[1])
                except ValueError:
                    return None
    except Exception:
        pass
    return None


def game_running():
    return game_pid() is not None


def is_process_elevated(pid):
    """True if the process token is elevated, False if not, None if unknown."""
    if not pid or os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        # PROCESS_QUERY_LIMITED_INFORMATION works across integrity levels.
        process = kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return None
        token = wintypes.HANDLE()
        try:
            if not advapi32.OpenProcessToken(
                process, 0x0008, ctypes.byref(token)
            ):  # TOKEN_QUERY
                return None
            elevation = wintypes.DWORD()
            returned = wintypes.DWORD()
            if not advapi32.GetTokenInformation(
                token,
                20,  # TokenElevation
                ctypes.byref(elevation),
                ctypes.sizeof(elevation),
                ctypes.byref(returned),
            ):
                return None
            return bool(elevation.value)
        finally:
            if token:
                kernel32.CloseHandle(token)
            kernel32.CloseHandle(process)
    except Exception:
        return None


def _steam_libraries():
    libraries = []
    steam_path = None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
    except OSError:
        pass

    if steam_path:
        libraries.append(steam_path)
        vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                path = match.group(1).replace("\\\\", "\\")
                if path not in libraries:
                    libraries.append(path)
        except OSError:
            pass
    return libraries


def find_steam_dll():
    candidates = list(DLL_CANDIDATES)
    for library in _steam_libraries():
        steamworks = os.path.join(
            library,
            "steamapps",
            "common",
            "Wardogs",
            "Engine",
            "Binaries",
            "ThirdParty",
            "Steamworks",
        )
        if not os.path.isdir(steamworks):
            continue
        for name in os.listdir(steamworks):
            candidate = os.path.join(steamworks, name, "Win64", "steam_api64.dll")
            if os.path.exists(candidate):
                candidates.append(candidate)

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _steam_user_id(dll):
    """Current user's 64-bit SteamID, via the SteamUser interface if present."""
    for version in ("v023", "v022", "v021", "v020"):
        factory = getattr(dll, f"SteamAPI_SteamUser_{version}", None)
        if not factory:
            continue
        factory.restype = ctypes.c_void_p
        user = factory()
        if not user:
            continue
        dll.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
        dll.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
        steam_id = dll.SteamAPI_ISteamUser_GetSteamID(user)
        if steam_id:
            return steam_id

    # Fallback: the Steam client stores the logged-in account ID in the registry.
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess"
        ) as key:
            account_id = winreg.QueryValueEx(key, "ActiveUser")[0]
        if account_id:
            return STEAM_ID_BASE + int(account_id)
    except OSError:
        pass
    return 0


class RichPresence:
    def __init__(self, dll_path):
        self.dll_path = dll_path
        self.dll = None
        self.friends = None
        self.steam_id = 0

    def init(self):
        # SteamAPI_Init looks for steam_appid.txt in the current directory.
        appid_path = os.path.join(BASE_DIR, "steam_appid.txt")
        with open(appid_path, "w", encoding="ascii") as handle:
            handle.write(str(APP_ID))
        os.chdir(BASE_DIR)

        dll = ctypes.WinDLL(self.dll_path)
        dll.SteamAPI_InitSafe.restype = ctypes.c_bool
        dll.SteamAPI_SteamFriends_v017.restype = ctypes.c_void_p
        dll.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
        dll.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
        dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyCount.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
        ]
        dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyCount.restype = ctypes.c_int
        dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyByIndex.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_int,
        ]
        dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyByIndex.restype = ctypes.c_char_p
        dll.SteamAPI_ISteamFriends_GetFriendRichPresence.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_char_p,
        ]
        dll.SteamAPI_ISteamFriends_GetFriendRichPresence.restype = ctypes.c_char_p
        dll.SteamAPI_ISteamFriends_RequestFriendRichPresence.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
        ]
        dll.SteamAPI_ISteamFriends_GetPersonaName.argtypes = [ctypes.c_void_p]
        dll.SteamAPI_ISteamFriends_GetPersonaName.restype = ctypes.c_char_p
        dll.SteamAPI_RunCallbacks.restype = None
        dll.SteamAPI_Shutdown.restype = None

        if not dll.SteamAPI_InitSafe():
            return False

        self.dll = dll
        self.friends = dll.SteamAPI_SteamFriends_v017()
        self.steam_id = _steam_user_id(dll)
        return True

    def read(self):
        """Current Rich Presence keys, or {} when in a menu / not in a match."""
        dll = self.dll
        dll.SteamAPI_RunCallbacks()
        dll.SteamAPI_ISteamFriends_RequestFriendRichPresence(self.friends, self.steam_id)
        dll.SteamAPI_RunCallbacks()

        count = dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyCount(
            self.friends, self.steam_id
        )
        data = {}
        for index in range(count):
            key = dll.SteamAPI_ISteamFriends_GetFriendRichPresenceKeyByIndex(
                self.friends, self.steam_id, index
            )
            if not key:
                continue
            value = dll.SteamAPI_ISteamFriends_GetFriendRichPresence(
                self.friends, self.steam_id, key
            )
            data[key.decode("utf-8", "replace")] = (
                value.decode("utf-8", "replace") if value else None
            )
        return data

    def persona_name(self):
        if not self.dll or not self.friends:
            return None
        raw = self.dll.SteamAPI_ISteamFriends_GetPersonaName(self.friends)
        return raw.decode("utf-8", "replace") if raw else None

    def shutdown(self):
        if self.dll:
            self.dll.SteamAPI_Shutdown()
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[90m"
RED = "\x1b[91m"
GREEN = "\x1b[92m"
YELLOW = "\x1b[93m"
CYAN = "\x1b[96m"
WHITE = "\x1b[97m"


def elevation_badge(elevated):
    """Colored dot + short label for the console header."""
    if elevated is True:
        return [("● ", RED), ("ADMIN", RED)]
    if elevated is False:
        return [("● ", GREEN), ("OK", GREEN)]
    return [("● ", DIM), ("--", DIM)]


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", ctypes.c_short),
        ("Top", ctypes.c_short),
        ("Right", ctypes.c_short),
        ("Bottom", ctypes.c_short),
    ]


class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", _COORD),
        ("dwCursorPosition", _COORD),
        ("wAttributes", ctypes.c_ushort),
        ("srWindow", _SMALL_RECT),
        ("dwMaximumWindowSize", _COORD),
    ]


def _enable_vt():
    """Turn on ANSI escape processing for the current Windows console."""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def _console_size(default=(100, 30)):
    """Visible console window size (srWindow), not the scrollback buffer."""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetStdHandle.restype = ctypes.c_void_p
            kernel32.GetConsoleScreenBufferInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_CONSOLE_SCREEN_BUFFER_INFO),
            ]
            kernel32.GetConsoleScreenBufferInfo.restype = ctypes.c_bool
            handle = kernel32.GetStdHandle(-11)
            info = _CONSOLE_SCREEN_BUFFER_INFO()
            if kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                width = info.srWindow.Right - info.srWindow.Left + 1
                height = info.srWindow.Bottom - info.srWindow.Top + 1
                if width > 0 and height > 0:
                    return width, height
        except Exception:
            pass
    try:
        size = shutil.get_terminal_size(default)
    except OSError:
        return default
    return size.columns, size.lines


def render_segments(segments, width):
    """Render (text, colour) segments, clipped to width visible characters."""
    out = []
    used = 0
    for text, color in segments:
        if used >= width:
            break
        text = text[: width - used]
        if not text:
            continue
        if color:
            out.append(f"{color}{text}{RESET}")
        else:
            out.append(text)
        used += len(text)
    return "".join(out)


def plain_segments(segments):
    return "".join(text for text, _ in segments)


class ConsoleUI:
    """Frozen header on top, scrolling event feed underneath."""

    HEADER_ROWS = 4

    def __init__(self, plain=False):
        self.plain = plain or not sys.stdout.isatty()
        self.enabled = False
        self.header = []
        self.lines = deque(maxlen=1000)

    def start(self):
        if self.plain:
            return
        if not _enable_vt():
            self.plain = True
            return
        self.enabled = True
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        self.draw()

    def stop(self):
        if not self.enabled:
            return
        sys.stdout.write(f"\x1b[?25h{RESET}\x1b[2J\x1b[H")
        sys.stdout.flush()
        self.enabled = False

    def set_header(self, lines):
        self.header = lines
        if self.enabled:
            self.draw()

    def event(self, segments):
        self.lines.append(list(segments))
        if self.plain:
            print(plain_segments(segments), flush=True)
        elif self.enabled:
            self.draw()

    def status(self, text):
        if self.plain:
            print(f"          {text}", flush=True)

    def draw(self):
        if not self.enabled:
            return
        width, height = _console_size((100, 30))
        width = max(40, width)
        height = max(10, height)
        header_rows = min(len(self.header), self.HEADER_ROWS)
        body_rows = max(1, height - header_rows)
        visible = list(self.lines)[-body_rows:]

        chunks = ["\x1b[H"]
        row = 1
        for segments in self.header[:header_rows]:
            chunks.append(f"\x1b[{row};1H{render_segments(segments, width)}\x1b[K")
            row += 1

        # Clear the whole feed area, then draw the newest lines at the bottom.
        chunks.append(f"\x1b[{row};1H\x1b[J")
        row += max(0, body_rows - len(visible))
        for segments in visible:
            chunks.append(f"\x1b[{row};1H{render_segments(segments, width)}\x1b[K")
            row += 1
        sys.stdout.write("".join(chunks))
        sys.stdout.flush()

    def history_text(self):
        return "\n".join(plain_segments(segments) for segments in self.lines)


class _SeasonRequest:
    def __init__(self, name):
        self.name = name
        self.event = threading.Event()
        self.result = None
        self.error = None


class Tracker:
    def __init__(
        self,
        presence,
        steam_name="?",
        ui=None,
        write_csv=True,
        overlay=None,
        fps_sampler=None,
        history=None,
        fresh=False,
        breadcrumbs=None,
        dashboard=None,
        seasons=None,
        gold=None,
    ):
        self.presence = presence
        self.steam_name = steam_name or "?"
        self.ui = ui if ui is not None else ConsoleUI(plain=True)
        self.write_csv = write_csv
        self.overlay = overlay
        self.fps_sampler = fps_sampler
        self.history = history
        self.fresh = fresh
        self.breadcrumbs = breadcrumbs
        self.dashboard = dashboard
        self.seasons = seasons
        self.gold = gold if gold is not None else GoldPriceService()
        self._season_requests = []
        self._season_lock = threading.Lock()
        self.fps = None
        self.game_pid = None
        self.game_elevated = None


        self.match_active = False
        self.match_id = 0
        self.match_started = None
        self.match_partial = False
        self.match_resumed = False
        self.partial_baseline_pending = False
        self.match_spent = 0
        self.match_gained = 0
        self.match_balance = 0
        self.match_curve = []
        self.match_faction = ""
        self.last_profit = 0
        self.match_started_utc = None
        self.match_map = None
        self.match_map_raw = None
        self.match_lives = None
        self.match_spawns = 0
        self.match_role_xp_start = {}
        self.match_role_xp = {}
        self.match_role_xp_total = {}
        self.match_role_xp_partial = False
        self._last_breadcrumb_poll = 0.0

        self.season_active = False
        self.season_started = None
        self.season_ended = None
        self.session_spent = 0
        self.session_gained = 0
        self.session_balance = 0

        self.last_match = None
        self.last_match_chart = None

        self.playing_reads = 0
        self.miss_reads = 0
        self.saw_menu = False
        self._shutdown_done = False
        self.current_session_id = None
        self.current_match_uid = None
        self._csv_ready = False

    # ------------------------------------------------------------------ log
    def log(self, event, amount, faction=""):
        if not self.write_csv:
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            if not self._csv_ready:
                ensure_events_csv(EVENTS_CSV)
                self._csv_ready = True
            new_file = not os.path.exists(EVENTS_CSV)
            with open(EVENTS_CSV, "a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new_file:
                    writer.writerow(EVENTS_HEADER)
                writer.writerow(
                    [
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        self.match_id,
                        self.current_session_id or "",
                        self.current_match_uid or "",
                        event,
                        "" if amount is None else amount,
                        self.match_balance,
                        self.session_balance,
                        faction,
                    ]
                )
        except OSError:
            pass

    def emit(self, segments):
        self.ui.event(segments)

    # --------------------------------------------------------------- services
    def start_services(self):
        self.gold.start()
        if self.overlay is not None:
            self.overlay.start()
        if self.fps_sampler is not None:
            self.fps_sampler.start()
        if self.dashboard is not None:
            if self.dashboard.start():
                for index, url in enumerate(self.dashboard.urls()):
                    label = "DASHBOARD" if index == 0 else "DASHBOARD LAN"
                    self.emit(
                        [
                            (now_str() + "  ", DIM),
                            (label, GREEN if index == 0 else DIM),
                            (f"   {url}", DIM),
                        ]
                    )
            else:
                self.emit(
                    [
                        (now_str() + "  ", DIM),
                        ("DASHBOARD", YELLOW),
                        (f"   could not start: {self.dashboard.error}", DIM),
                    ]
                )

    def _on_fps(self, fps):
        self.fps = fps
        if self.overlay is not None:
            self.overlay.update(fps=fps)

    def poll_breadcrumbs(self, force=False):
        if self.breadcrumbs is None:
            return
        now = time.monotonic()
        if not force and now - self._last_breadcrumb_poll < 1.0:
            return
        self._last_breadcrumb_poll = now
        try:
            self.breadcrumbs.poll()
        except Exception:
            return
        if self.match_active and not self.match_map:
            current = self.breadcrumbs.current_map()
            if current:
                self.match_map = current
                self.match_map_raw = self.breadcrumbs.map_raw

    @staticmethod
    def role_xp_snapshot():
        try:
            return read_role_xp() or {}
        except Exception:
            return {}

    @staticmethod
    def role_xp_gains(start, end):
        gains = {}
        start = start or {}
        for role, total in (end or {}).items():
            if role not in start:
                continue
            try:
                after = int(total)
                before = int(start.get(role, 0))
            except (TypeError, ValueError):
                continue
            if after > before:
                gains[role] = after - before
        return gains

    def display_last_match(self):
        """Last match, but only while it belongs to the active session."""
        match = self.last_match
        if not match:
            return None
        session = self.history.active_session if self.history is not None else None
        if session is not None and match.get("session_id") != session.get("id"):
            return None
        return match

    def _last_match_chart_payload(self):
        last = self.display_last_match()
        if not last:
            return {"active": False}
        match_id = last.get("id") or last.get("match_uid")
        chart = self.last_match_chart
        if chart and (not match_id or chart.get("match_id") == match_id):
            payload = dict(chart)
            payload["active"] = False
            payload["last"] = True
            return payload
        curve = []
        started = parse_iso(last.get("started_at"))
        if match_id and started is not None:
            curve = match_curve_from_events(match_id, started)
        if not curve:
            curve = [(0.0, float(last.get("balance") or 0))]
        ended = parse_iso(last.get("ended_at"))
        return {
            "active": False,
            "last": True,
            "match_id": match_id,
            "curve": curve,
            "duration_s": last.get("duration_s") or 0,
            "map": display_map_name(last.get("map_raw") or last.get("map")) or "Unknown",
            "faction": display_faction(last.get("faction")),
            "partial": bool(last.get("partial")),
            "net": last.get("balance"),
            "ended_ts": ended.timestamp() if ended else None,
        }

    def update_overlay(self):
        if self.overlay is None:
            return
        last = self.display_last_match()
        if self.match_active:
            values = {
                "label": "MATCH",
                "balance": self.match_balance,
                "spent": self.match_spent,
                "gained": self.match_gained,
                "live": True,
                "partial": self.match_partial,
            }
        elif last:
            match = last
            values = {
                "label": "LAST",
                "balance": match["balance"],
                "spent": match["spent"],
                "gained": match["gained"],
                "live": False,
                "partial": match["partial"],
            }
        else:
            values = {
                "label": "MATCH",
                "balance": None,
                "spent": None,
                "gained": None,
                "live": False,
                "partial": False,
            }
        if self.match_active:
            elapsed = (
                (datetime.now() - self.match_started).total_seconds()
                if self.match_started
                else 0
            )
            rate = cash_per_hour(self.match_balance, elapsed)
        elif last:
            rate = cash_per_hour(
                last["balance"],
                last.get("duration_s") or 0,
            )
        else:
            rate = None
        values["cash_per_hour"] = rate
        values["fps"] = self.fps
        values["elevated"] = self.game_elevated
        gold = self.gold.peek() if self.gold is not None else None
        values["gold"] = gold if gold and gold.get("price") is not None else None
        if self.match_active:
            values["match_chart"] = {
                "active": True,
                "curve": list(self.match_curve),
                "started_ts": (
                    self.match_started.timestamp() if self.match_started else None
                ),
                "map": display_map_name(self.match_map_raw or self.match_map) or "Unknown",
                "faction": display_faction(self.match_faction),
                "partial": self.match_partial,
                "net": self.match_balance,
            }
        else:
            values["match_chart"] = self._last_match_chart_payload()
        self.overlay.update(**values)

    # -------------------------------------------------------------- seasons
    def new_season(self, name):
        """Archive the current files, reset to zero, and keep tracking."""
        if self.seasons is None:
            raise RuntimeError("season archives are not configured")
        # Validate before ending a live match so a bad name changes nothing.
        name = self.seasons.validate_new(name)

        self.save_history()
        was_match = bool(self.match_active)
        if self.match_active:
            self.end_match(interrupted=True)
        if self.season_active:
            self.end_session(interrupted=was_match)

        summary = None
        if self.history is not None:
            summary = dict(self.history.data.get("lifetime") or {})
        descriptor = self.seasons.archive(name, summary=summary)

        if self.history is not None:
            self.history.reset()
        self.seasons.reset_current()
        self._reset_season_state()
        self._csv_ready = False

        self.update_header()
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("NEW SEASON", CYAN),
                (f"   archived as {descriptor['name']}", WHITE),
                (f"   current reset", DIM),
            ]
        )
        if self.game_pid:
            self.start_session(pid=self.game_pid)
        return {"ok": True, "season": descriptor}

    def _reset_season_state(self):
        self.match_active = False
        self.match_id = 0
        self.match_started = None
        self.match_started_utc = None
        self.match_partial = False
        self.match_resumed = False
        self.partial_baseline_pending = False
        self.match_spent = 0
        self.match_gained = 0
        self.match_balance = 0
        self.match_curve = []
        self.match_faction = ""
        self.last_profit = 0
        self.match_map = None
        self.match_map_raw = None
        self.match_lives = None
        self.match_spawns = 0
        self.match_role_xp_start = {}
        self.match_role_xp = {}
        self.match_role_xp_total = {}
        self.match_role_xp_partial = False

        self.season_active = False
        self.season_started = None
        self.season_ended = None
        self.session_spent = 0
        self.session_gained = 0
        self.session_balance = 0

        self.last_match = None
        self.last_match_chart = None
        self.playing_reads = 0
        self.miss_reads = 0
        self.saw_menu = False
        self.current_session_id = None
        self.current_match_uid = None

    def request_new_season(self, name, timeout=10.0):
        """Queue a reset for the tracker loop and wait for its result."""
        if self.seasons is None:
            raise RuntimeError("season archives are not configured")
        name = self.seasons.validate_new(name)
        request = _SeasonRequest(name)
        with self._season_lock:
            self._season_requests.append(request)
        if not request.event.wait(timeout):
            with self._season_lock:
                try:
                    self._season_requests.remove(request)
                except ValueError:
                    pass
            raise TimeoutError("the tracker did not respond in time")
        if request.error is not None:
            raise request.error
        return request.result

    def process_new_season_requests(self):
        while True:
            with self._season_lock:
                if not self._season_requests:
                    return
                request = self._season_requests.pop(0)
            try:
                request.result = self.new_season(request.name)
            except Exception as exc:
                request.error = exc
            finally:
                request.event.set()

    def save_history(self):
        if self.history is None:
            return
        if self.season_active:
            self.history.update_session(
                self.session_spent, self.session_gained, self.session_balance
            )
        if self.match_active:
            self.history.update_match(
                self.match_spent,
                self.match_gained,
                self.match_balance,
                last_profit=self.last_profit,
            )
        self.history.save()

    # -------------------------------------------------------------- session
    def start_session(self, resume=False, pid=None):
        if self.match_active:
            self.end_match()
        if resume and self.history is not None and self.history.active_session:
            session = self.history.active_session
            self.current_session_id = session.get("id")
            self.season_active = True
            self.game_pid = pid
            self.season_started = parse_local(session.get("started_at")) or datetime.now()
            self.season_ended = None
            self.session_spent = int(session.get("spent") or 0)
            self.session_gained = int(session.get("gained") or 0)
            self.session_balance = int(session.get("balance") or 0)
            self.match_id = int(session.get("matches") or 0)
            self.last_match = self.history.last_match
            self.saw_menu = self.history.active_match is None
            self.playing_reads = 0
            self.miss_reads = 0
            if pid:
                self.history.set_pid(pid)
            self.update_header()
            self.emit(
                [
                    (now_str() + "  ", DIM),
                    ("SESSION RESUMED", CYAN),
                    (f"   {signed(self.session_balance)}", WHITE),
                    (f"   spent {amount_str(self.session_spent)}", DIM),
                    (f"   gained {amount_str(self.session_gained)}", DIM),
                ]
            )
            self.log("session_resume", self.session_balance)
            return

        self.season_active = True
        self.game_pid = pid
        self.season_started = datetime.now()
        self.season_ended = None
        self.session_spent = 0
        self.session_gained = 0
        self.session_balance = 0
        self.match_resumed = False
        if self.history is not None:
            self.history.start_session(pid)
            self.current_session_id = (self.history.active_session or {}).get("id")
            self.last_match = self.history.last_match
        else:
            self.last_match = None
        self.last_match_chart = None
        self.saw_menu = False
        self.playing_reads = 0
        self.miss_reads = 0
        self.update_header()
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("SESSION START", CYAN),
                ("   waiting for a match", DIM),
            ]
        )
        self.log("session_start", 0)

    def end_session(self, interrupted=False):
        if self.match_active:
            self.end_match(interrupted=interrupted)
        if not self.season_active:
            return
        self.season_active = False
        self.season_ended = datetime.now()
        if self.history is not None:
            self.history.update_session(
                self.session_spent, self.session_gained, self.session_balance
            )
            self.history.end_session(interrupted=interrupted)
        self.update_header()
        suffix = "   (interrupted)" if interrupted else ""
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("SESSION END", CYAN),
                (f"   {signed(self.session_balance)}", WHITE),
                (f"   spent {amount_str(self.session_spent)}", DIM),
                (f"   gained {amount_str(self.session_gained)}", DIM),
                (suffix, YELLOW),
            ]
        )
        self.log("session_end", self.session_balance)
        self.current_session_id = None

    def resume_match(self):
        if self.history is None or self.history.active_match is None:
            return False
        match = self.history.active_match
        self.current_match_uid = match.get("id")
        self.match_active = True
        self.match_started = parse_local(match.get("started_at")) or datetime.now()
        self.match_started_utc = self.match_started.astimezone(timezone.utc)
        self.match_partial = True
        self.match_resumed = True
        self.partial_baseline_pending = False
        self.match_spent = int(match.get("spent") or 0)
        self.match_gained = int(match.get("gained") or 0)
        self.match_balance = int(match.get("balance") or 0)
        self.match_faction = display_faction(match.get("faction"))
        self.match_map_raw = match.get("map_raw") or match.get("map")
        self.match_map = display_map_name(self.match_map_raw)
        self.match_lives = match.get("lives")
        self.match_spawns = int(match.get("spawns") or 0)
        self.match_role_xp_start = dict(match.get("role_xp_total") or {})
        self.match_role_xp = dict(match.get("role_xp") or {})
        self.match_role_xp_total = dict(match.get("role_xp_total") or {})
        self.match_role_xp_partial = True
        self.last_profit = int(match.get("last_profit") or self.match_balance)
        self.match_curve = match_curve_from_events(
            self.current_match_uid, self.match_started_utc
        ) or [(0.0, self.match_balance)]
        session = self.history.active_session or {}
        self.match_id = int(session.get("matches") or 0)
        self.playing_reads = 0
        self.miss_reads = 0
        gap = self.history.match_gap_seconds()
        self.history.mark_resumed()
        self.update_header()
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("MATCH RESUMED", CYAN),
                (f"   {signed(self.match_balance)}", WHITE),
                (f"   spent {amount_str(self.match_spent)}", DIM),
                (f"   gained {amount_str(self.match_gained)}", DIM),
                (f"   gap {format_duration(timedelta(seconds=int(gap)))}", YELLOW),
            ]
        )
        return True

    # ---------------------------------------------------------------- match
    def start_match(self, faction, profit):
        self.match_active = True
        self.match_id += 1
        self.match_started = datetime.now()
        self.match_partial = not self.saw_menu
        self.partial_baseline_pending = False
        self.match_spent = 0
        self.match_gained = 0
        self.match_balance = 0
        self.match_curve = [(0.0, 0.0)]
        self.last_match_chart = None
        self.match_faction = display_faction(faction)
        self.last_profit = 0
        self.playing_reads = 0
        self.miss_reads = 0
        self.match_resumed = False
        self.match_started_utc = datetime.now(timezone.utc)
        self.match_map = None
        self.match_map_raw = None
        self.poll_breadcrumbs(force=True)
        self.match_map = (
            self.breadcrumbs.current_map()
            if self.breadcrumbs is not None
            else None
        )
        self.match_map_raw = (
            self.breadcrumbs.map_raw if self.breadcrumbs is not None else None
        )
        self.match_lives = None
        self.match_spawns = 0
        self.match_role_xp_start = self.role_xp_snapshot()
        self.match_role_xp = {}
        self.match_role_xp_total = dict(self.match_role_xp_start)
        self.match_role_xp_partial = (
            self.match_partial or not bool(self.match_role_xp_start)
        )
        if self.history is not None:
            self.history.start_match(self.match_faction, partial=self.match_partial)
            self.current_match_uid = (self.history.active_match or {}).get("id")
            self.history.update_match_meta(
                map=self.match_map,
                map_raw=self.match_map_raw,
                role_xp_total=self.match_role_xp_start,
                role_xp_partial=self.match_role_xp_partial,
            )

        suffix = "   (partial)" if self.match_partial else ""
        self.update_header()
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("MATCH START", CYAN),
                (f"   faction={self.match_faction}", DIM),
                (suffix, YELLOW),
            ]
        )
        self.log("match_start", None, self.match_faction)

        if self.match_partial:
            if profit is None:
                self.partial_baseline_pending = True
            else:
                self.last_profit = profit
                self.match_balance = profit
                self.match_curve = [(0.0, profit)]
            self.update_header()
            self.ui.status(
                f"match {signed(self.match_balance)}   "
                "(partial: spent/gained count from attach)"
            )
        elif profit is not None:
            self.last_profit = profit
            if profit:
                self.apply_delta(profit, self.match_faction)
        self.save_history()
        self.update_header()

    def end_match(self, interrupted=False):
        if not self.match_active:
            return
        duration_seconds = (
            int((datetime.now() - self.match_started).total_seconds())
            if self.match_started
            else 0
        )
        duration = (
            format_duration(timedelta(seconds=duration_seconds))
            if self.match_started
            else "?"
        )
        self.poll_breadcrumbs(force=True)
        if self.breadcrumbs is not None and not self.match_map:
            current = self.breadcrumbs.current_map()
            if current:
                self.match_map = current
                self.match_map_raw = self.breadcrumbs.map_raw

        self.match_role_xp_total = self.role_xp_snapshot()
        if self.match_role_xp_start:
            self.match_role_xp = self.role_xp_gains(
                self.match_role_xp_start, self.match_role_xp_total
            )
        else:
            self.match_role_xp = {}
        self.match_role_xp_partial = (
            self.match_partial or not bool(self.match_role_xp_start)
        )

        if self.breadcrumbs is not None:
            spawns = self.breadcrumbs.spawn_events()
            if spawns:
                start = self.match_started_utc or datetime.now(timezone.utc)
                window_start = start - timedelta(seconds=10)
                in_match = [
                    spawn for spawn in spawns if spawn >= window_start
                ]
                if in_match:
                    self.match_lives = len(in_match)
                    self.match_spawns = max(0, len(in_match) - 1)

        self.last_match = {
            "balance": self.match_balance,
            "spent": self.match_spent,
            "gained": self.match_gained,
            "faction": self.match_faction,
            "duration": duration,
            "duration_s": duration_seconds,
            "partial": self.match_partial,
            "resumed": self.match_resumed,
            "session_id": (
                (self.history.active_session or {}).get("id")
                if self.history is not None
                else None
            ),
            "map": self.match_map,
            "map_raw": self.match_map_raw,
            "lives": self.match_lives,
            "spawns": self.match_spawns,
            "role_xp": self.match_role_xp,
            "role_xp_total": self.match_role_xp_total,
            "role_xp_partial": self.match_role_xp_partial,
        }
        if self.history is not None:
            self.history.update_match_meta(
                map=self.match_map,
                map_raw=self.match_map_raw,
                lives=self.match_lives,
                spawns=self.match_spawns,
                role_xp=self.match_role_xp,
                role_xp_total=self.match_role_xp_total,
                role_xp_partial=self.match_role_xp_partial,
            )
            self.history.update_match(
                self.match_spent,
                self.match_gained,
                self.match_balance,
                last_profit=self.last_profit,
            )
            record = self.history.end_match(interrupted=interrupted)
            if record is not None:
                self.last_match["duration_s"] = int(record.get("duration_s") or 0)
                self.last_match["duration"] = format_duration(
                    timedelta(seconds=self.last_match["duration_s"])
                )
                for key in (
                    "map",
                    "map_raw",
                    "lives",
                    "spawns",
                    "role_xp",
                    "role_xp_total",
                    "role_xp_partial",
                ):
                    self.last_match[key] = record.get(key)
        self.last_match.setdefault("id", self.current_match_uid)
        self.last_match.setdefault(
            "started_at",
            self.match_started_utc.isoformat() if self.match_started_utc else None,
        )
        self.last_match.setdefault(
            "ended_at", datetime.now(timezone.utc).isoformat()
        )
        self.last_match_chart = {
            "active": False,
            "last": True,
            "match_id": self.last_match.get("id") or self.current_match_uid,
            "curve": list(self.match_curve),
            "duration_s": self.last_match.get("duration_s") or 0,
            "map": display_map_name(
                self.last_match.get("map_raw") or self.last_match.get("map")
            ) or "Unknown",
            "faction": display_faction(self.last_match.get("faction")),
            "partial": bool(self.last_match.get("partial")),
            "net": self.last_match.get("balance"),
            "ended_ts": datetime.now(timezone.utc).timestamp(),
        }

        self.match_active = False
        self.playing_reads = 0
        self.miss_reads = 0

        flags = []
        if self.match_partial:
            flags.append("partial")
        if interrupted:
            flags.append("interrupted")
        suffix = f"   ({', '.join(flags)})" if flags else ""
        self.update_header()
        self.emit(
            [
                (now_str() + "  ", DIM),
                ("MATCH END", CYAN),
                (f"   {signed(self.last_match['balance'])}", WHITE),
                (f"   spent {amount_str(self.last_match['spent'])}", DIM),
                (f"   gained {amount_str(self.last_match['gained'])}", DIM),
                (f"   ({duration})", DIM),
                (suffix, YELLOW),
            ]
        )
        meta_parts = []
        if self.match_map:
            meta_parts.append(("   " + str(self.match_map), CYAN))
        if self.match_lives:
            meta_parts.append((f"   {int(self.match_lives)} lives", DIM))
        role_gains = [
            (role, xp)
            for role, xp in sorted(self.match_role_xp.items())
            if xp
        ]
        if role_gains:
            meta_parts.append(
                (
                    "   "
                    + ", ".join(f"{role} +{xp}" for role, xp in role_gains),
                    DIM,
                )
            )
        if meta_parts:
            self.emit(
                [(now_str() + "  ", DIM), ("MATCH META", CYAN)] + meta_parts
            )
        self.log("match_end", self.match_balance)
        self.current_match_uid = None

        self.match_spent = 0
        self.match_gained = 0
        self.match_balance = 0
        self.match_curve = []
        self.last_profit = 0
        self.match_partial = False
        self.partial_baseline_pending = False
        self.match_started = None
        self.match_started_utc = None
        self.match_faction = ""
        self.match_map = None
        self.match_map_raw = None
        self.match_lives = None
        self.match_spawns = 0
        self.match_role_xp_start = {}
        self.match_role_xp = {}
        self.match_role_xp_total = {}
        self.match_role_xp_partial = False
        self.update_header()

    def apply_delta(self, delta, faction):
        faction = display_faction(faction)
        if delta < 0:
            self.match_spent += -delta
            self.session_spent += -delta
            kind = "spent"
        elif delta > 0:
            self.match_gained += delta
            self.session_gained += delta
            kind = "gained"
        else:
            return

        self.match_balance += delta
        self.session_balance += delta
        elapsed = (
            (datetime.now() - self.match_started).total_seconds()
            if self.match_started
            else 0.0
        )
        point = (max(0.0, elapsed), self.match_balance)
        if self.match_curve and self.match_curve[-1][0] == point[0]:
            self.match_curve[-1] = point
        else:
            self.match_curve.append(point)
        if len(self.match_curve) > 1200:
            self.match_curve = self.match_curve[::2]
        self.save_history()
        self.update_header()

        color = RED if delta < 0 else GREEN
        self.emit(
            [
                (now_str() + "  ", DIM),
                (f"{signed(delta):>10}", color),
                (f"   {kind}", DIM),
            ]
        )
        self.ui.status(
            f"match {signed(self.match_balance):>9}   "
            f"season {signed(self.session_balance):>9}   "
            f"spent {amount_str(self.match_spent):>8}   "
            f"gained {amount_str(self.match_gained):>8}"
        )
        self.log(kind, delta, faction)

    # --------------------------------------------------------------- header
    def update_header(self):
        self.ui.set_header(self.header_lines())
        self.update_overlay()

    def header_lines(self):
        lines = [
            elevation_badge(self.game_elevated)
            + [
                ("  |  ", DIM),
                ("Wardogs Cash Tracker", BOLD + CYAN),
                ("  |  ", DIM),
                (f"Steam: {self.steam_name}", WHITE),
                ("  |  ", DIM),
                (f"app {APP_ID}", DIM),
            ]
        ]

        if self.season_active and self.season_started:
            season_status = f"since {self.season_started.strftime('%H:%M')}"
        elif self.season_ended:
            season_status = f"ended {self.season_ended.strftime('%H:%M')}"
        else:
            season_status = "waiting for game"

        lines.append(
            [
                ("SEASON    ", DIM),
                (signed(self.session_balance), BOLD + WHITE),
                ("   spent ", DIM),
                (amount_str(self.session_spent), WHITE),
                ("   gained ", DIM),
                (amount_str(self.session_gained), WHITE),
                ("   " + season_status, DIM),
            ]
        )

        last = self.display_last_match()
        map_name = None
        lives = None
        if self.match_active:
            label = "MATCH     "
            balance = self.match_balance
            spent = self.match_spent
            gained = self.match_gained
            faction = self.match_faction
            map_name = self.match_map
            lives = self.match_lives
            elapsed = (
                (datetime.now() - self.match_started).total_seconds()
                if self.match_started
                else 0
            )
            duration = (
                format_duration(datetime.now() - self.match_started)
                if self.match_started
                else "?"
            )
            rate = cash_per_hour(balance, elapsed)
            partial = self.match_partial
        elif last:
            label = "LAST MATCH"
            balance = last["balance"]
            spent = last["spent"]
            gained = last["gained"]
            faction = display_faction(last.get("faction"))
            map_name = display_map_name(last.get("map_raw") or last.get("map"))
            lives = last.get("lives")
            duration = match_duration(last)
            rate = cash_per_hour(
                balance, last.get("duration_s") or 0
            )
            partial = last["partial"]
        else:
            label = "MATCH     "
            balance = None
            spent = gained = 0
            faction = ""
            duration = ""
            rate = None
            partial = False

        if balance is None:
            lines.append([(label, DIM), ("-   waiting for a match", DIM)])
        else:
            color = GREEN if balance > 0 else RED if balance < 0 else WHITE
            if rate is None:
                rate_color = DIM
            elif rate > 0:
                rate_color = GREEN
            elif rate < 0:
                rate_color = RED
            else:
                rate_color = WHITE
            parts = [
                (label, DIM),
                (signed(balance), BOLD + color),
                ("   spent ", DIM),
                (amount_str(spent), WHITE),
                ("   gained ", DIM),
                (amount_str(gained), WHITE),
                ("   " + faction, CYAN),
                ("   " + duration, DIM),
                ("   cash/h ", DIM),
                (cash_per_hour_str(rate), rate_color),
            ]
            if map_name:
                parts.append(("   " + str(map_name), CYAN))
            if lives:
                parts.append((f"   {int(lives)} lives", DIM))
            if partial:
                parts.append(("   (partial)", YELLOW))
            lines.append(parts)

        width, _ = _console_size((100, 30))
        lines.append([("-" * max(20, min(width, 200)), DIM)])
        return lines
    # -------------------------------------------------------------- summary
    def summary_lines(self):
        last = self.display_last_match()
        if self.season_started:
            if self.season_ended:
                status = f"ended {self.season_ended.strftime('%H:%M')}"
            else:
                status = f"since {self.season_started.strftime('%H:%M')}"
        else:
            status = "not started"

        lines = [
            f"SEASON     {signed(self.session_balance):>10}   "
            f"spent {amount_str(self.session_spent):>9}   "
            f"gained {amount_str(self.session_gained):>9}   {status}"
        ]

        if self.match_active:
            extra = ""
            if self.match_map:
                extra += f"  {self.match_map}"
            if self.match_lives:
                extra += f"  {int(self.match_lives)} lives"
            lines.append(
                f"MATCH      {signed(self.match_balance):>10}   "
                f"spent {amount_str(self.match_spent):>9}   "
                f"gained {amount_str(self.match_gained):>9}   "
                f"{self.match_faction}  (live){extra}"
            )
        elif last:
            match = last
            suffix = "  (partial)" if match["partial"] else ""
            extra = ""
            if match.get("map"):
                extra += f"  {match['map']}"
            if match.get("lives"):
                extra += f"  {int(match['lives'])} lives"
            lines.append(
                f"LAST MATCH {signed(match['balance']):>10}   "
                f"spent {amount_str(match['spent']):>9}   "
                f"gained {amount_str(match['gained']):>9}   "
                f"{match['faction']}  ({match_duration(match)}){extra}{suffix}"
            )
        else:
            lines.append("MATCH      no match yet")
        return lines

    # ------------------------------------------------------------- tracking
    def handle(self, data):
        state = (data.get("game_state") or "").strip().lower()
        playing = state == "playing"
        profit = parse_money(data.get("profit_loss"))
        faction = display_faction(data.get("faction"))

        if not self.season_active:
            if playing:
                self.start_session()
            else:
                self.saw_menu = True
                self.playing_reads = 0
                return

        if playing:
            self.miss_reads = 0
            self.playing_reads += 1

            if not self.match_active:
                if self.playing_reads >= CONFIRM_READS:
                    self.start_match(faction, profit)
            elif profit is not None:
                if self.partial_baseline_pending:
                    self.last_profit = profit
                    self.match_balance = profit
                    self.match_curve = match_curve_from_events(
                        self.current_match_uid, self.match_started_utc
                    ) or [(0.0, profit)]
                    self.partial_baseline_pending = False
                    self.save_history()
                    self.update_header()
                    self.ui.status(
                        f"match {signed(self.match_balance)}   (partial baseline)"
                    )
                else:
                    delta = profit - self.last_profit
                    self.last_profit = profit
                    if delta:
                        self.apply_delta(delta, faction)
                    else:
                        self.save_history()
        else:
            self.saw_menu = True
            self.playing_reads = 0
            if self.match_active:
                self.miss_reads += 1
                if self.miss_reads >= MISS_THRESHOLD:
                    self.end_match()

    def run(self, interval, stay=False):
        self.ui.start()
        pid = game_pid()
        self.game_pid = pid
        self.game_elevated = is_process_elevated(pid) if pid else None
        if self.overlay is not None:
            self.overlay.set_game_pid(pid)
        self.start_services()
        if self.breadcrumbs is None:
            try:
                self.breadcrumbs = BreadcrumbReader()
            except Exception:
                self.breadcrumbs = None
        self.poll_breadcrumbs(force=True)
        if self.ui.plain:
            print(
                f"Wardogs Cash Tracker  |  Steam: {self.steam_name}  |  app {APP_ID}",
                flush=True,
            )

        first = self.presence.read()
        state = (first.get("game_state") or "").strip().lower()
        playing = state == "playing"
        active = self.history.active_session if self.history is not None else None
        active_match = self.history.active_match if self.history is not None else None

        if self.fresh and active is not None:
            self.history.end_session(interrupted=True)
            active = None
            active_match = None

        if pid and active is not None and active.get("game_pid") in (None, pid):
            self.start_session(resume=True, pid=pid)
            if active_match is not None:
                gap = self.history.match_gap_seconds()
                same_faction = (first.get("faction") or "?") == active_match.get(
                    "faction"
                )
                if gap <= 300 and (not first or (playing and same_faction)):
                    self.resume_match()
                else:
                    self.history.end_match(interrupted=True)
                    self.last_match = self.history.last_match
                    self.saw_menu = not playing
                    self.emit(
                        [
                            (now_str() + "  ", DIM),
                            ("MATCH CLOSED", YELLOW),
                            ("   not resumed; starting a new match", DIM),
                        ]
                    )
            else:
                self.saw_menu = not playing
        elif pid:
            if active is not None:
                self.history.end_session(interrupted=True)
                self.emit(
                    [
                        (now_str() + "  ", DIM),
                        ("OLD SESSION CLOSED", YELLOW),
                        ("   game restarted", DIM),
                    ]
                )
            self.start_session(pid=pid)
            self.saw_menu = not playing
        else:
            if active is not None:
                self.history.end_session(interrupted=True)
                self.last_match = self.history.last_match
                self.emit(
                    [
                        (now_str() + "  ", DIM),
                        ("SESSION CLOSED", YELLOW),
                        ("   game was not running", DIM),
                    ]
                )
            self.emit(
                [
                    (now_str() + "  ", DIM),
                    ("WAITING", YELLOW),
                    (f"   {GAME_PROCESS} is not running", DIM),
                ]
            )

        missing_since = None if pid else time.monotonic()
        auto_stop_handled = False

        self.handle(first)
        self.update_header()
        tick = 0
        while True:
            self.process_new_season_requests()
            self.poll_breadcrumbs()
            self.handle(self.presence.read())
            self.update_header()

            tick += 1
            if tick % 5 == 0 and self.season_active:
                self.save_history()
            if tick % PROCESS_CHECK_EVERY == 0:
                old_pid = self.game_pid
                pid = game_pid()
                self.game_elevated = is_process_elevated(pid) if pid else None
                if self.overlay is not None:
                    self.overlay.set_game_pid(pid)

                if pid:
                    missing_since = None
                    auto_stop_handled = False
                    if self.season_active and old_pid and pid != old_pid:
                        self.end_session(interrupted=True)
                        self.start_session(pid=pid)
                        self.saw_menu = True
                        self.emit(
                            [
                                (now_str() + "  ", DIM),
                                ("GAME RESTARTED", YELLOW),
                                ("   new season", DIM),
                            ]
                        )
                    elif not self.season_active:
                        self.start_session(pid=pid)
                else:
                    if missing_since is None:
                        missing_since = time.monotonic()
                    if (
                        not auto_stop_handled
                        and time.monotonic() - missing_since >= AUTO_STOP_GRACE
                    ):
                        auto_stop_handled = True
                        if self.season_active:
                            self.end_session(
                                interrupted=bool(self.match_active)
                            )
                        self.emit(
                            [
                                (now_str() + "  ", DIM),
                                ("AUTO-STOP", YELLOW),
                                (f"   {GAME_PROCESS} is not running", DIM),
                            ]
                        )
                        if not stay:
                            return
                self.game_pid = pid

            time.sleep(interval)

    def shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True

        if self.season_active:
            if self.history is not None and game_running():
                self.save_history()
                self.history.pause()
                self.emit(
                    [
                        (now_str() + "  ", DIM),
                        ("PAUSED", YELLOW),
                        ("   will resume next launch", DIM),
                    ]
                )
            else:
                self.end_session()

        if self.fps_sampler is not None:
            self.fps_sampler.stop()
        if self.gold is not None:
            self.gold.stop()
        if self.overlay is not None:
            self.overlay.stop()
        if self.dashboard is not None:
            self.dashboard.stop()

        was_tui = not self.ui.plain
        self.ui.stop()

        print()
        for line in self.summary_lines():
            print(line)
        if was_tui and self.ui.lines:
            print()
            print(self.ui.history_text())
        print("Stopped.")


class DemoPresence:
    def __init__(self, steps):
        self.steps = steps
        self.index = 0

    def persona_name(self):
        return "DEMO"

    def read(self):
        if not self.steps:
            return {}
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        return step


def _playing(profit, faction="alpha"):
    if profit > 0:
        raw = f"+${profit:,}"
    elif profit < 0:
        raw = f"-${abs(profit):,}"
    else:
        raw = "$0"
    return {
        "game_state": "playing",
        "profit_loss": raw,
        "faction": faction,
        "in_profit_or_loss": "profit" if profit >= 0 else "loss",
    }


def run_demo(tracker, interval):
    tracker.start_session()
    steps = []
    steps.extend([{}] * 3)
    steps.extend([_playing(0)] * 2)
    steps.extend(
        [_playing(-575), _playing(-325), _playing(-425), _playing(-275), _playing(242)]
    )
    steps.extend([{}] * 5)
    steps.extend([{}] * 3)
    steps.extend([_playing(0)] * 2)
    steps.extend([_playing(-300), _playing(-50), _playing(400)])
    steps.extend([{}] * 5)
    steps.extend([{}] * 3)

    for step in steps:
        tracker.handle(step)
        tracker.update_header()
        time.sleep(interval)

    while True:
        tracker.handle({})
        tracker.update_header()
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="Live Wardogs cash flow from Steam Rich Presence."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="poll interval in seconds (default 0.25)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="print raw Rich Presence and exit",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="plain scrolling output instead of the full-screen UI",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="show fake cash flow (no Steam, no CSV)",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="do not draw the in-game cash overlay",
    )
    parser.add_argument(
        "--no-fps",
        action="store_true",
        help="do not sample FPS with Windows ETW",
    )
    parser.add_argument(
        "--overlay-x",
        type=int,
        default=7,
        help="overlay left position in pixels (default 7)",
    )
    parser.add_argument(
        "--overlay-y",
        type=int,
        default=7,
        help="overlay top position in pixels (default 7)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="print saved match/season history and exit",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=20,
        help="number of recent matches/seasons to show (default 20)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="ignore a paused season and start a new one",
    )
    parser.add_argument(
        "--new-match",
        action="store_true",
        help="close the saved current match (keeps the season) and exit",
    )
    parser.add_argument(
        "--stay",
        action="store_true",
        help="keep waiting when Wardogs closes instead of auto-stopping",
    )
    parser.add_argument(
        "--dashboard-host",
        default="0.0.0.0",
        help="dashboard bind host (default 0.0.0.0 for LAN access)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8765,
        help="dashboard port (default 8765)",
    )
    parser.add_argument(
        "--dashboard-open",
        action="store_true",
        help="open the dashboard in the default browser",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="do not start the dashboard",
    )
    args = parser.parse_args()

    enable_dpi_awareness()

    history = HistoryStore(HISTORY_JSON)
    if not os.path.exists(HISTORY_JSON):
        history.migrate_csv(EVENTS_CSV)

    if args.history:
        print(history.render_history(args.history_limit))
        return 0

    if args.new_match:
        if history.active_match is not None:
            history.end_match(interrupted=True)
            print("Current match closed. The next launch will start a new partial match.")
        else:
            print("No active match to close.")
        return 0

    if args.demo:
        ui = ConsoleUI(plain=args.plain)
        overlay = (
            None
            if args.no_overlay
            else Overlay(args.overlay_x, args.overlay_y, only_foreground=False)
        )
        tracker = Tracker(
            None, steam_name="DEMO", ui=ui, write_csv=False, overlay=overlay
        )
        try:
            tracker.ui.start()
            tracker.start_services()
            tracker.fps = 128.0
            tracker.update_overlay()
            run_demo(tracker, args.interval)
        except KeyboardInterrupt:
            pass
        finally:
            tracker.shutdown()
        return 0

    dll_path = find_steam_dll()
    if not dll_path:
        print("steam_api64.dll not found. Put it next to this script or install Wardogs.")
        return 1

    presence = RichPresence(dll_path)
    if not presence.init():
        print("SteamAPI_Init failed. Is Steam running and logged in?")
        return 1

    tracker = None
    try:
        if args.probe:
            print(f"Steam: {presence.persona_name()}")
            for _ in range(5):
                data = presence.read()
                print(data if data else "(no Rich Presence - menu or game not running)")
                time.sleep(1)
            return 0

        _mutex = acquire_single_instance()
        if _mutex is None:
            print("Wardogs Cash Tracker is already running.")
            return 1

        ui = ConsoleUI(plain=args.plain)
        overlay = (
            None
            if args.no_overlay
            else Overlay(args.overlay_x, args.overlay_y)
        )
        tracker = Tracker(
            presence,
            steam_name=presence.persona_name() or "?",
            ui=ui,
            overlay=overlay,
            history=history,
            fresh=args.fresh,
            seasons=SeasonStore(DATA_DIR),
        )
        if not args.no_dashboard:
            tracker.dashboard = DashboardServer(
                host=args.dashboard_host,
                port=args.dashboard_port,
                data=DashboardData(HISTORY_JSON, EVENTS_CSV),
                season_store=tracker.seasons,
                on_new_season=tracker.request_new_season,
                auto_open=args.dashboard_open,
                gold=tracker.gold,
            )
        if overlay is not None and not args.no_fps:
            tracker.fps_sampler = FpsSampler(
                GAME_PROCESS, interval=4.0, callback=tracker._on_fps
            )
        tracker.run(args.interval, stay=args.stay)
    except KeyboardInterrupt:
        pass
    finally:
        if tracker:
            tracker.shutdown()
        presence.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
