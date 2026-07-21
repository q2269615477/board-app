@echo off
cd /d D:\.workbuddy\2026-06-27-21-35-52\board-app

echo.
echo ================================================
echo      board-app Full Data Update
echo ================================================
echo.

set TUSHARE_TOKEN=cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590

D:\.workbuddy\2026-06-27-21-35-52\board-app\venv\Scripts\python.exe D:\.workbuddy\2026-06-27-21-35-52\board-app\scripts\run_full_update.py %*

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Script failed with code %errorlevel%
    echo Check logs in data\update_logs\
    echo.
)

echo.
pause
