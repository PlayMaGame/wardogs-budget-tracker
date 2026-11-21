# WARDOGS Cash Tracker

Tracks your WARDOGS cash, sessions, matches, and the in-game gold-bar market.

## What you need

- Windows 10/11
- Python 3.11 or newer (tick **Add Python to PATH** during install)
- WARDOGS on Steam

## Run it

1. Download this repository (`Code` > `Download ZIP`) or clone it.
2. Open a terminal in the folder and install the one dependency:

   ```bat
   pip install -r requirements.txt
   ```

3. Double-click **`start_cash_tracker.bat`**.
4. Start WARDOGS. The overlay appears in the top-left while the game is focused.

The tracker creates a local `data/` folder for your history. Nothing is uploaded.

## Overlay

Shows balance, spent, gained, cash per hour, FPS, and the current gold-bar price.

**Hold F8** in game to open the current-match cash graph and the 90-day gold chart.
Release F8 to close it. The overlay is click-through and never blocks your mouse.

## Dashboard

Open **http://localhost:8765** while the tracker is running.

To view it from your phone or another PC:

```bat
python dashboard.py --host 0.0.0.0
```

Allow Python through Windows Firewall, then open `http://<your-pc-ip>:8765`.
The dashboard is read-only and has no login, so only use it on a network you trust.

## Gold price

The gold price is public market data from MetaForge, refreshed every five minutes.
The colour and stars are based on a rolling 90-day window:

- ★★★ at or below the 90-day low
- ★★☆ within 1.4x the low
- ★☆☆ within 1.7x the low
- green at or below 2x the low
- amber at or below the 90-day median
- red above the median

## Troubleshooting

- **"Python was not found"** — reinstall Python with *Add Python to PATH* ticked.
- **No overlay** — the overlay only shows while WARDOGS is the focused window.
- **Dashboard unreachable from another device** — allow Python on the Private
  firewall profile and use the PC's LAN IP, not `localhost`.
