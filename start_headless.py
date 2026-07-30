"""静默启动脚本 - 避免click stdout问题"""
import os
import sys
import io

# 禁用Windows控制台流检测
os.environ["FLASK_DEBUG"] = "0"

# 确保stdout/stderr有效
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()

from app import app
from core.config import FLASK_HOST, FLASK_PORT

print(f"[启动] http://{FLASK_HOST}:{FLASK_PORT}")

# 使用werkzeug直接启动，避免click控制台检测
from werkzeug.serving import run_simple
run_simple(FLASK_HOST, FLASK_PORT, app, use_reloader=False, use_debugger=False)
