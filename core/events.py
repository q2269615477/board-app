"""
core/events.py — 事件总线（发布-订阅模式）
用于SSE推送和模块间通信，替代原有的 _ai_sse_event_queue + _ai_result_store
"""
import queue
import threading
import logging
from typing import Callable, Any, Optional

logger = logging.getLogger('events')


class EventBus:
    """线程安全的事件总线"""

    def __init__(self):
        self._listeners: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()
        self._event_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._running = True

    def subscribe(self, event_type: str, callback: Callable):
        with self._lock:
            if event_type not in self._listeners:
                self._listeners[event_type] = []
            self._listeners[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        with self._lock:
            if event_type in self._listeners:
                self._listeners[event_type] = [
                    c for c in self._listeners[event_type] if c != callback
                ]

    def publish(self, event_type: str, data: Any = None):
        """发布事件（同步通知所有监听器）"""
        with self._lock:
            listeners = list(self._listeners.get(event_type, []))
        for cb in listeners:
            try:
                cb(data)
            except Exception as e:
                logger.error(f"[EventBus] {event_type} handler error: {e}")

    def push_sse(self, event_type: str, data: Any = None):
        """推入SSE队列（异步）"""
        try:
            self._event_queue.put_nowait((event_type, data))
        except queue.Full:
            logger.warning("[EventBus] SSE queue overflow, dropping event")

    def get_sse_events(self, timeout: float = 0.1) -> Optional[tuple]:
        """SSE消费者获取事件"""
        try:
            return self._event_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self):
        with self._lock:
            self._listeners.clear()


# 全局单例
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
