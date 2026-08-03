#!/usr/bin/env python
"""Flask启动脚本 - 工作目录自动定位到board-app"""
import os
import sys
from pathlib import Path

# 确保工作目录正确
os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load local overrides, then cap native pools before app imports pandas/numpy.
from core.env_bootstrap import load_env_files
from core.runtime_limits import configure_runtime_limits

load_env_files()
configure_runtime_limits()

from app import app, create_app
from core.config import FLASK_HOST, FLASK_PORT, DEBUG

if __name__ == '__main__':
    # 导入 app 只组装 Flask；真正运行时必须显式启动。
    app = create_app(start_runtime=True)
    print(f"[启动] http://{FLASK_HOST}:{FLASK_PORT}")
    # 调度器由 core.lifecycle 唯一启动；关闭 reloader 避免后台任务重复创建。
    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG,
        use_reloader=False,
    )
