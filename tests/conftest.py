"""共享测试夹具"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from core.env_bootstrap import load_env_files
    load_env_files()
except Exception:
    pass

# 设置测试环境变量（须在 import app 之前）
os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)
