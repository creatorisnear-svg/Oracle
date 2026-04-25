@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM  TradeSignal AI — Frontend launcher (port 8081)
REM  Double-click to:
REM    1. Kill anything still listening on port 8081
REM    2. Pull the latest code from GitHub
REM    3. Start the React/Vite dashboard
REM
REM  Open http://localhost:8081 in your browser once you see "Local: ..."
REM  Make sure start-backend.bat is also running so the dashboard has data.
REM ────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo [1/3] Killing anything on port 8081...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081') do taskkill /F /PID %%a 2>nul

echo [2/3] Pulling latest code from GitHub...
git pull
if errorlevel 1 (
    echo.
    echo *** git pull failed — fix the error above and re-run. ***
    pause
    exit /b 1
)

echo [3/3] Starting frontend on http://localhost:8081 ...
cd artifacts\mockup-sandbox
set PORT=8081
set BASE_PATH=/
pnpm dev

pause
