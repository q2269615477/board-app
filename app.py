import os
import sys
import json
import logging
import secrets
import time
from pathlib import Path
from datetime import datetime

# 确保当前目录在 sys.path 中（支持直接运行 python app.py）
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 尽早加载 .env（须在 data_loader / board_api 等模块 import 前）
# 并强制国内行情直连（清除 HTTP(S)_PROXY→7688 等，避免 Tushare 超时）
try:
    from core.env_bootstrap import load_env_files, force_direct_network
    load_env_files()
    force_direct_network()
except Exception:
    pass

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from flask_sock import Sock

from services.realtime_websocket import realtime_websocket

# ============================================================
# 配置与基础设施
# ============================================================

from core.config import Config
from core.cache import get_cache
from core.lifecycle import start_app, get_app_context, is_qmt_available

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = Flask(__name__, static_folder=Config.STATIC_DIR)

# 初始化 WebSocket
sock = Sock(app)
realtime_websocket.init_app(app)

# ============================================================
# 安全配置（Phase 2.1 安全加固）
# ============================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
# 请求体大小限制（1MB）
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024

# CORS 仅允许本地来源（5000+/3000开发）
CORS(app, resources={
    r"/*": {"origins": ["http://127.0.0.1:5000", "http://localhost:5000",
                           "http://127.0.0.1:3000", "http://localhost:3000"]}
})

logger = logging.getLogger('app')


# ============================================================
# 安全头
# ============================================================
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Cache-Control'] = 'no-store'
    return response


# ============================================================
# 启动应用
# ============================================================
def _bootstrap():
    """应用启动时执行：依赖检查 → QMT启动 → 预热 → 后台服务"""
    logger.info("=" * 60)
    logger.info("AI炒股面板 v3.0")
    logger.info("=" * 60)
    missing = Config.validate()
    if missing:
        logger.warning(f"依赖检查未通过，缺失文件: {missing}")
    else:
        logger.info("[OK] 依赖检查通过")
    ctx = start_app()
    
    # 启动 WebSocket 实时推送服务
    try:
        realtime_websocket.start()
        logger.info("[BOOTSTRAP] WebSocket 实时推送服务已启动")
    except Exception as e:
        logger.error(f"[BOOTSTRAP] WebSocket 启动失败: {e}")
    
    return ctx


# 模块级 bootstrap：仅当 BOARD_APP_AUTO_BOOTSTRAP != '0' 时执行
if os.environ.get('BOARD_APP_AUTO_BOOTSTRAP', '1') != '0':
    _bootstrap()


# ============================================================
# 注册蓝图路由（分层组织）
# ============================================================
from api import register_routes
register_routes(app)


# ============================================================
# 静态文件
# ============================================================
@app.route('/favicon.ico')
def favicon():
    """浏览器默认请求；避免控制台 404 噪音。"""
    fav = Config.STATIC_DIR / 'favicon.ico'
    if fav.exists() and fav.stat().st_size > 0:
        return send_from_directory(Config.STATIC_DIR, 'favicon.ico')
    # 空/缺失时返回 204，比 404 更干净
    return Response(status=204)


@app.route('/')
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
# 运行入口
# ============================================================
def create_app(auto_bootstrap: bool = True):
    """返回模块级 app 实例。

    注意：当前实现并非真正的 Flask app factory。
    app 对象在模块导入时已创建并注册路由，bootstrap 依赖
    BOARD_APP_AUTO_BOOTSTRAP 环境变量控制。
    auto_bootstrap 参数保留为接口预留，暂无实际效果。
    真正的工厂改造另开任务。
    """
    return app


if __name__ == '__main__':
    from core.lifecycle import get_app_context
    ctx = get_app_context()
    from core.config import FLASK_HOST, FLASK_PORT, DEBUG
    logger.info(f"面板启动: http://{FLASK_HOST}:{FLASK_PORT}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=DEBUG)
