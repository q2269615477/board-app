"""
config.py — 集中配置
所有路径、端口、参数在此定义，一处修改全局生效
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
STATIC_DIR = BASE_DIR / 'static'

# QMT 配置（支持环境变量覆盖）
QMT_PYTHON_PATH = os.environ.get(
    'QMT_PYTHON_PATH',
    r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\pythonw.exe'
)
QMT_DIR = os.environ.get(
    'QMT_DIR',
    r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64'
)
QMT_MINI_PATH = os.environ.get(
    'QMT_MINI_PATH',
    r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\XtMiniQmt.exe'
)
# 仅 58600 RPC；58610 MiniQMT 数据口已废弃，勿再加入列表
QMT_PORTS = [58600]
QMT_RPC_PORT = 58600
QMT_ENABLED = os.environ.get('QMT_ENABLED', '1') == '1'
# 默认不自动启动 MiniQMT：完整 QMT 登录后可直接走 58600 取数（历史经验）
# 若需应用托管 MiniQMT，显式设 QMT_AUTO_START=1
QMT_AUTO_START = os.environ.get('QMT_AUTO_START', '0') == '1'
# 本地 K 线缓存目录：优先完整客户端 datadir（非 userdata_mini）
QMT_DATA_DIR = os.environ.get(
    'QMT_DATA_DIR',
    str(Path(QMT_DIR).resolve().parent / 'datadir')
)
# 兼容回退（xtquant 默认常指向此路径）
QMT_MINI_DATA_DIR = os.environ.get(
    'QMT_MINI_DATA_DIR',
    str(Path(QMT_DIR).resolve().parent / 'userdata_mini' / 'datadir')
)

# Flask 配置
FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))
FLASK_HOST = os.environ.get('FLASK_HOST', '127.0.0.1')
DEBUG = os.environ.get('DEBUG', '0') == '1'

# K线同步/HTTP配置
KLINE_SYNC_TIMEOUT = int(os.environ.get('KLINE_SYNC_TIMEOUT', '15'))
KLINE_BG_REFRESH_WORKERS = int(os.environ.get('KLINE_BG_REFRESH_WORKERS', '2'))
QMT_TIMEOUT_MINUTE = int(os.environ.get('QMT_TIMEOUT_MINUTE', '30'))
KLINE_FETCH_TIMEOUT_MS = int(os.environ.get('KLINE_FETCH_TIMEOUT_MS', '15000'))

# 前端加载动画配置
LOADING_MIN_MS = int(os.environ.get('LOADING_MIN_MS', '300'))
LOADING_MAX_MS = int(os.environ.get('LOADING_MAX_MS', '8000'))

# 盘中OHLC缓存TTL
INTRADAY_OHLC_TTL_SEC = float(os.environ.get('INTRADAY_OHLC_TTL_SEC', '15.0'))

# QMT HTTP 客户端配置
QMT_HTTP_BASE_URL = os.environ.get('QMT_HTTP_BASE_URL', 'http://127.0.0.1:18080')
QMT_HTTP_TIMEOUT_SEC = float(os.environ.get('QMT_HTTP_TIMEOUT_SEC', '10.0'))


# 缓存配置
CACHE_DEFAULT_TTL = 300  # 5分钟
CACHE_MAX_ITEMS = 200
CACHE_CLEAN_INTERVAL = 60  # 清理间隔（秒）

# MCP 配置（对外契约统一 /mcp/*）
MCP_ENABLED = os.environ.get('MCP_ENABLED', '1') == '1'
MCP_SSE_ENDPOINT = '/mcp/sse'
MCP_TOOLS_ENDPOINT = '/mcp/tools'
MCP_CALL_ENDPOINT = '/mcp/call'

# 知识库 / Obsidian vault（文件夹即库；默认项目内 TradingVault）
ANNOTATION_VAULT_PATH = Path(
    os.environ.get(
        'ANNOTATION_VAULT_PATH',
        str(BASE_DIR / 'vault' / 'TradingVault'),
    )
)
ANNOTATION_INDEX_DB = DATA_DIR / 'annotation_index.sqlite'
# Obsidian URI 用的库名（须与 Obsidian「库名称」一致，默认同文件夹名）
OBSIDIAN_VAULT_NAME = os.environ.get(
    'OBSIDIAN_VAULT_NAME', ANNOTATION_VAULT_PATH.name
)
# 可选：Obsidian 安装路径（仅用于文档/打开应用，写库不依赖）
OBSIDIAN_APP_PATH = os.environ.get(
    'OBSIDIAN_APP_PATH',
    r'D:\Program Files\Obsidian\Obsidian.exe',
)

# QMT缓存配置
QMT_CACHE_INTERVAL = 3  # 后端缓存刷新间隔（秒）
FRONTEND_REFRESH_INTERVAL = 5  # 前端显示刷新间隔（秒）
QMT_CACHE_MAX_CODES = 500  # 最大缓存标的数

# 数据更新配置
BOARD_CHG_REFRESH_INTERVAL = 60

# 午休缓存配置
NOON_CACHE_DIR = DATA_DIR / 'noon_cache'
NOON_CACHE_FILE_PATTERN = 'noon_cache_{date}.json'  # date format: YYYYMMDD

# 更新调度时间配置（混合模式：固定时间 + 状态检测）
UPDATE_SCHEDULE_TIMES = {
    'morning_prewarm': '09:25',   # 开盘前预热
    'noon_update': '11:35',       # 午休更新
    'afternoon_switch': '13:00',  # 下午开盘切换
    'daily_close': '15:05',       # 盘后更新
}

# WebSocket 推送间隔（秒）
WEBSOCKET_UPDATE_INTERVAL = 3  # 顶部指数导航栏推送间隔

PREWARM_TARGETS = [
    ('sh000001', '上证指数', 'index'),
    ('sz399006', '创业板指', 'index'),
    ('sh000688', '科创50', 'index'),
    ('sh000300', '沪深300', 'index'),
    ('sh000016', '上证50', 'index'),
    ('sh000852', '中证1000', 'index'),
    ('sh000853', '中证2000', 'index'),
    ('sh000985', '中证全指', 'index'),
    ('HSI', '恒生指数', 'hk_index'),
    ('HSTECH', '恒生科技', 'hk_index'),
    ('BK1158', '微盘股', 'concept'),
    ('800000', '东方财富全A', 'index'),
]

# 数据库配置
SQLITE_PATH = DATA_DIR / 'kline.db'

# 板块分类文件
BOARD_CLASSIFICATION_FILE = STATIC_DIR / 'board_classification.json'

# LLM/AI 配置
# 支持多种配置方式（优先级从高到低）：
# 1. WorkBuddy/ManAI8: 设置 OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL
#    示例: OPENAI_BASE_URL=https://api.manai8.xyz/v1 OPENAI_API_KEY=xxx OPENAI_MODEL=gpt-5.5
# 2. 标准OpenAI: 仅设置 OPENAI_API_KEY
# 3. Anthropic: 设置 ANTHROPIC_API_KEY
# 4. Ollama: 自动检测本地 http://localhost:11434
# 5. 模拟模式: 以上都未设置时的回退
LLM_BASE_URL = os.environ.get('OPENAI_BASE_URL', '')
LLM_API_KEY = os.environ.get('OPENAI_API_KEY', '')
LLM_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# 午休缓存配置
NOON_CACHE_DIR = DATA_DIR / 'noon_cache'
NOON_CACHE_FILE_PATTERN = 'noon_cache_{date}.json'  # date format: YYYYMMDD

# 更新调度时间配置（混合模式：固定时间 + 状态检测）
UPDATE_SCHEDULE_TIMES = {
    'morning_prewarm': '09:25',   # 开盘前预热
    'noon_update': '11:35',       # 午休更新
    'afternoon_switch': '13:00',  # 下午开盘切换
    'daily_close': '15:05',       # 盘后更新
}

# WebSocket 推送间隔（秒）
WEBSOCKET_UPDATE_INTERVAL = 3  # 顶部指数导航栏推送间隔

class Config:
    """回退兼容：旧代码可直接 from core.config import Config"""
    BASE_DIR = BASE_DIR
    DATA_DIR = DATA_DIR
    STATIC_DIR = STATIC_DIR
    QMT_PYTHON_PATH = QMT_PYTHON_PATH
    QMT_DIR = QMT_DIR
    QMT_PORTS = QMT_PORTS
    QMT_RPC_PORT = QMT_RPC_PORT
    QMT_ENABLED = QMT_ENABLED
    FLASK_PORT = FLASK_PORT
    FLASK_HOST = FLASK_HOST
    CACHE_DEFAULT_TTL = CACHE_DEFAULT_TTL
    KLINE_SYNC_TIMEOUT = KLINE_SYNC_TIMEOUT
    KLINE_FETCH_TIMEOUT_MS = KLINE_FETCH_TIMEOUT_MS
    LOADING_MIN_MS = LOADING_MIN_MS
    LOADING_MAX_MS = LOADING_MAX_MS
    INTRADAY_OHLC_TTL_SEC = INTRADAY_OHLC_TTL_SEC

    BOARD_CHG_REFRESH_INTERVAL = BOARD_CHG_REFRESH_INTERVAL
    QMT_CACHE_INTERVAL = QMT_CACHE_INTERVAL
    FRONTEND_REFRESH_INTERVAL = FRONTEND_REFRESH_INTERVAL
    PREWARM_TARGETS = PREWARM_TARGETS
    SQLITE_PATH = SQLITE_PATH
    BOARD_CLASSIFICATION_FILE = BOARD_CLASSIFICATION_FILE
    MCP_TOOLS_ENDPOINT = MCP_TOOLS_ENDPOINT
    MCP_SSE_ENDPOINT = MCP_SSE_ENDPOINT
    MCP_CALL_ENDPOINT = MCP_CALL_ENDPOINT
    ANNOTATION_VAULT_PATH = ANNOTATION_VAULT_PATH
    ANNOTATION_INDEX_DB = ANNOTATION_INDEX_DB
    OBSIDIAN_VAULT_NAME = OBSIDIAN_VAULT_NAME
    OBSIDIAN_APP_PATH = OBSIDIAN_APP_PATH

    @staticmethod
    def validate():
        """检查必要文件是否存在"""
        missing = []
        if not (DATA_DIR / '行业板块K线数据').exists():
            missing.append(str(DATA_DIR / '行业板块K线数据'))
        if not (DATA_DIR / '概念板块K线数据').exists():
            missing.append(str(DATA_DIR / '概念板块K线数据'))
        return missing
