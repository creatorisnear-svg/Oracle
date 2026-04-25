@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM  TradeSignal AI — Start EVERYTHING (backend + frontend)
REM  Double-click to open both servers in two separate windows.
REM  Each window auto-pulls the latest code and auto-stashes any local edits.
REM ────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo Launching backend (port 8080) in a new window...
start "TradeSignal Backend" cmd /k "%~dp0start-backend.bat"

echo Launching frontend (port 8081) in a new window...
start "TradeSignal Frontend" cmd /k "%~dp0start-frontend.bat"

echo.
echo Both servers are starting. Once you see "Local: http://localhost:8081"
echo in the frontend window, open that URL in your browser.
echo.
echo This window can be closed.
timeout /t 5 >nul
