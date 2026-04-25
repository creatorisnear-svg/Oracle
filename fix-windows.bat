@echo off
REM ────────────────────────────────────────────────────────────────────────────
REM  TradeSignal AI — One-time Windows fix
REM  Pulls the latest code, then re-installs pnpm deps so the Windows-only
REM  binaries (esbuild, rollup, lightningcss, tailwindcss/oxide, ngrok)
REM  get downloaded. Run this once after pulling the pnpm-workspace.yaml fix.
REM ────────────────────────────────────────────────────────────────────────────

cd /d "%~dp0"

echo Working folder: %CD%
echo.

echo [1/3] Pulling latest code...
git pull --rebase --autostash
if errorlevel 1 (
    echo.
    echo *** git pull failed. Resolve the error above, then re-run. ***
    pause
    exit /b 1
)

echo.
echo [2/3] Installing pnpm packages (this pulls the Windows binaries)...
call pnpm install --no-frozen-lockfile
if errorlevel 1 (
    echo.
    echo *** pnpm install failed. See the error above. ***
    pause
    exit /b 1
)

echo.
echo [3/3] Done. You can now double-click start-everything.bat to launch.
echo.
pause
