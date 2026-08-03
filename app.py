import os
import sys
import logging
import secrets
import threading
from collections.abc import Mapping
from pathlib import Path

# 确保当前目录在 sys.path 中（支持直接运行 python app.py）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 尽早加载 .env（须在 data_loader / board_api 等模块 import 前）
# 国内行情直连由各市场数据客户端的局部 Session 负责，启动时不再改写全局网络。
try:
    from core.env_bootstrap import load_env_files
    load_env_files()
except Exception:
    pass

from flask import Flask, send_from_directory, Response
from flask_cors import CORS

from services.realtime_websocket import RealtimeWebSocket

# ============================================================
# 配置与基础设施
# ============================================================

from core.config import Config
from core.lifecycle import start_app

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger('app')

# Runtime 是进程级资源（调度器、PID、信号处理和广播线程），只能有一个
# Flask app 持有它。锁同时覆盖启动序列，避免两个 app 并发抢占。
_runtime_lock = threading.Lock()
_runtime_owner = None
_runtime_context = None
_runtime_websocket_started = False


# ============================================================
# 安全头
# ============================================================
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _bootstrap(flask_app=None):
    """显式启动应用 runtime：依赖检查 → 后台服务 → WebSocket。"""
    global _runtime_owner, _runtime_context, _runtime_websocket_started

    flask_app = flask_app or app
    with _runtime_lock:
        if _runtime_owner is not None:
            if _runtime_owner is not flask_app:
                raise RuntimeError(
                    "runtime already owned by another Flask app; "
                    "only one runtime may run per process"
                )

            # 同一个 app 的重复 bootstrap 只在此前 WebSocket 启动失败时重试；
            # 成功后不再次调用 start，避免重复广播线程。
            if not _runtime_websocket_started:
                try:
                    flask_app.extensions['realtime_websocket'].start()
                    _runtime_websocket_started = True
                    logger.info("[BOOTSTRAP] WebSocket 实时推送服务已启动")
                except Exception as e:
                    logger.error(f"[BOOTSTRAP] WebSocket 启动失败: {e}")
            return _runtime_context

        logger.info("=" * 60)
        logger.info("AI炒股面板 v3.0")
        logger.info("=" * 60)
        missing = Config.validate()
        if missing:
            logger.warning(f"依赖检查未通过，缺失文件: {missing}")
        else:
            logger.info("[OK] 依赖检查通过")
        ctx = start_app()

        # 生命周期启动成功后立即保留 owner；若 WebSocket 后续失败，
        # 仍只允许这个 app 重试，不能让另一个 app 造成第二套 runtime。
        _runtime_owner = flask_app
        _runtime_context = ctx
        flask_app.extensions['app_context'] = ctx

        # 启动 WebSocket 实时推送服务
        try:
            flask_app.extensions['realtime_websocket'].start()
            _runtime_websocket_started = True
            logger.info("[BOOTSTRAP] WebSocket 实时推送服务已启动")
        except Exception as e:
            logger.error(f"[BOOTSTRAP] WebSocket 启动失败: {e}")

        return ctx


def favicon():
    """浏览器默认请求；避免控制台 404 噪音。"""
    fav = Config.STATIC_DIR / 'favicon.ico'
    if fav.exists() and fav.stat().st_size > 0:
        return send_from_directory(Config.STATIC_DIR, 'favicon.ico')
    # 空/缺失时返回 204，比 404 更干净
    return Response(status=204)


def index():
    """Serve main panel.

    Phase 0（迁 OpenCode + 真实浏览器）：现役前端 static/js/* 已全部走正常
    fetch('/api/...') 直取分类与 K 线，不再读 window.__init_data__。历史上这里
    为绕过 WorkBuddy 内置浏览器 CSP 而在 </head> 前注入 __init_data__（并同步
    加载分类文件 + sh000001 日线），真实浏览器下前端不消费该数据，故移除注入，
    直接返回模板即可（消除每次请求的分类/DB/QMT 读取开销与首屏延迟）。
    """
    static_dir = Path(__file__).resolve().parent / 'static'
    template = (static_dir / 'index.html').read_text(encoding='utf-8')
    return Response(template, mimetype='text/html')


# ============================================================
# 应用工厂
# ============================================================
def create_app(config=None, start_runtime=False):
    """创建一个独立 Flask 应用实例。

    ``start_runtime`` 明确控制生命周期、调度器、QMT 和 WebSocket 的启动；
    默认只组装 Flask 路由和扩展，不启动任何后台运行时。
    """
    flask_app = Flask(__name__, static_folder=Config.STATIC_DIR)

    # ============================================================
    # 安全配置（Phase 2.1 安全加固）
    # ============================================================
    flask_app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
    # 请求体大小限制（1MB）
    flask_app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

    if config is not None:
        if isinstance(config, Mapping):
            flask_app.config.from_mapping(config)
        else:
            flask_app.config.from_object(config)

    # CORS 仅允许本地来源（5000+/3000开发）
    CORS(flask_app, resources={
        r"/*": {"origins": ["http://127.0.0.1:5000", "http://localhost:5000",
                               "http://127.0.0.1:3000", "http://localhost:3000"]}
    })

    # 每个 Flask app 都拥有自己的 Sock/WebSocket 生命周期对象。
    websocket = RealtimeWebSocket()
    websocket.init_app(flask_app)
    flask_app.extensions['realtime_websocket'] = websocket

    flask_app.after_request(add_security_headers)

    # 注册蓝图路由（分层组织）。蓝图本身不启动 runtime。
    from api import register_routes
    register_routes(flask_app)

    # 注册应用内置路由。
    flask_app.add_url_rule('/favicon.ico', endpoint='favicon', view_func=favicon)
    flask_app.add_url_rule('/', endpoint='index', view_func=index)

    if start_runtime:
        _bootstrap(flask_app)

    return flask_app


# 保留模块级 app 和旧的模块级扩展名，供现有 WSGI/测试导入使用；默认不启动 runtime。
app = create_app()
realtime_websocket = app.extensions['realtime_websocket']
sock = realtime_websocket.sock


if __name__ == '__main__':
    app = create_app(start_runtime=True)
    realtime_websocket = app.extensions['realtime_websocket']
    sock = realtime_websocket.sock
    from core.config import FLASK_HOST, FLASK_PORT, DEBUG
    logger.info(f"面板启动: http://{FLASK_HOST}:{FLASK_PORT}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=DEBUG)
