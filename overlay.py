"""Small click-through Win32 overlay for the Wardogs cash tracker.

This is a separate topmost layered window.  It does not inject into, hook,
or read memory from the game.  It simply paints one compact line of text in
the top-left corner of the screen and lets mouse input pass straight through.
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [
        ("cx", wintypes.LONG),
        ("cy", wintypes.LONG),
    ]


WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_TIMER = 0x0113
WM_NCHITTEST = 0x0084
WM_MOUSEACTIVATE = 0x0021

VK_F8 = 0x77

TIMER_LAYOUT = 1
TIMER_PANEL = 2
PANEL_POLL_MS = 50

PS_SOLID = 0
PS_DASH = 1
PS_NULL = 5

HTTRANSPARENT = -1
MA_NOACTIVATE = 3
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
LWA_ALPHA = 0x00000002
DT_RIGHT = 0x00000002
DT_SINGLELINE = 0x00000020
DT_VCENTER = 0x00000004
DT_NOPREFIX = 0x00000800
TRANSPARENT = 1

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = ctypes.c_void_p(-1)

# Window messages / functions.
user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_ubyte,
    wintypes.DWORD,
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
user32.InvalidateRect.restype = wintypes.BOOL
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetTextExtentPoint32W.argtypes = [
    wintypes.HDC,
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(SIZE),
]
gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL
gdi32.CreateFontW.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPCWSTR,
]
gdi32.CreateFontW.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.CreatePen.restype = wintypes.HANDLE
gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.MoveToEx.restype = wintypes.BOOL
gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.LineTo.restype = wintypes.BOOL
gdi32.Polyline.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.c_int]
gdi32.Polyline.restype = wintypes.BOOL
gdi32.Polygon.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.c_int]
gdi32.Polygon.restype = wintypes.BOOL
gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.restype = wintypes.BOOL
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE]
user32.FillRect.restype = ctypes.c_int
user32.FrameRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE]
user32.FrameRect.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetTextColor.restype = wintypes.DWORD
gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
gdi32.TextOutW.restype = wintypes.BOOL
user32.DrawTextW.argtypes = [
    wintypes.HDC,
    wintypes.LPCWSTR,
    ctypes.c_int,
    ctypes.POINTER(wintypes.RECT),
    wintypes.UINT,
]
user32.DrawTextW.restype = ctypes.c_int


def _rgb(red: int, green: int, blue: int) -> int:
    return red | (green << 8) | (blue << 16)


def enable_dpi_awareness() -> None:
    """Make screen coordinates physical pixels (important at 125%/150%)."""
    set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_context is not None:
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        if set_context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except OSError:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _money(value) -> str:
    if value is None:
        return "--"
    if value < 0:
        return f"-${abs(value):,}"
    if value > 0:
        return f"+${value:,}"
    return "$0"


def _plain_money(value) -> str:
    if value is None:
        return "--"
    return f"${abs(value):,}"


def _short_money(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if abs(number) >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"${round(number / 1_000):,}k"
    return f"${round(number):,}"


def _ordinal(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "--"
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _day_label(day) -> str:
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    try:
        text = str(day)
        return f"{months[int(text[5:7]) - 1]} {int(text[8:10])}"
    except (ValueError, IndexError):
        return str(day or "")


def _signed_short(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    sign = "+" if number > 0 else ("-" if number < 0 else "")
    return sign + _short_money(abs(number))


def _clock(seconds) -> str:
    try:
        total = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        total = 0
    return f"{total // 60}:{total % 60:02d}"


def _month_splits(labels):
    """Return (fraction across the series, month name) at each month change."""
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    splits = []
    count = len(labels)
    if count < 2:
        return splits
    previous = None
    for index, day in enumerate(labels):
        text = str(day)
        key = text[:7]
        if previous is not None and key != previous:
            try:
                name = names[int(text[5:7]) - 1]
            except (ValueError, IndexError):
                name = key
            splits.append((index / (count - 1), name))
        previous = key
    return splits


def _linear_points(samples, left, top, right, bottom, x_max=None):
    """Map (x, y) samples onto a linear chart rect, including zero in the y range."""
    clean = []
    for point in samples:
        try:
            x_value, y_value = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(x_value) and math.isfinite(y_value):
            clean.append((x_value, y_value))
    if not clean:
        return [], 0.0, 0.0
    clean.sort(key=lambda point: point[0])
    xs = [x for x, _ in clean]
    ys = [y for _, y in clean]
    lo = min(min(ys), 0.0)
    hi = max(max(ys), 0.0)
    if hi <= lo:
        hi = lo + 1000.0
        lo -= 1000.0
    pad = (hi - lo) * 0.08
    lo -= pad
    hi += pad
    max_x = float(x_max) if x_max is not None else max(xs)
    max_x = max(max_x, max(xs), 1.0)
    min_x = min(0.0, min(xs))
    span = max(max_x - min_x, 1.0)
    points = []
    for x_value, y_value in clean:
        px = left + ((x_value - min_x) / span) * (right - left)
        py = bottom - ((y_value - lo) / (hi - lo)) * (bottom - top)
        points.append((px, py))
    return points, lo, hi


def _log_points(values, left, top, right, bottom, floor=None, ceiling=None):
    """Map prices onto a log-scaled chart rect, cheapest at the bottom."""
    prices = [float(value) for value in values if value and float(value) > 0]
    if not prices:
        return [], 0.0, 0.0
    lo = min(prices)
    hi = max(prices)
    try:
        if floor is not None and float(floor) > 0:
            lo = min(lo, float(floor))
        if ceiling is not None and float(ceiling) > 0:
            hi = max(hi, float(ceiling))
    except (TypeError, ValueError):
        pass
    lo *= 0.92
    hi *= 1.04
    if hi <= lo:
        hi = lo * 1.1
    count = len(prices)
    span = right - left
    steps = max(count - 1, 1)
    points = []
    for index, price in enumerate(prices):
        x = left + (span * index / steps if count > 1 else span / 2)
        ratio = math.log(price / lo) / math.log(hi / lo)
        ratio = max(0.0, min(1.0, ratio))
        points.append((x, bottom - ratio * (bottom - top)))
    return points, lo, hi


class Overlay(threading.Thread):
    """Topmost, click-through one-line cash strip.

    ``update`` accepts: balance, spent, gained, fps, live, partial, label, gold.
    The window is only shown while the game process is the foreground window
    (unless ``only_foreground=False``, which is useful for the demo).

    Holding F8 while the game is focused expands the strip into a 90-day gold
    chart.  Releasing F8 closes it again.
    """

    MIN_WIDTH = 300
    HEIGHT = 26
    PAD = 7
    PANEL_WIDTH = 560
    PANEL_HEIGHT = 368

    def __init__(
        self,
        x: int = 7,
        y: int = 7,
        only_foreground: bool = True,
    ):
        super().__init__(daemon=True, name="wardogs-overlay")
        self.x = x
        self.y = y
        self.only_foreground = only_foreground
        self._game_pid = None
        self._lock = threading.Lock()
        self._state = {
            "balance": None,
            "spent": None,
            "gained": None,
            "fps": None,
            "live": False,
            "partial": False,
            "label": "MATCH",
            "elevated": None,
            "cash_per_hour": None,
            "gold": None,
        }
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._hwnd = None
        self._visible = False
        self._width = self.MIN_WIDTH
        self._height = self.HEIGHT
        self._panel = False
        self._measure_dc = None
        self._wndproc = None
        self.error: Exception | None = None

    # ------------------------------------------------------------- public
    def update(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    def set_game_pid(self, pid) -> None:
        self._game_pid = pid

    def stop(self) -> None:
        if not self.is_alive():
            return
        self._stop_event.set()
        self._ready.wait(timeout=2.0)
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        self.join(timeout=2.0)

    # ------------------------------------------------------------ internals
    def _foreground_is_game(self) -> bool:
        if not self.only_foreground:
            return True
        if not self._game_pid:
            return False
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._game_pid

    def _show(self, visible: bool) -> None:
        if visible == self._visible or not self._hwnd:
            return
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE if visible else SW_HIDE)
        self._visible = visible

    def _panel_open(self) -> bool:
        return self._panel

    def _f8_down(self) -> bool:
        return bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)

    def _set_panel(self, open_: bool) -> None:
        if open_ == self._panel:
            return
        self._panel = open_
        self._resize_if_needed()
        if self._visible and self._hwnd:
            user32.InvalidateRect(self._hwnd, None, False)

    def _sync_panel(self) -> None:
        self._set_panel(self._foreground_is_game() and self._f8_down())

    def _text(self, hdc, text, x, y, colour, font) -> None:
        gdi32.SelectObject(hdc, font)
        gdi32.SetTextColor(hdc, colour)
        gdi32.TextOutW(hdc, int(x), int(y), text, len(text))

    def _text_width(self, hdc, text, font) -> int:
        gdi32.SelectObject(hdc, font)
        size = SIZE()
        gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(size))
        return size.cx

    def _hline(self, hdc, x1, x2, y, colour, style=PS_DASH, width=1) -> None:
        pen = gdi32.CreatePen(style, width, colour)
        old = gdi32.SelectObject(hdc, pen)
        gdi32.MoveToEx(hdc, int(x1), int(y), None)
        gdi32.LineTo(hdc, int(x2), int(y))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(pen)

    def _vline(self, hdc, x, y1, y2, colour, style=PS_DASH, width=1) -> None:
        pen = gdi32.CreatePen(style, width, colour)
        old = gdi32.SelectObject(hdc, pen)
        gdi32.MoveToEx(hdc, int(x), int(y1), None)
        gdi32.LineTo(hdc, int(x), int(y2))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(pen)

    def _draw_panel(self, hdc, state, width) -> None:
        border = _rgb(70, 70, 76)
        self._hline(hdc, 0, width, self.HEIGHT, border, PS_SOLID)
        self._draw_match_chart(hdc, state, width)
        self._hline(hdc, self.PAD, width - self.PAD, 158, border, PS_SOLID)
        self._draw_gold_chart(hdc, state, width)

    def _draw_match_chart(self, hdc, state, width) -> None:
        dim = _rgb(150, 150, 150)
        text = _rgb(225, 225, 225)
        green = _rgb(95, 235, 130)
        red = _rgb(255, 92, 92)
        chart = state.get("match_chart") or {}
        left, right = 58, width - 12
        chart_top, chart_bottom = 62, 146

        self._text(hdc, "CURRENT MATCH", self.PAD + 3, 32, dim, self._font_label)
        net = chart.get("net")
        net_value = None
        net_colour = text
        if net is not None:
            try:
                net_value = float(net)
            except (TypeError, ValueError):
                net_value = None
            if net_value is not None:
                net_colour = (
                    green if net_value > 0
                    else red if net_value < 0
                    else text
                )
                net_text = _signed_short(net_value)
                self._text(
                    hdc, net_text,
                    width - self.PAD - self._text_width(hdc, net_text, self._font_big),
                    28, net_colour, self._font_big,
                )

        if not chart.get("active"):
            self._text(hdc, "no active match", left, chart_top + 32, dim, self._font_label)
            return

        started = chart.get("started_ts")
        try:
            elapsed = max(0.0, time.time() - float(started)) if started else 0.0
        except (TypeError, ValueError):
            elapsed = 0.0

        meta_parts = []
        if chart.get("map"):
            meta_parts.append(str(chart["map"]))
        if chart.get("faction"):
            meta_parts.append(str(chart["faction"]))
        meta_parts.append(_clock(elapsed))
        if chart.get("partial"):
            meta_parts.append("partial")
        self._text(
            hdc, " - ".join(meta_parts),
            self.PAD + 3, 48, dim, self._font_label,
        )

        samples = []
        for point in chart.get("curve") or []:
            try:
                samples.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError, IndexError):
                continue
        samples.sort(key=lambda point: point[0])
        if net is not None:
            try:
                current = float(net)
            except (TypeError, ValueError):
                current = None
            if current is not None:
                if samples and samples[-1][0] == elapsed:
                    samples[-1] = (elapsed, current)
                elif not samples or samples[-1][0] < elapsed:
                    samples.append((elapsed, current))
                elif samples[-1][1] != current:
                    samples.append((elapsed, current))
        if not samples:
            samples = [(0.0, 0.0), (max(elapsed, 60.0), 0.0)]
        elif len(samples) == 1:
            samples.insert(0, (0.0, samples[0][1]))
        x_max = max(elapsed, samples[-1][0], 60.0)
        if samples[-1][0] < x_max:
            samples.append((x_max, samples[-1][1]))
        points, lo, hi = _linear_points(
            samples, left, chart_top, right, chart_bottom, x_max=x_max,
        )

        zero_y = chart_bottom - ((0.0 - lo) / (hi - lo)) * (
            chart_bottom - chart_top
        )

        if len(points) >= 2:
            if net_value is not None and net_value < 0:
                area_colour = _rgb(46, 20, 20)
            elif net_value is not None and net_value > 0:
                area_colour = _rgb(18, 40, 27)
            else:
                area_colour = _rgb(22, 32, 40)
            area_points = list(points) + [
                (points[-1][0], zero_y),
                (points[0][0], zero_y),
            ]
            area_brush = gdi32.CreateSolidBrush(area_colour)
            null_pen = gdi32.CreatePen(PS_NULL, 0, 0)
            old_brush = gdi32.SelectObject(hdc, area_brush)
            old_area_pen = gdi32.SelectObject(hdc, null_pen)
            packed_area = (wintypes.POINT * len(area_points))(
                *[(int(px), int(py)) for px, py in area_points]
            )
            gdi32.Polygon(hdc, packed_area, len(area_points))
            gdi32.SelectObject(hdc, old_area_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(null_pen)
            gdi32.DeleteObject(area_brush)

        grid_colour = _rgb(38, 44, 52)
        for fraction in (0.25, 0.5, 0.75):
            grid_y = chart_top + fraction * (chart_bottom - chart_top)
            self._hline(hdc, left, right, grid_y, grid_colour, PS_SOLID)

        if chart_top <= zero_y <= chart_bottom:
            self._hline(hdc, left, right, zero_y, dim, PS_DASH)

        self._text(hdc, _signed_short(hi), 5, chart_top - 2, dim, self._font_label)
        self._text(hdc, _signed_short(lo), 5, chart_bottom - 13, dim, self._font_label)
        self._text(hdc, "0:00", left, chart_bottom + 2, dim, self._font_label)
        end_text = _clock(x_max)
        self._text(
            hdc, end_text,
            right - self._text_width(hdc, end_text, self._font_label),
            chart_bottom + 2, dim, self._font_label,
        )

        pen = gdi32.CreatePen(PS_SOLID, 2, net_colour)
        old_pen = gdi32.SelectObject(hdc, pen)
        packed = (wintypes.POINT * len(points))(
            *[(int(px), int(py)) for px, py in points]
        )
        gdi32.Polyline(hdc, packed, len(points))
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)

        last_x, last_y = points[-1]
        brush = gdi32.CreateSolidBrush(net_colour)
        old_brush = gdi32.SelectObject(hdc, brush)
        dot_pen = gdi32.CreatePen(PS_SOLID, 1, net_colour)
        old_dot_pen = gdi32.SelectObject(hdc, dot_pen)
        gdi32.Ellipse(
            hdc, int(last_x) - 3, int(last_y) - 3,
            int(last_x) + 3, int(last_y) + 3,
        )
        gdi32.SelectObject(hdc, old_dot_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(dot_pen)
        gdi32.DeleteObject(brush)

    def _draw_gold_chart(self, hdc, state, width) -> None:
        gold = state.get("gold") or {}
        dim = _rgb(150, 150, 150)
        text = _rgb(225, 225, 225)
        gold_line = _rgb(244, 201, 93)
        green = _rgb(95, 235, 130)
        red = _rgb(255, 92, 92)
        footer_y = self.PANEL_HEIGHT - 22
        self._text(hdc, "GOLD BAR - LAST 90 DAYS", self.PAD + 3, 166, dim, self._font_label)

        price = gold.get("price")
        if price is None:
            self._text(hdc, "--", width - self.PAD - 24, 162, dim, self._font_big)
            self._text(hdc, "no price yet", self.PAD + 3, 188, dim, self._font_label)
            hint = "release F8 to close"
            self._text(
                hdc, hint,
                width - self.PAD - self._text_width(hdc, hint, self._font_label),
                footer_y, dim, self._font_label,
            )
            return

        zone = gold.get("zone") or "fair"
        accent = {"buy": green, "fair": gold_line, "wait": red}.get(zone, text)
        price_text = f"${int(price):,}"
        self._text(
            hdc, price_text,
            width - self.PAD - self._text_width(hdc, price_text, self._font_big),
            162, accent, self._font_big,
        )

        values, labels = [], []
        for entry in gold.get("history") or []:
            try:
                day, value = entry[0], float(entry[1])
            except (TypeError, ValueError, IndexError):
                continue
            if value > 0:
                values.append(value)
                labels.append(day)

        sub = str(gold.get("zone_label") or "").upper()
        if gold.get("percentile") is not None:
            sub += f" - {_ordinal(gold.get('percentile'))} percentile"
        if len(labels) >= 2:
            sub += f" - {_day_label(labels[0])} - {_day_label(labels[-1])}"
        self._text(hdc, sub, self.PAD + 3, 188, accent, self._font_label)
        stars = max(0, min(3, int(gold.get("stars") or 0)))
        if stars:
            star_x = self.PAD + 3 + self._text_width(hdc, sub, self._font_label) + 8
            self._text(hdc, "★" * stars, star_x, 187, gold_line, self._font_symbol)

        left, right = 58, width - 12
        chart_top, chart_bottom = 206, 322
        if len(values) < 2:
            self._text(hdc, "no 90-day history", left, chart_top + 40, dim, self._font_label)
        else:
            points, lo, hi = _log_points(
                values, left, chart_top, right, chart_bottom,
                gold.get("low90"), gold.get("median90"),
            )
            for value, colour in (
                (gold.get("low90"), green),
                (gold.get("buy_at"), green),
                (gold.get("median90"), red),
            ):
                try:
                    line_value = float(value)
                except (TypeError, ValueError):
                    continue
                if line_value <= 0 or hi <= lo:
                    continue
                ratio = math.log(line_value / lo) / math.log(hi / lo)
                if 0 <= ratio <= 1:
                    self._hline(
                        hdc, left, right,
                        chart_bottom - ratio * (chart_bottom - chart_top), colour,
                    )

            month_colour = _rgb(80, 90, 100)
            for fraction, name in _month_splits(labels):
                x = left + fraction * (right - left)
                self._vline(hdc, x, chart_top, chart_bottom, month_colour)
                self._text(hdc, name, x + 2, chart_bottom + 2, dim, self._font_label)

            pen = gdi32.CreatePen(PS_SOLID, 2, gold_line)
            old_pen = gdi32.SelectObject(hdc, pen)
            packed = (wintypes.POINT * len(points))(
                *[(int(px), int(py)) for px, py in points]
            )
            gdi32.Polyline(hdc, packed, len(points))
            gdi32.SelectObject(hdc, old_pen)
            gdi32.DeleteObject(pen)

            last_x, last_y = points[-1]
            brush = gdi32.CreateSolidBrush(gold_line)
            old_brush = gdi32.SelectObject(hdc, brush)
            dot_pen = gdi32.CreatePen(PS_SOLID, 1, gold_line)
            old_dot_pen = gdi32.SelectObject(hdc, dot_pen)
            gdi32.Ellipse(
                hdc, int(last_x) - 3, int(last_y) - 3,
                int(last_x) + 3, int(last_y) + 3,
            )
            gdi32.SelectObject(hdc, old_dot_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(dot_pen)
            gdi32.DeleteObject(brush)

            self._text(hdc, _short_money(hi), 5, chart_top - 2, dim, self._font_label)
            self._text(hdc, _short_money(lo), 5, chart_bottom - 13, dim, self._font_label)

        source = "offline - cached reading" if gold.get("offline") else "MetaForge"
        self._text(hdc, source, self.PAD + 3, footer_y, dim, self._font_label)
        hint = "release F8 to close"
        self._text(
            hdc, hint,
            width - self.PAD - self._text_width(hdc, hint, self._font_label),
            footer_y, dim, self._font_label,
        )

    def _segments(self, state):
        balance = state.get("balance")
        if balance is None:
            match_colour = _rgb(170, 170, 170)
        elif balance > 0:
            match_colour = _rgb(95, 235, 130)
        elif balance < 0:
            match_colour = _rgb(255, 92, 92)
        else:
            match_colour = _rgb(225, 225, 225)

        label = "LAST" if state.get("live") is False and balance is not None else "MATCH"
        if state.get("partial"):
            label += "*"

        label_colour = _rgb(150, 150, 150)
        spent_colour = _rgb(255, 160, 160)
        gained_colour = _rgb(125, 255, 150)
        fps_colour = _rgb(130, 200, 255)
        gap = "  "

        rate = state.get("cash_per_hour")
        if rate is None:
            rate_colour = _rgb(170, 170, 170)
            rate_text = "--"
        elif rate > 0:
            rate_colour = _rgb(95, 235, 130)
            rate_text = f"+${rate:,}/h"
        elif rate < 0:
            rate_colour = _rgb(255, 92, 92)
            rate_text = f"-${abs(rate):,}/h"
        else:
            rate_colour = _rgb(225, 225, 225)
            rate_text = "$0/h"

        gold = state.get("gold") or {}
        gold_price = gold.get("price")
        stars = max(0, min(3, int(gold.get("stars") or 0)))
        star_colour = _rgb(244, 201, 93)
        star_text = "★" * stars
        if gold_price is None:
            gold_colour = _rgb(150, 150, 150)
            gold_text = "--"
        else:
            gold_colour = {
                "buy": _rgb(95, 235, 130),
                "fair": _rgb(244, 201, 93),
                "wait": _rgb(255, 92, 92),
            }.get(gold.get("zone"), _rgb(225, 225, 225))
            gold_text = f"${int(gold_price):,}"

        elevated = state.get("elevated")
        if elevated is True:
            status_colour = _rgb(255, 92, 92)
            status_text = "ADMIN"
        elif elevated is False:
            status_colour = _rgb(95, 235, 130)
            status_text = "OK"
        else:
            status_colour = _rgb(150, 150, 150)
            status_text = "--"

        segments = [
            ("● ", status_colour, "label"),
            (status_text, status_colour, "label"),
            (gap, label_colour, "label"),
            (label, label_colour, "label"),
            (" ", label_colour, "label"),
            (_money(balance), match_colour, "value"),
            (gap, label_colour, "label"),
            ("SPENT", label_colour, "label"),
            (" ", label_colour, "label"),
            (_plain_money(state.get("spent")), spent_colour, "value"),
            (gap, label_colour, "label"),
            ("GAINED", label_colour, "label"),
            (" ", label_colour, "label"),
            (_plain_money(state.get("gained")), gained_colour, "value"),
            (gap, label_colour, "label"),
            ("CASH/H", label_colour, "label"),
            (" ", label_colour, "label"),
            (rate_text, rate_colour, "value"),
            (gap, label_colour, "label"),
            ("FPS", label_colour, "label"),
            (" ", label_colour, "label"),
            (
                "--" if state.get("fps") is None else f"{state['fps']:.0f}",
                fps_colour,
                "value",
            ),
            (gap, label_colour, "label"),
            ("GOLD", label_colour, "label"),
            (" ", label_colour, "label"),
            (gold_text, gold_colour, "value"),
        ]
        if star_text:
            segments.append((" ", label_colour, "label"))
            segments.append((star_text, star_colour, "symbol"))
        return segments

    def _font_for(self, name):
        if name == "symbol":
            return getattr(self, "_font_symbol", None) or self._font_value
        return self._font_label if name == "label" else self._font_value

    def _measure_width(self, state) -> int:
        if not self._measure_dc:
            return self.MIN_WIDTH
        total = 0
        for text, _colour, font_name in self._segments(state):
            gdi32.SelectObject(self._measure_dc, self._font_for(font_name))
            size = SIZE()
            gdi32.GetTextExtentPoint32W(
                self._measure_dc, text, len(text), ctypes.byref(size)
            )
            total += size.cx
        return max(self.MIN_WIDTH, min(total + self.PAD * 2, 900))

    def _resize_if_needed(self) -> None:
        if not self._hwnd:
            return
        with self._lock:
            state = dict(self._state)
        width = self._measure_width(state)
        height = self.HEIGHT
        if self._panel_open():
            width = max(width, self.PANEL_WIDTH)
            height = self.PANEL_HEIGHT
        if width != self._width or height != self._height:
            self._width = width
            self._height = height
            user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                self.x,
                self.y,
                width,
                height,
                SWP_NOACTIVATE,
            )

    def _draw(self, hdc) -> None:
        with self._lock:
            state = dict(self._state)

        width, height = self._width, self._height
        rect = wintypes.RECT(0, 0, width, height)
        user32.FillRect(hdc, ctypes.byref(rect), self._bg_brush)
        user32.FrameRect(hdc, ctypes.byref(rect), self._border_brush)
        gdi32.SetBkMode(hdc, TRANSPARENT)

        x = self.PAD
        for text, colour, font_name in self._segments(state):
            gdi32.SelectObject(hdc, self._font_for(font_name))
            gdi32.SetTextColor(hdc, colour)
            size = SIZE()
            gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(size))
            if x + size.cx > width - self.PAD:
                break
            seg_rect = wintypes.RECT(x, 0, x + size.cx + 2, self.HEIGHT)
            user32.DrawTextW(
                hdc,
                text,
                -1,
                ctypes.byref(seg_rect),
                DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX,
            )
            x += size.cx

        if self._panel_open():
            self._draw_panel(hdc, state, width)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            self._draw(hdc)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_TIMER:
            if wparam == TIMER_PANEL:
                self._sync_panel()
                return 0
            self._resize_if_needed()
            self._show(self._foreground_is_game())
            if self._visible:
                user32.InvalidateRect(hwnd, None, False)
            return 0
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_DESTROY:
            user32.KillTimer(hwnd, TIMER_LAYOUT)
            user32.KillTimer(hwnd, TIMER_PANEL)
            user32.PostQuitMessage(0)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        class_name = f"WardogsCashOverlay_{id(self)}"

        self._wndproc = WNDPROC(self._wnd_proc)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = user32.LoadCursorW(None, 32512)
        wc.hbrBackground = self._bg_brush
        wc.lpszMenuName = None
        wc.lpszClassName = class_name

        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())

        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex_style,
            class_name,
            "Wardogs Cash",
            WS_POPUP,
            self.x,
            self.y,
            self._width,
            self.HEIGHT,
            None,
            None,
            hinst,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

        user32.SetLayeredWindowAttributes(hwnd, 0, 242, LWA_ALPHA)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            self.x,
            self.y,
            self._width,
            self.HEIGHT,
            SWP_NOACTIVATE,
        )
        user32.SetTimer(hwnd, TIMER_LAYOUT, 250, None)
        user32.SetTimer(hwnd, TIMER_PANEL, PANEL_POLL_MS, None)
        self._resize_if_needed()
        self._show(self._foreground_is_game())

    def run(self) -> None:
        try:
            self._bg_brush = gdi32.CreateSolidBrush(_rgb(14, 14, 16))
            self._border_brush = gdi32.CreateSolidBrush(_rgb(70, 70, 76))
            self._font_label = gdi32.CreateFontW(
                -11, 0, 0, 0, 600, 0, 0, 0, 0, 0, 0, 5, 0, "Consolas"
            )
            self._font_value = gdi32.CreateFontW(
                -13, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 5, 0, "Consolas"
            )
            self._font_symbol = gdi32.CreateFontW(
                -13, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 5, 0, "Segoe UI Symbol"
            )
            self._font_big = gdi32.CreateFontW(
                -20, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 5, 0, "Consolas"
            )
            self._measure_dc = gdi32.CreateCompatibleDC(None)
            self._create()
            self._ready.set()

            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0 or result == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # keep the tracker alive if the overlay fails
            self.error = exc
        finally:
            self._ready.set()
            hwnd = self._hwnd
            self._hwnd = None
            if hwnd:
                user32.KillTimer(hwnd, 1)
                user32.DestroyWindow(hwnd)
            dc = self._measure_dc
            self._measure_dc = None
            if dc:
                gdi32.DeleteDC(dc)
            for attr in (
                "_font_label",
                "_font_value",
                "_font_symbol",
                "_font_big",
                "_bg_brush",
                "_border_brush",
            ):
                obj = getattr(self, attr, None)
                if obj:
                    gdi32.DeleteObject(obj)
                    setattr(self, attr, None)
