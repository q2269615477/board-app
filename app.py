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
    if os.environ.get('BOARD_APP_AUTO_BOOTSTRAP', '1') == '0':
        logger.info("[BOOTSTRAP] BOARD_APP_AUTO_BOOTSTRAP=0，跳过启动初始化")
        return None

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


_bootstrap()


# ============================================================
# 注册蓝图路由（分层组织）
# ============================================================
from api import register_routes
register_routes(app)


# ============================================================
# 静态文件
# ============================================================
@app.route('/')
def index():
    """Serve main panel with all initial data embedded (WorkBuddy CSP blocks internal fetch)"""
    from pathlib import Path
    import sqlite3
    
    base_dir = Path(__file__).resolve().parent
    static_dir = base_dir / 'static'
    template = (static_dir / 'index.html').read_text(encoding='utf-8')
    
    # Load classification data
    classification_data = []
    for cls_file in ['board_classification_saved.json', 'board_classification.json']:
        fp = static_dir / cls_file
        if fp.exists():
            try:
                classification_data = json.loads(fp.read_text(encoding='utf-8'))
                if classification_data:
                    break
            except FileNotFoundError:
                pass  # 文件不存在是正常情况（首次启动时）
            except Exception as e:
                logger.warning(f"分类文件 {fp.name} 读取失败: {e}")
    
    # Load initial kline data for default stock (sh000001 daily)
    kline_data = []
    try:
        from data.sqlite_repo import get_sqlite_repo
        from services.kline_service import df_to_kline
        db = get_sqlite_repo()
        df = db.read_kline('sh000001', 'daily')
        if df is not None and not df.empty:
            kline_data = df_to_kline(df)
            # 过滤周末数据（避免非交易日导致视觉断层）
            kline_data = [r for r in kline_data
                          if datetime.fromtimestamp(r['timestamp'] / 1000).weekday() < 5]
    except Exception as e:
        logger.warning(f"加载初始K线数据失败: {e}", exc_info=True)
    
    # Also try QMT data for sh000001 daily
    if not kline_data:
        try:
            from data.qmt_client import get_qmt_client
            from services.kline_service import df_to_kline
            qmt = get_qmt_client()
            df = qmt.get_daily_local('000001.SH')
            if df is not None and not df.empty:
                kline_data = df_to_kline(df)
        except Exception as e:
            logger.warning(f"QMT数据回退加载失败: {e}", exc_info=True)
    
    # Build embed script — extract nested 'categories' array if needed
    _cats = classification_data.get('categories', []) if isinstance(classification_data, dict) else classification_data
    init_data = {
        'categories': _cats,
        'defaultKline': kline_data,
        'defaultSymbol': {'ticker': 'sh000001', 'name': '上证指数', 'type': 'index'}
    }
    embed = f'<script>\nwindow.__init_data__ = {json.dumps(init_data, ensure_ascii=False)};\n</script>\n'
    
    # Inject before </head>
    template = template.replace('</head>', embed + '</head>', 1)
    
    return Response(template, mimetype='text/html')


# ============================================================
# 运行入口
# ============================================================
if __name__ == '__main__':
    from core.lifecycle import get_app_context
    ctx = get_app_context()
    from core.config import FLASK_HOST, FLASK_PORT, DEBUG
    logger.info(f"面板启动: http://{FLASK_HOST}:{FLASK_PORT}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=DEBUG)
