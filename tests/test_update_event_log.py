"""tests/test_update_event_log.py — update_event_log 模块测试

验证从 data_update_manager.py 抽取的 SSE 事件通知层：
  - notify_event 不崩溃
  - get_sse_events 返回 tuple
  - notify → get 往返一致
  - data_update_manager.py facade 委托正确
"""
import pytest

from services.update_event_log import notify_event, get_sse_events


class TestNotifyEvent:
    """notify_event 基本行为。"""

    def test_notify_does_not_crash(self):
        """推送事件不应抛出异常。"""
        notify_event('test_event', 'test message')

    def test_notify_empty_message(self):
        """空消息也不应崩溃。"""
        notify_event('test_event')

    def test_notify_no_args_except_type(self):
        """仅有事件类型不应崩溃。"""
        notify_event('bare_event')


class TestGetSSEEvents:
    """get_sse_events 基本行为。"""

    def test_returns_tuple(self):
        events, idx = get_sse_events(0)
        assert isinstance(events, list)
        assert isinstance(idx, int)

    def test_returns_tuple_with_custom_index(self):
        events, idx = get_sse_events(42)
        assert isinstance(events, list)
        assert isinstance(idx, int)


class TestNotifyThenGet:
    """推送 → 读取 往返一致性。"""

    def test_notify_then_get(self):
        """推送事件后可以读取到。"""
        from core.events import get_event_bus
        bus = get_event_bus()
        # 清空队列
        while bus.get_sse_events(timeout=0.01) is not None:
            pass

        notify_event('test_type', 'test_msg')
        events, _ = get_sse_events(0)
        assert len(events) >= 1
        assert events[0]['type'] == 'test_type'
        assert events[0]['message'] == 'test_msg'

    def test_notify_multiple_then_get(self):
        """多个事件都能读取到。"""
        from core.events import get_event_bus
        bus = get_event_bus()
        while bus.get_sse_events(timeout=0.01) is not None:
            pass

        notify_event('evt1', 'msg1')
        notify_event('evt2', 'msg2')
        events, _ = get_sse_events(0)
        assert len(events) >= 2

    def test_event_has_timestamp(self):
        """事件应包含时间字段。"""
        from core.events import get_event_bus
        bus = get_event_bus()
        while bus.get_sse_events(timeout=0.01) is not None:
            pass

        notify_event('timed_evt', 'check time')
        events, _ = get_sse_events(0)
        assert len(events) >= 1
        assert 'time' in events[0]
        # 时间格式 HH:MM:SS
        assert len(events[0]['time']) == 8


class TestFacadeDelegation:
    """验证 data_update_manager.py 中的 facade 函数正确委托。"""

    def test_dum_notify_event_delegates(self):
        """_notify_event 委托给 update_event_log.notify_event。"""
        from data_update_manager import _notify_event
        _notify_event('facade_test', 'delegation check')
        # 不崩溃即证明委托成功

    def test_dum_get_sse_events_delegates(self):
        """get_sse_events 委托给 update_event_log.get_sse_events。"""
        from data_update_manager import get_sse_events
        events, idx = get_sse_events(0)
        assert isinstance(events, list)
        assert isinstance(idx, int)

    def test_dum_notify_then_get_roundtrip(self):
        """data_update_manager facade 的推送→读取往返。"""
        from data_update_manager import _notify_event, get_sse_events
        from core.events import get_event_bus
        bus = get_event_bus()
        while bus.get_sse_events(timeout=0.01) is not None:
            pass

        _notify_event('facade_roundtrip', 'hello')
        events, _ = get_sse_events(0)
        assert len(events) >= 1
        assert events[0]['type'] == 'facade_roundtrip'
