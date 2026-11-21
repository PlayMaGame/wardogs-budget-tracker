"""WARDOGS gold-bar price and a rolling 90-day buy signal.

The game has no official market API.  MetaForge publishes the public exchange
series used by the community trackers (one point per day, 00:00 UTC)::

    GET https://metaforge.app/api/wardogs/market

This module fetches that series, keeps the last good reading on disk, and
turns the trailing 90 days into a simple signal:

    stars  3 = at/below the 90-day low, 2 = within 1.4x, 1 = within 1.7x
    zone   buy  = within 2x the 90-day low (the game's long-run floor)
           fair = at/below the 90-day median
           wait = above the 90-day median

Every threshold is derived from the window, so nothing is hard-coded and the
ladder rolls forward as cheap days age out.  Network work happens in a
background thread; callers only ever read the cached snapshot.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_PATH = os.path.join(DATA_DIR, "gold_price.json")

MARKET_URL = "https://metaforge.app/api/wardogs/market"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) WardogsCashTracker/1.0"
)
WINDOW_DAYS = 90
REFRESH_SECONDS = 300
RETRY_SECONDS = 60
CACHE_VERSION = 1


def _iso_from_ms(value, fallback=None):
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return fallback


def _date_from_ms(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def compute_snapshot(payload, window_days=WINDOW_DAYS):
    """Turn a MetaForge payload into the dashboard snapshot.

    Raises ``ValueError`` when the payload has no usable points.
    """
    points = []
    for item in payload.get("points") or []:
        if not isinstance(item, dict):
            continue
        try:
            stamp = int(item.get("t"))
            price = int(item.get("price"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            points.append((stamp, price))
    points.sort()
    if not points:
        raise ValueError("market payload has no price points")

    window = points[-int(window_days) :]
    prices = [price for _, price in window]
    low90 = min(prices)
    median90 = int(round(statistics.median(prices)))
    all_prices = [price for _, price in points]
    all_time_low = min(all_prices)
    all_time_high = max(all_prices)
    current = prices[-1]

    star2_at = int(round(low90 * 1.4))
    star1_at = int(round(low90 * 1.7))
    buy_at = int(round(low90 * 2.0))

    if current <= low90:
        stars = 3
    elif current <= star2_at:
        stars = 2
    elif current <= star1_at:
        stars = 1
    else:
        stars = 0

    if current <= buy_at:
        zone = "buy"
    elif current <= median90:
        zone = "fair"
    else:
        zone = "wait"

    cheaper_days = sum(1 for price in prices if price < current)
    percentile = int(round(100 * cheaper_days / len(prices)))
    low_index = prices.index(low90)
    change90 = round((current / prices[0] - 1) * 100, 1) if prices[0] else 0.0

    stats = payload.get("stats") or {}
    changes = stats.get("changes") or {}
    try:
        change7 = round(float(changes.get("7d")) * 100, 1)
    except (TypeError, ValueError):
        change7 = None

    return {
        "ok": True,
        "offline": False,
        "version": CACHE_VERSION,
        "price": current,
        "updated_at": _iso_from_ms(
            payload.get("updatedAt"),
            fallback=datetime.fromtimestamp(
                window[-1][0] / 1000, timezone.utc
            ).isoformat(timespec="seconds"),
        ),
        "window_days": len(window),
        "low90": low90,
        "low90_date": _date_from_ms(window[low_index][0]),
        "median90": median90,
        "star3_at": low90,
        "star2_at": star2_at,
        "star1_at": star1_at,
        "buy_at": buy_at,
        "stars": stars,
        "zone": zone,
        "zone_label": {"buy": "BUY", "fair": "FAIR", "wait": "WAIT"}[zone],
        "percentile": percentile,
        "change90": change90,
        "change7": change7,
        "all_time_low": all_time_low,
        "all_time_high": all_time_high,
        "record": current <= all_time_low,
        "history": [[_date_from_ms(stamp), price] for stamp, price in window],
        "source": "MetaForge",
        "source_url": MARKET_URL,
    }


def _fetch_payload(url=MARKET_URL, timeout=8):
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


class GoldPriceService:
    """Thread-safe cache around :func:`compute_snapshot`.

    ``snapshot()`` may fetch on a cold cache and is meant for HTTP handlers.
    ``peek()`` never blocks and is meant for the 1 Hz overlay; it returns the
    last snapshot and refreshes in the background when the reading is stale.
    """

    def __init__(
        self,
        cache_path=CACHE_PATH,
        url=MARKET_URL,
        refresh_seconds=REFRESH_SECONDS,
        retry_seconds=RETRY_SECONDS,
        timeout=8,
        fetcher=None,
    ):
        self.cache_path = cache_path
        self.url = url
        self.refresh_seconds = max(30, int(refresh_seconds))
        self.retry_seconds = max(10, int(retry_seconds))
        self.timeout = timeout
        self._fetcher = fetcher or (lambda: _fetch_payload(self.url, self.timeout))
        self._lock = threading.Lock()
        self._snapshot = self._read_cache()
        self._fetched_at = time.time() if self._snapshot else 0.0
        self._last_attempt = 0.0
        self._refreshing = False
        self._started = False
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ snapshot
    def peek(self):
        """Return the cached snapshot without waiting for the network."""
        with self._lock:
            snapshot = dict(self._snapshot) if self._snapshot else None
            started = self._started
        if snapshot is not None and started and self._needs_refresh():
            self.refresh_async()
        return self._decorate(snapshot)

    def snapshot(self):
        """Return the snapshot, blocking once if nothing has been read yet."""
        with self._lock:
            snapshot = dict(self._snapshot) if self._snapshot else None
        if snapshot is None:
            self.refresh()
            deadline = time.time() + self.timeout + 2
            while snapshot is None and time.time() < deadline:
                with self._lock:
                    snapshot = dict(self._snapshot) if self._snapshot else None
                if snapshot is None:
                    time.sleep(0.05)
        if snapshot is None:
            snapshot = {
                "ok": False,
                "offline": True,
                "price": None,
                "error": "gold price is not available yet",
            }
        elif self._needs_refresh():
            self.refresh_async()
        return self._decorate(snapshot)

    def _decorate(self, snapshot):
        if snapshot is None:
            return None
        result = dict(snapshot)
        if self._fetched_at:
            result["age_s"] = max(0, int(time.time() - self._fetched_at))
        else:
            result["age_s"] = None
        return result

    def _needs_refresh(self):
        with self._lock:
            if self._refreshing:
                return False
            if self._snapshot is None:
                return True
            wait = (
                self.retry_seconds
                if self._snapshot.get("offline")
                else self.refresh_seconds
            )
            now = time.time()
            return (now - self._fetched_at) >= wait and (
                now - self._last_attempt
            ) >= wait

    # ------------------------------------------------------------- refresh
    def refresh_async(self):
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
            self._last_attempt = time.time()
        threading.Thread(
            target=self._refresh_worker, name="wardogs-gold-once", daemon=True
        ).start()

    def refresh(self):
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
            self._last_attempt = time.time()
        self._refresh_worker()

    def _refresh_worker(self):
        try:
            payload = self._fetcher()
            snapshot = compute_snapshot(payload)
        except Exception as exc:  # offline, HTTP error, bad JSON, short series
            with self._lock:
                base = self._snapshot or {
                    "ok": False,
                    "price": None,
                    "window_days": 0,
                }
                self._snapshot = {
                    **base,
                    "offline": True,
                    "error": str(exc) or exc.__class__.__name__,
                }
                self._refreshing = False
            return
        with self._lock:
            self._snapshot = snapshot
            self._fetched_at = time.time()
            self._refreshing = False
        self._write_cache(snapshot)

    def start(self):
        """Begin periodic background refreshes (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="wardogs-gold", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            if self._stop.wait(self.refresh_seconds):
                return

    # --------------------------------------------------------------- cache
    def _read_cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(snapshot, dict) or not snapshot.get("history"):
            return None
        if not isinstance(snapshot.get("price"), (int, float)):
            return None
        snapshot["offline"] = False
        snapshot.pop("error", None)
        return snapshot

    def _write_cache(self, snapshot):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            temporary = self.cache_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False)
            os.replace(temporary, self.cache_path)
        except OSError:
            pass


_default_service = None
_default_lock = threading.Lock()


def default_service():
    """Shared service used by the dashboard and the overlay."""
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = GoldPriceService()
        return _default_service
