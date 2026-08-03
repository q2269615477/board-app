"""静默启动脚本 - 避免 click stdout 问题。"""
import io
import os
import sys

from app import app, create_app
from core.config import FLASK_HOST, FLASK_PORT


def main():
    # 禁用 Windows 控制台流检测，并确保 stdout/stderr 有效。
    os.environ["FLASK_DEBUG"] = "0"
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    runtime_app = create_app(start_runtime=True)
    print(f"[启动] http://{FLASK_HOST}:{FLASK_PORT}")

    # 使用 werkzeug 直接启动，避免 click 控制台检测。
    from werkzeug.serving import run_simple
    run_simple(
        FLASK_HOST,
        FLASK_PORT,
        runtime_app,
        use_reloader=False,
        use_debugger=False,
    )


if __name__ == '__main__':
    main()
