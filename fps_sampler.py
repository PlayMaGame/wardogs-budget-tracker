"""Measure Wardogs FPS through Windows' built-in DXGI ETW provider.

No injection, no hooks, no game-file changes.  A short ETW trace is started
with logman, converted to CSV with tracerpt, and the successful Present calls
for the game process are counted.  The sampler runs at below-normal priority
and is idle whenever the game is not running.
"""

from __future__ import annotations

import csv
import ctypes
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
THREAD_PRIORITY_BELOW_NORMAL = -1

SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows")
SYSTEM32 = os.path.join(SYSTEM_ROOT, "System32")
LOGMAN = shutil.which("logman") or os.path.join(SYSTEM32, "logman.exe")
TRACEPRT = shutil.which("tracerpt") or os.path.join(SYSTEM32, "tracerpt.exe")


def _quote(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _run(command: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS,
    )


def find_pid(process_name: str):
    """Return the PID of the first process with this exe name, or None."""
    try:
        result = _run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {process_name}",
                "/FO",
                "CSV",
                "/NH",
            ],
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) >= 2 and row[0].strip().lower() == process_name.lower():
            try:
                return int(row[1])
            except ValueError:
                return None
    return None


class FpsSampler(threading.Thread):
    """Background sampler that calls ``callback(fps_or_None)``."""

    def __init__(
        self,
        process_name: str,
        interval: float = 4.0,
        callback=None,
        pause: float = 1.0,
    ):
        super().__init__(daemon=True, name="wardogs-fps")
        self.process_name = process_name
        self.interval = max(1.0, float(interval))
        self.pause = max(0.0, float(pause))
        self.callback = callback
        self.fps: float | None = None
        self.error: str | None = None
        self._stop_event = threading.Event()
        self._tmp = Path(tempfile.gettempdir()) / "wardogs_cash_tracker"
        self._etl = self._tmp / f"fps_{os.getpid()}.etl"
        self._csv = self._tmp / f"fps_{os.getpid()}.csv"
        self._session = f"WardogsFPS_{os.getpid()}"

    # ------------------------------------------------------------- public
    def stop(self) -> None:
        if not self.is_alive():
            return
        self._stop_event.set()
        self.join(timeout=4.0)

    # ------------------------------------------------------------ internals
    def _stop_session(self) -> None:
        try:
            _run([LOGMAN, "stop", self._session, "-ets"], timeout=10.0)
        except (OSError, subprocess.SubprocessError):
            pass

    def _start_session(self) -> bool:
        try:
            result = _run(
                [
                    LOGMAN,
                    "start",
                    self._session,
                    "-p",
                    "Microsoft-Windows-DXGI",
                    "-o",
                    str(self._etl),
                    "-ets",
                ],
                timeout=10.0,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def _count_presents(self, pid: int):
        try:
            result = _run(
                [
                    TRACEPRT,
                    str(self._etl),
                    "-o",
                    str(self._csv),
                    "-of",
                    "CSV",
                    "-y",
                ],
                timeout=30.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None

        times: list[int] = []
        try:
            with open(self._csv, "r", encoding="utf-8", errors="replace", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) < 20:
                        continue
                    if row[0].strip() != "Microsoft-Windows-DXGI":
                        continue
                    if row[2].strip() != "179":  # Present stop event
                        continue
                    try:
                        if int(row[9], 16) != pid:
                            continue
                    except ValueError:
                        continue
                    try:
                        if int(row[19].strip(), 0) != 0:  # m_Ret != 0: not displayed
                            continue
                    except ValueError:
                        continue
                    try:
                        times.append(int(row[16]))  # Clock-Time, 100 ns units
                    except ValueError:
                        continue
        except OSError:
            return None

        if len(times) < 2:
            return None
        span = (times[-1] - times[0]) / 10_000_000.0
        if span <= 0:
            return None
        return (len(times) - 1) / span

    def _set(self, fps, error) -> None:
        self.fps = fps
        self.error = error
        if self.callback is not None:
            try:
                self.callback(fps)
            except Exception:
                pass

    def _cleanup(self) -> None:
        for path in (self._etl, self._csv):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def run(self) -> None:
        try:
            ctypes.windll.kernel32.SetThreadPriority(
                ctypes.windll.kernel32.GetCurrentThread(),
                THREAD_PRIORITY_BELOW_NORMAL,
            )
        except Exception:
            pass

        try:
            self._tmp.mkdir(parents=True, exist_ok=True)
            self._stop_session()  # clear a stale session from a previous run
            while not self._stop_event.is_set():
                pid = find_pid(self.process_name)
                if not pid:
                    self._set(None, None)
                    self._stop_event.wait(1.5)
                    continue

                self._cleanup()
                self._stop_session()
                if not self._start_session():
                    self._set(None, "logman could not start the DXGI trace")
                    self._stop_event.wait(15.0)
                    continue

                stopped = self._stop_event.wait(self.interval)
                self._stop_session()
                if stopped:
                    break

                fps = self._count_presents(pid)
                if fps is None:
                    self._set(None, "no Present events captured")
                else:
                    self._set(fps, None)

                self._stop_event.wait(self.pause)
        finally:
            self._stop_session()
            self._cleanup()
