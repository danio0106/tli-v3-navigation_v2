@echo off
setlocal
cd /d "%~dp0"

echo Starting auto-sync watcher for origin/main every 60 seconds...
echo Leave this window open. Press Ctrl+C to stop.
echo.

pwsh -ExecutionPolicy Bypass -File ".\scripts\auto-sync-main.ps1" -IntervalSeconds 60

endlocal
