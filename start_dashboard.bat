@echo off
cd /d "%~dp0"
title Wardogs Cash Dashboard
python dashboard.py --host 0.0.0.0
if errorlevel 1 pause
