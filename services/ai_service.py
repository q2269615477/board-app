"""
services/ai_service.py — AI分析结果管理
"""
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict

from core.cache import get_cache
from core.events import get_event_bus

logger = logging.getLogger('ai_service')


class AiService:
    """AI分析服务"""

    def __init__(self):
        self._cache = get_cache()
        self._event_bus = get_event_bus()
        self._result_store = {}
        self._result_lock = threading.Lock()

    def get_result(self, board_code: str) -> Optional[dict]:
        """获取AI分析结果"""
        return self._result_store.get(board_code)

    def save_result(self, board_code: str, skill_id: str, result: dict) -> bool:
        """保存AI分析结果"""
        try:
            with self._result_lock:
                self._result_store[board_code] = {
                    'code': board_code,
                    'skill_id': skill_id,
                    'summary': result.get('summary', ''),
                    'direction': result.get('direction', ''),
                    'confidence': result.get('confidence', 0),
                    'support': result.get('support', []),
                    'resistance': result.get('resistance', []),
                    'action': result.get('action', ''),
                    'reasons': result.get('reasons', []),
                    'annotations': result.get('annotations', []),
                    'timestamp': result.get('timestamp', '')
                }
            self._event_bus.push_sse('ai_result', board_code)
            self._event_bus.publish('ai_result', board_code)
            return True
        except Exception as e:
            logger.error(f"[AI] 保存结果失败: {e}")
            return False

    def clear_result(self, board_code: str):
        """清除AI结果"""
        with self._result_lock:
            self._result_store.pop(board_code, None)

    def get_result_lock(self):
        return self._result_lock


# 全局单例

_ai_service: Optional[AiService] = None


def get_ai_service() -> AiService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AiService()
    return _ai_service
