"""
services/update_status_store.py — 数据更新状态存储

职责：
 - 管理 update_status.json 的读写
 - 线程安全的 CRUD
 - 今日更新状态标记与查询

从 data_update_manager.py 中抽取，保持行为不变。
data_update_manager.py 通过本模块的函数调用保持兼容。
"""
import json
import threading
import logging
import os
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger('data_update')

# 默认状态文件路径（可被调用方覆盖）
DEFAULT_STATUS_FILE = Path('data') / 'update_status.json'

# 线程锁：保护状态文件读写
_lock = threading.RLock()


def _default_status() -> dict:
    """返回空白状态模板。"""
    return {
        'boards': {},
        'indices': {},
        'stocks': {},
        'today': '',
        'qmt_daily_done': '',
        'scheduler': {'last_run': '', 'next_run': '', 'status': 'idle'},
    }


def _status_path(status_file: Optional[Path] = None) -> Path:
    return Path(status_file) if status_file else DEFAULT_STATUS_FILE


def _load_status_unlocked(sf: Path) -> dict:
    """Load and normalize status while the caller holds ``_lock``."""
    if sf.exists():
        try:
            with open(sf, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'indices' not in data:
                    data['indices'] = {}
                if 'boards' not in data:
                    data['boards'] = {}
                if 'stocks' not in data:
                    data['stocks'] = {}
                return data
        except Exception:
            pass
    return _default_status()


def load_status(status_file: Optional[Path] = None) -> dict:
    """加载状态文件，返回带默认字段的 dict。

    Args:
        status_file: 状态文件路径，为 None 时使用 DEFAULT_STATUS_FILE。

    Returns:
        dict with keys: boards, indices, stocks, today, qmt_daily_done, scheduler
    """
    sf = _status_path(status_file)
    with _lock:
        return _load_status_unlocked(sf)


def _save_status_unlocked(status: dict, sf: Path):
    """Atomically save status while the caller holds ``_lock``."""
    temp_path = None
    try:
        sf.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f'.{sf.name}.', suffix='.tmp', dir=str(sf.parent)
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, sf)
        temp_path = None
    except Exception as e:
        logger.error(f"保存状态失败: {e}")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def save_status(status: dict, status_file: Optional[Path] = None):
    """保存状态到 JSON 文件。

    Args:
        status: 要保存的状态 dict
        status_file: 状态文件路径，为 None 时使用 DEFAULT_STATUS_FILE。
    """
    sf = _status_path(status_file)
    with _lock:
        _save_status_unlocked(status, sf)


def update_status(mutator, status_file: Optional[Path] = None) -> dict:
    """Atomically load, mutate and save a status document."""
    sf = _status_path(status_file)
    with _lock:
        status = _load_status_unlocked(sf)
        mutator(status)
        _save_status_unlocked(status, sf)
        return status


def mark_today_done(status_file: Optional[Path] = None):
    """标记今日全量更新已完成。"""
    today = datetime.now().strftime('%Y-%m-%d')

    def mutate(status):
        status['today'] = today

    status = update_status(mutate, status_file)
    logger.info(f"[标记] 今日({status['today']})全量更新已完成")


def mark_qmt_daily_done(status_file: Optional[Path] = None):
    """标记今日 QMT 个股日更已完成。"""
    today = datetime.now().strftime('%Y-%m-%d')

    def mutate(status):
        status['qmt_daily_done'] = today

    update_status(mutate, status_file)


def is_today_updated(status_file: Optional[Path] = None) -> bool:
    """今日是否已完成全量更新。"""
    status = load_status(status_file)
    return status.get('today') == datetime.now().strftime('%Y-%m-%d')


def is_qmt_daily_done(status_file: Optional[Path] = None) -> bool:
    """今日是否已完成 QMT 个股日更。"""
    status = load_status(status_file)
    return status.get('qmt_daily_done') == datetime.now().strftime('%Y-%m-%d')
