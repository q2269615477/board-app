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
QMT_PORTS = [58600, 58610]
QMT_ENABLED = os.environ.get('QMT_ENABLED', '1') == '1'
QMT_AUTO_START = os.environ.get('QMT_AUTO_START', '1') == '1'  # 自动启动 MiniQMT

# Flask 配置
FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))
FLASK_HOST = os.environ.get('FLASK_HOST', '127.0.0.1')
DEBUG = os.environ.get('DEBUG', '0') == '1'

# 缓存配置
CACHE_DEFAULT_TTL = 300  # 5分钟
CACHE_MAX_ITEMS = 200
CACHE_CLEAN_INTERVAL = 60  # 清理间隔（秒）

# MCP 配置
MCP_ENABLED = os.environ.get('MCP_ENABLED', '1') == '1'
MCP_SSE_ENDPOINT = '/api/mcp/sse'
MCP_TOOLS_ENDPOINT = '/api/mcp/tools'

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
    QMT_ENABLED = QMT_ENABLED
    FLASK_PORT = FLASK_PORT
    FLASK_HOST = FLASK_HOST
    CACHE_DEFAULT_TTL = CACHE_DEFAULT_TTL
    BOARD_CHG_REFRESH_INTERVAL = BOARD_CHG_REFRESH_INTERVAL
    QMT_CACHE_INTERVAL = QMT_CACHE_INTERVAL
    FRONTEND_REFRESH_INTERVAL = FRONTEND_REFRESH_INTERVAL
    PREWARM_TARGETS = PREWARM_TARGETS
    SQLITE_PATH = SQLITE_PATH
    BOARD_CLASSIFICATION_FILE = BOARD_CLASSIFICATION_FILE

    @staticmethod
    def validate():
        """检查必要文件是否存在"""
        missing = []
        if not (DATA_DIR / '行业板块K线数据').exists():
            missing.append(str(DATA_DIR / '行业板块K线数据'))
        if not (DATA_DIR / '概念板块K线数据').exists():
            missing.append(str(DATA_DIR / '概念板块K线数据'))
        return missing
