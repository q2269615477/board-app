"""test_mcp_handlers.py — MCP 处理器测试"""
import pytest
from mcp.handlers import MCPHandler


class TestSessionIsolation:
    """会话隔离测试"""

    def test_session_isolation_overlays(self):
        handler = MCPHandler()
        # session A 创建画线
        handler.handle('create_overlay', {
            'type': 'line',
            'points': [{'time': 1000, 'price': 100}]
        }, session_id='session_a')
        # session B 创建画线
        handler.handle('create_overlay', {
            'type': 'line',
            'points': [{'time': 2000, 'price': 200}]
        }, session_id='session_b')
        # 验证 A 的画线不影响 B
        result_a = handler.handle('get_overlays', {}, session_id='session_a')
        result_b = handler.handle('get_overlays', {}, session_id='session_b')
        assert result_a['count'] == 1
        assert result_b['count'] == 1
        assert result_a['overlays'][0]['points'][0]['time'] == 1000
        assert result_b['overlays'][0]['points'][0]['time'] == 2000

    def test_session_isolation_symbol(self):
        handler = MCPHandler()
        handler.handle('set_symbol', {'symbol': 'sh000001'}, session_id='s1')
        handler.handle('set_symbol', {'symbol': 'sz399006'}, session_id='s2')
        ctx1 = handler.handle('get_chart_context', {}, session_id='s1')
        ctx2 = handler.handle('get_chart_context', {}, session_id='s2')
        assert ctx1['context']['symbol'] == 'sh000001'
        assert ctx2['context']['symbol'] == 'sz399006'

    def test_default_session_backward_compat(self):
        """无 session_id 时使用 default 会话"""
        handler = MCPHandler()
        result = handler.handle('set_symbol', {'symbol': 'sh000001'})
        assert result['success']
        # chart_state 属性应返回 default 会话
        assert handler.chart_state['symbol'] == 'sh000001'


class TestHandleSetSymbol:
    """切换标的测试"""

    def test_set_symbol_success(self):
        handler = MCPHandler()
        result = handler.handle('set_symbol', {'symbol': 'sh000001', 'symbol_type': 'index'})
        assert result['success']
        assert result['symbol'] == 'sh000001'

    def test_set_symbol_missing(self):
        handler = MCPHandler()
        result = handler.handle('set_symbol', {})
        assert not result['success']
        assert '缺少' in result['error']


class TestHandleSetPeriod:
    """切换周期测试"""

    def test_set_period_success(self):
        handler = MCPHandler()
        result = handler.handle('set_period', {'period': 'weekly'})
        assert result['success']
        assert result['period'] == 'weekly'

    def test_set_period_missing(self):
        handler = MCPHandler()
        result = handler.handle('set_period', {})
        assert not result['success']


class TestHandleCreateOverlay:
    """创建画线测试"""

    def test_create_overlay_success(self):
        handler = MCPHandler()
        result = handler.handle('create_overlay', {
            'type': 'line',
            'points': [{'time': 1000, 'price': 100}]
        })
        assert result['success']
        assert 'overlayId' in result

    def test_create_overlay_missing_type(self):
        handler = MCPHandler()
        result = handler.handle('create_overlay', {'points': []})
        assert not result['success']

    def test_create_overlay_missing_points(self):
        handler = MCPHandler()
        result = handler.handle('create_overlay', {'type': 'line'})
        assert not result['success']


class TestHandleRemoveOverlay:
    """删除画线测试"""

    def test_remove_overlay_success(self):
        handler = MCPHandler()
        create_result = handler.handle('create_overlay', {
            'type': 'line', 'points': [{'time': 1, 'price': 1}]
        })
        overlay_id = create_result['overlayId']
        remove_result = handler.handle('remove_overlay', {'overlayId': overlay_id})
        assert remove_result['success']
        assert remove_result['removed']

    def test_remove_overlay_not_found(self):
        handler = MCPHandler()
        result = handler.handle('remove_overlay', {'overlayId': 'nonexistent'})
        assert result['success']
        assert not result['removed']


class TestHandleUnknownTool:
    """未知工具测试"""

    def test_unknown_tool(self):
        handler = MCPHandler()
        result = handler.handle('unknown_tool', {})
        assert not result['success']
        assert '未知工具' in result['error']
