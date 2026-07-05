"""
services/signal_service.py — 信号系统业务逻辑
"""
import json
import logging
from pathlib import Path
from typing import Optional

from core.cache import get_cache

logger = logging.getLogger('signal_service')


class SignalService:
    """信号管理服务"""

    def __init__(self):
        self._cache = get_cache()
        self._signals_file = Path(__file__).resolve().parent.parent / 'data' / 'signals.json'

    def get_signals(self, board_code: str) -> list:
        """获取板块信号"""
        if self._signals_file.exists():
            with open(self._signals_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get(board_code, [])
        return []

    def submit_signals(self, board_code: str, skill: str, signals: list,
                       mode: str = 'append') -> bool:
        """提交信号"""
        try:
            data = {}
            if self._signals_file.exists():
                with open(self._signals_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

            if board_code not in data:
                data[board_code] = []

            # 限制每个板块最多200条
            if mode == 'replace':
                data[board_code] = signals
            else:
                data[board_code].extend(signals)
                data[board_code] = data[board_code][-200:]

            # 原子写入
            tmp_path = self._signals_file.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._signals_file)
            return True
        except Exception as e:
            logger.error(f"[Signal] 提交失败: {e}")
            return False


# 全局单例

_signal_service: Optional[SignalService] = None


def get_signal_service() -> SignalService:
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service
