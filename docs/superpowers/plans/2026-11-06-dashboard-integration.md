# Embedded LAN Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the cash dashboard start and stop with the tracker, and make it reachable from another PC on the LAN.

**Architecture:** Add a small `DashboardServer` wrapper around the existing `ThreadingHTTPServer` in `dashboard.py`. The tracker owns one instance and starts it from `Tracker.start_services()`; `Tracker.shutdown()` stops it. The embedded instance binds to `0.0.0.0` by default and prints both localhost and LAN URLs. The standalone `dashboard.py` keeps working and uses the same wrapper.

**Tech Stack:** Python 3.11 stdlib (`http.server`, `threading`, `socket`), existing `DashboardData`, unittest.

**Spec:** User request `2026-11-06`: "make the dashboard to be a part of the tracker, so when I run it, dash board also starts, and I want to open dashboard on another pc. save this current version, make a plan, then go."

## Global Constraints

- No OCR, no screen capture, no game reads or writes.
- Dashboard is read-only and stays local to the LAN; no cloud service, no CDN.
- Existing `dashboard.py` standalone mode must keep working.
- If the dashboard port is in use, the tracker must continue running.
- Backup of the pre-change version: `backups/20261106-012813-pre-dashboard-integration/`.
- Do not restart the currently running tracker mid-match; the new behavior applies on the next launch.

## File Structure

- Modify `dashboard.py`: `dashboard_urls()`, `DashboardServer`, refactored `main()`.
- Modify `cash_tracker.py`: import `DashboardServer`; `Tracker(dashboard=...)`; start in `start_services()`; stop in `shutdown()`; CLI flags.
- Modify `start_dashboard.bat`: bind `0.0.0.0` for standalone LAN use.
- Create `allow_dashboard_firewall.ps1`: optional one-time Windows Firewall rule helper.
- Test: `test_dashboard_server.py`, extend `test_tracker_meta.py` or add `test_tracker_dashboard.py`.

---

### Task 1: Dashboard server wrapper

**Files:**
- Modify: `dashboard.py`
- Test: `test_dashboard_server.py`

**Interfaces:**
- `dashboard_urls(host: str, port: int) -> list[str]`
- `DashboardServer(host="127.0.0.1", port=8765, data=None, index_path=INDEX_HTML, auto_open=False)`
  - `start() -> bool`
  - `stop() -> None`
  - `urls() -> list[str]`
  - `local_url` property
  - `error` attribute (exception or None)
  - `port` attribute (actual bound port)

- [x] **Step 1: Write failing tests**
  - `dashboard_urls("0.0.0.0", 8765)` includes `http://127.0.0.1:8765/` and the LAN URL when `_lan_ip()` is patched.
  - `dashboard_urls("127.0.0.1", 8765)` returns only localhost.
  - `DashboardServer(host="127.0.0.1", port=0)` starts, serves `/api/state`, and stops.
  - Starting twice is safe.
  - A port conflict returns `False` and sets `error`.

- [x] **Step 2: Run tests to verify RED**

Run: `python -m unittest -v test_dashboard_server.py`
Expected: import error / missing symbols.

- [x] **Step 3: Implement `dashboard_urls()` and `DashboardServer`**

Use `socket` for `_lan_ip()`; bind with `ThreadingHTTPServer`; run `serve_forever` in a daemon thread; `stop()` calls `shutdown()` then `server_close()` and joins briefly.

- [x] **Step 4: Run tests to verify GREEN**

Run: `python -m unittest -v test_dashboard_server.py`
Expected: all pass.

---

### Task 2: Refactor standalone dashboard main

**Files:**
- Modify: `dashboard.py`

- [x] **Step 1: Replace the inline server in `main()` with `DashboardServer`**
  - Keep `--host` default `127.0.0.1` in standalone mode.
  - Keep `--port`, `--history`, `--events`, `--no-open`.
  - Print every URL from `server.urls()`.
  - Print a firewall hint when bound to `0.0.0.0`.
  - On `KeyboardInterrupt`, call `server.stop()`.

- [x] **Step 2: Verify standalone**

Run: `python dashboard.py --host 127.0.0.1 --port 8877 --no-open`
Expected: prints `http://127.0.0.1:8877/`; `Invoke-WebRequest http://127.0.0.1:8877/api/state` returns 200.

---

### Task 3: Embed dashboard in the tracker

**Files:**
- Modify: `cash_tracker.py`
- Test: `test_tracker_dashboard.py`

**Interfaces:**
- `Tracker(..., dashboard=None)`
- `Tracker.dashboard`
- `Tracker.start_services()` starts the dashboard once and emits its URLs.
- `Tracker.shutdown()` stops the dashboard.

- [x] **Step 1: Write failing tests**
  - Fake dashboard with `start()`, `stop()`, `urls()`, `error`.
  - `tracker.start_services()` calls `start()` once.
  - `tracker.shutdown()` calls `stop()` once.
  - A dashboard whose `start()` returns `False` does not crash; the tracker still shuts down.

- [x] **Step 2: Run tests to verify RED**

Run: `python -m unittest -v test_tracker_dashboard.py`
Expected: `Tracker.__init__` rejects `dashboard` or no calls recorded.

- [x] **Step 3: Implement**
  - Add `dashboard=None` to `Tracker.__init__`; store it.
  - In `start_services()`: start once, emit `DASHBOARD` plus URLs, or `DASHBOARD OFF` plus the error.
  - In `shutdown()`: stop it before the UI stops.
  - Import `DashboardServer` from `dashboard`.

- [x] **Step 4: Add CLI flags in `main()`**
  - `--dashboard-host` default `0.0.0.0`
  - `--dashboard-port` default `8765`
  - `--dashboard-open` (default off)
  - `--no-dashboard`
  - Create `DashboardServer` only for the real tracker path; pass it to `Tracker`.
  - Do not start a dashboard for `--demo`, `--probe`, `--history`, or `--new-match`.

- [x] **Step 5: Run tests to verify GREEN**

Run: `python -m unittest -v test_tracker_dashboard.py`
Expected: all pass.

---

### Task 4: LAN launcher and firewall helper

**Files:**
- Modify: `start_dashboard.bat`
- Create: `allow_dashboard_firewall.ps1`

- [x] **Step 1: Update `start_dashboard.bat`**
  - Run `python dashboard.py --host 0.0.0.0`.
  - Keep `if errorlevel 1 pause`.

- [x] **Step 2: Add `allow_dashboard_firewall.ps1`**
  - Creates an inbound TCP rule for port 8765, profile `Private`, if missing.
  - Prints the rule name and reminds the user to run it as Administrator once.

- [x] **Step 3: Verify**
  - Run the dashboard standalone on `0.0.0.0:8877`.
  - Open `http://127.0.0.1:8877/` locally.
  - Confirm `dashboard_urls()` includes the machine's LAN IP.

---

### Task 5: Full verification

- [x] Run `python -m unittest discover -q`; expect the full suite green.
- [x] Run `python -m py_compile cash_tracker.py dashboard.py test_dashboard_server.py test_tracker_dashboard.py`.
- [x] Start `python dashboard.py --host 0.0.0.0 --port 8877 --no-open`; request `/api/state`, `/api/matches`, `/api/insights`, and one `/api/match/<uid>`; expect 200.
- [x] Confirm the tracker with `--no-dashboard` still starts (unit-level or short real run).
- [x] Do not restart the live tracker mid-match. Note that the embedded dashboard starts on the next launch.

## Review Focus

- Port already in use: tracker must keep running and print a clear message.
- Firewall: binding `0.0.0.0` is not enough if Windows blocks Python; the helper and printed hint must be clear.
- Security: the dashboard has no auth; keep the default to the LAN and document it.
- Shutdown: Ctrl+C and auto-stop must close the HTTP server cleanly.
- Data consistency: the dashboard reads `history.json` while the tracker writes it atomically; a read must never crash the server.
