"""browser_smoke_server.py — 真实浏览器 smoke 服务器入口。

用法（在项目根目录）::

    python tests/browser_smoke_server.py

流程：

1. 在 ``BOARD_APP_SMOKE_RUNTIME_ROOT``（缺省为系统临时目录下的新目录）
   生成 ``data/kline.db`` 与 ``data/search_index.json``（见
   ``browser_smoke_seed``）；
2. 先注入运行环境变量，再 import app，保证 ``core.config`` 指向临时目录；
3. 用 ``create_app(start_runtime=False)`` 创建真实 Flask 并从
   ``FLASK_PORT`` 启动；不启动 QMT、调度器、预热或 WebSocket runtime。

模块本身被 import 时零副作用：不写文件、不 import app、不启动任何服务，
全部入口延迟到函数内部与 ``__main__`` 守卫。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _ensure_project_on_path() -> None:
    """脚本直跑（python tests/browser_smoke_server.py）时保证根目录可导入。"""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def create_smoke_app():
    """构建不启动 runtime 的 Flask 应用（create_app(start_runtime=False)）。"""
    _ensure_project_on_path()
    from app import create_app

    return create_app(start_runtime=False)


def main() -> None:
    _ensure_project_on_path()
    runtime_root = Path(
        os.environ.get("BOARD_APP_SMOKE_RUNTIME_ROOT")
        or tempfile.mkdtemp(prefix="board-app-smoke-")
    )
    data_dir = runtime_root / "data"

    # 必须在任何会读取 core.config 的模块（seed 内部会 import
    # data.sqlite_repo → core.config）之前把运行路径注入 env；
    # seed 返回的 env 随后覆盖为同一组值。
    os.environ.update(
        {
            "BOARD_APP_DATA_DIR": str(data_dir),
            "BOARD_APP_SEARCH_INDEX_PATH": str(data_dir / "search_index.json"),
            "QMT_ENABLED": "0",
            "QMT_AUTO_START": "0",
            "QMT_STARTUP_HISTORY_SYNC": "0",
            "BOARD_APP_STARTUP_PREWARM": "0",
            "BOARD_APP_AUTO_BOOTSTRAP": "0",
        }
    )
    from browser_smoke_seed import seed_smoke_environment

    os.environ.update(seed_smoke_environment(runtime_root))

    flask_app = create_smoke_app()
    from core.config import DEBUG, FLASK_HOST, FLASK_PORT

    print(f"[smoke] runtime root: {runtime_root}")
    print(f"[smoke] Flask: http://{FLASK_HOST}:{FLASK_PORT}  (runtime disabled)")
    flask_app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG,
        use_reloader=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
