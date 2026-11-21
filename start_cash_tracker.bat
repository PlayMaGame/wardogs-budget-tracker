@echo off
cd /d "%~dp0"
title Wardogs Cash Tracker
mode con cols=120 lines=35 >nul 2>nul
where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found in PATH. Install Python 3.11 or run with the full path.
    pause
    exit /b 1
)
python cash_tracker.py --stay
if errorlevel 1 pause
