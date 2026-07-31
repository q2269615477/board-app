"""test_events.py — 事件总线测试"""
import time
import threading
from unittest.mock import patch, MagicMock
import pytest

from core.events import EventBus, get_event_bus


class TestSubscribePublish:
    """订阅发布测试"""

    def test_subscribe_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe('test_event', lambda data: received.append(data))
        bus.publish('test_event', {'key': 'value'})
        assert received == [{'key': 'value'}]

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        cb = lambda data: received.append(data)
        bus.subscribe('test_event', cb)
        bus.unsubscribe('test_event', cb)
        bus.publish('test_event', 'data')
        assert received == []

    def test_publish_no_listeners(self):
        bus = EventBus()
        bus.publish('no_listeners', 'data')  # 不应崩溃

    def test_multiple_listeners(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe('evt', lambda d: r1.append(d))
        bus.subscribe('evt', lambda d: r2.append(d))
        bus.publish('evt', 'hello')
        assert r1 == ['hello']
        assert r2 == ['hello']


class TestSSEQueue:
    """SSE 队列测试"""

    def test_push_sse_get_sse_events(self):
        bus = EventBus()
        bus.push_sse('update', {'msg': 'hello'})
        evt = bus.get_sse_events(timeout=1.0)
        assert evt is not None
        assert evt[0] == 'update'

    def test_get_sse_empty(self):
        bus = EventBus()
        evt = bus.get_sse_events(timeout=0.01)
        assert evt is None

    def test_push_sse_overflow(self):
        """队列满时不崩溃"""
        bus = EventBus()
        # 填满队列
        for i in range(bus._event_queue.maxsize):
            bus.push_sse('test', i)
        # 溢出
        bus.push_sse('overflow', 'data')
        # 不应崩溃，overflow_count 应递增
        assert bus._overflow_count >= 1

    def test_push_sse_overflow_counter(self):
        """溢出计数器递增"""
        bus = EventBus()
        for i in range(bus._event_queue.maxsize):
            bus.push_sse('test', i)
        initial = bus._overflow_count
        bus.push_sse('overflow1', 'd1')
        bus.push_sse('overflow2', 'd2')
        assert bus._overflow_count == initial + 2

    def test_overflow_logs_warning(self):
        """溢出时记录警告日志"""
        bus = EventBus()
        for i in range(bus._event_queue.maxsize):
            bus.push_sse('test', i)
        with patch('core.events.logger') as mock_logger:
            bus.push_sse('overflow', 'data')
            assert mock_logger.warning.called


class TestClear:
    """清除测试"""

    def test_clear(self):
        bus = EventBus()
        bus.subscribe('evt', lambda d: None)
        bus.clear()
        # 清除后 publish 不应崩溃
        bus.publish('evt', 'data')


class TestGetStats:
    """统计信息测试"""

    def test_get_stats(self):
        bus = EventBus()
        bus.subscribe('evt', lambda d: None)
        bus.push_sse('sse_evt', 'data')
        stats = bus.get_stats()
        assert 'queue_size' in stats
        assert 'queue_maxsize' in stats
        assert 'overflow_count' in stats
        assert 'listener_count' in stats
        assert stats['listener_count'] == 1


class TestSingleton:
    """单例测试"""

    def test_get_event_bus_singleton(self):
        b1 = get_event_bus()
        b2 = get_event_bus()
        assert b1 is b2
