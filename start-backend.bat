@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM  TradeSignal AI — Backend launcher (port 8080)
REM  Double-click to:
REM    1. Kill anything still listening on port 8080
REM    2. Pull the latest code from GitHub  (skipped if called with "nopull")
REM    3. Start the FastAPI server
REM
REM  Your local data files (predictions.db, track_record.json, regime_stats.json)
REM  are gitignored, so `git pull` will NEVER touch them. The model's memory
REM  accumulates forever on this machine.
REM ────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [1/3] Killing anything on port 8080...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080') do taskkill /F /PID %%a 2>nul

if /i "%1"=="nopull" goto skippull

echo [2/3] Pulling latest code from GitHub, auto-stashing any local edits...
git pull --rebase --autostash
if errorlevel 1 (
    echo.
    echo *** git pull failed - likely a merge conflict in your local edits. ***
    echo *** Your changes are safe in `git stash list`. Resolve and re-run.  ***
    pause
    exit /b 1
)
goto runserver

:skippull
echo [2/3] Skipping git pull - parent launcher already pulled.

:runserver
echo [3/3] Starting backend on http://localhost:8080 ...
cd artifacts\api-server
set PORT=8080
npm run dev

pause
