"""
services/update_event_log.py — 数据更新 SSE 事件通知

职责：
 - 将数据更新事件推送到 EventBus
 - 从 EventBus 非阻塞读取待处理事件（兼容旧接口）

从 data_update_manager.py 中抽取，保持行为不变。
"""
import logging
from datetime import datetime
from typing import List, Tuple

logger = logging.getLogger('data_update')


def notify_event(event_type: str, message: str = ''):
    """推送事件到 EventBus。

    Args:
        event_type: 事件类型（如 'data_updating', 'data_updated', 'scheduler_error'）
        message: 人类可读的消息文本
    """
    try:
        from core.events import get_event_bus
        get_event_bus().push_sse(event_type, {
            'type': event_type,
            'message': message,
            'time': datetime.now().strftime('%H:%M:%S'),
        })
    except Exception as e:
        logger.debug(f"[SSE] 推送事件失败: {e}")


def get_sse_events(last_index: int = 0) -> Tuple[List[dict], int]:
    """从 EventBus 队列中非阻塞读取待处理事件。

    返回 (events_list, new_index)，与旧接口签名一致。

    Args:
        last_index: 上次读取到的索引（兼容旧接口，当前实现忽略）

    Returns:
        (events, 0) — 事件列表和固定索引 0
    """
    try:
        from core.events import get_event_bus
        bus = get_event_bus()
        events = []
        while True:
            evt = bus.get_sse_events(timeout=0.01)
            if evt is None:
                break
            event_type, data = evt
            events.append(data if isinstance(data, dict) else {'type': event_type, 'message': str(data)})
        return events, 0
    except Exception:
        return [], 0
