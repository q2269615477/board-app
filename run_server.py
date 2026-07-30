#!/usr/bin/env python
"""Flask启动脚本 - 工作目录自动定位到board-app"""
import os
import sys
from pathlib import Path

# 确保工作目录正确
os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import app
from core.config import FLASK_HOST, FLASK_PORT, DEBUG

if __name__ == '__main__':
    print(f"[启动] http://{FLASK_HOST}:{FLASK_PORT}")
    # 启动数据更新调度器（交易日收盘后自动更新）
    try:
        from data_update_manager import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"[警告] 调度器启动失败: {e}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=DEBUG)
