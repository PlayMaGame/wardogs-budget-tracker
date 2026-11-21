# WARDOGS Cash Tracker

A Windows cash tracker for **WARDOGS** (Steam app ID `1867240`) with a click-through in-game overlay and a LAN dashboard.

It reads the game's local cash values, keeps per-session and per-match history, and shows live earnings, match profit, cash per hour, FPS, and the current gold-bar price.

## Features

- In-game overlay: session/match label, balance, spent, gained, cash/hour, FPS, and gold price.
- Gold price: 90-day rolling low, median, star rating, and buy/fair/wait zone. Hover the dashboard card for a 90-day chart; hold **F8** in game for the match and gold charts.
- Dashboard: live stats, session graphs, match list, insights, season management, and CSV export.
- Gold data comes from the public MetaForge market endpoint and falls back to the last cached reading when offline.

## Requirements

- Windows 10/11
- Python 3.11+
- WARDOGS installed through Steam
- `pip install -r requirements.txt`

## Run

Start the tracker and overlay:

```bat
start_cash_tracker.bat
```

or:

```bat
python cash_tracker.py --stay
```

Start only the dashboard:

```bat
start_dashboard.bat
```

or:

```bat
python dashboard.py --host 0.0.0.0 --port 8756
```

The dashboard is read-only and has no login. It is intended for a trusted local network only; do not expose it to the internet.

## Tests

```bat
python -m unittest discover -p "test_*.py"
```

## Notes

- The overlay is click-through and only appears while WARDOGS is the foreground window.
- F8 is polled, not captured, so the game still receives the key.
- Match and cash history stays in `data/` and is not committed.
