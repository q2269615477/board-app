@echo off
chcp 65001 >nul
cd /d "D:\.workbuddy\2026-06-27-21-35-52\board-app"

echo.
echo ╔══════════════════════════════════════════╗
echo ║     board-app 全量数据更新               ║
echo ╚══════════════════════════════════════════╝
echo.

:: Tushare token
set TUSHARE_TOKEN=cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590

:: 运行更新脚本
"D:\.workbuddy\2026-06-27-21-35-52\board-app\venv\Scripts\python.exe" "D:\.workbuddy\2026-06-27-21-35-52\board-app\scripts\run_full_update.py" %*

:: 出错也显示结果
if %errorlevel% neq 0 (
    echo.
    echo [错误] 更新脚本异常退出 (code: %errorlevel%)
    echo 请检查日志: data\update_logs\
    echo.
)

echo.
pause
