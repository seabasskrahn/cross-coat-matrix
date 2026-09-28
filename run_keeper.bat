@echo off
rem Starts the Keeper worker in the background (no window). Stop it with: Stop-Process -Id <pid>
rem The PID is in logs\keeper_worker.pid and the log in logs\keeper_worker.log.
cd /d "%~dp0"
start "" /b pythonw -m matrix.keeper_worker
echo Keeper worker started. Log: %~dp0logs\keeper_worker.log
