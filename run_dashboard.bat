@echo off
rem Opens the Keeper dashboard in your browser (http://127.0.0.1:8766). Close this window or press Ctrl+C to stop.
cd /d "%~dp0"
python keeper_dashboard.py %*
pause
