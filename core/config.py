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
    r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\python.exe'
)
QMT_DIR = os.environ.get(
    'QMT_DIR',
    r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64'
)
QMT_PORTS = [58600, 58610]
QMT_ENABLED = os.environ.get('QMT_ENABLED', '1') == '1'

# Flask 配置
FLASK_PORT = int(os.environ.get('FLASK_PORT', 5000))
FLASK_HOST = os.environ.get('FLASK_HOST', '127.0.0.1')
DEBUG = os.environ.get('DEBUG', '0') == '1'

# 缓存配置
CACHE_DEFAULT_TTL = 300  # 5分钟
CACHE_MAX_ITEMS = 200
CACHE_CLEAN_INTERVAL = 60  # 清理间隔（秒）

# 数据更新配置
BOARD_CHG_REFRESH_INTERVAL = 60
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
