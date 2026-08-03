"""test_kline_routes.py — K线路由参数透传测试"""
from unittest.mock import patch, MagicMock
import pytest

from core.config import KLINE_SYNC_TIMEOUT


class TestKlineRouteParams:
    """验证 /api/kline route 能正确读取 query 参数并传给服务"""

    def _make_client(self):
        """构造 Flask test client（使用 patch 隔离 app 启动副作用）"""
        # 延迟 import，避免 conftest 之前加载 app
        with patch('app.start_app'), patch('app.realtime_websocket'):
            from app import app
            app.config['TESTING'] = True
            return app.test_client()

    @patch('api.kline_routes.get_kline_service')
    def test_prefer_cache_param_passed(self, mock_get_svc):
        """prefer_cache=1 应映射为 cache_first=True 透传到 service.get_kline"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [], 'count': 0, 'last_date': '',
            'source': 'sqlite', 'stale': True, 'background_refresh_started': False
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily&prefer_cache=1')
        assert r.status_code == 200
        # 验证 service.get_kline 被调用时携带 cache_first=True
        call_kwargs = mock_svc.get_kline.call_args
        assert call_kwargs.kwargs.get('cache_first') is True

    @patch('api.kline_routes.get_kline_service')
    def test_stale_ok_param_passed(self, mock_get_svc):
        """stale_ok=1 应映射为 cache_first=True 透传到 service.get_kline"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [], 'count': 0, 'last_date': '',
            'source': 'sqlite', 'stale': True
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily&stale_ok=1')
        assert r.status_code == 200
        call_kwargs = mock_svc.get_kline.call_args
        assert call_kwargs.kwargs.get('cache_first') is True

    @patch('api.kline_routes.get_kline_service')
    def test_refresh_async_param_passed(self, mock_get_svc):
        """refresh_async=1 应映射为 cache_first=True 透传到 service.get_kline"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [], 'count': 0, 'last_date': '',
            'source': 'sqlite', 'stale': True, 'background_refresh_started': True
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily&refresh_async=1')
        assert r.status_code == 200
        call_kwargs = mock_svc.get_kline.call_args
        assert call_kwargs.kwargs.get('cache_first') is True

    @patch('api.kline_routes.get_kline_service')
    def test_timeout_param_passed(self, mock_get_svc):
        """timeout=5 应透传到 service.get_kline"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [], 'count': 0, 'last_date': ''
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily&timeout=5')
        assert r.status_code == 200
        call_kwargs = mock_svc.get_kline.call_args
        assert call_kwargs.kwargs.get('timeout') == 5.0

    @patch('api.kline_routes.get_kline_service')
    def test_response_includes_stale_and_source(self, mock_get_svc):
        """响应 JSON 应包含 stale 和 source 字段"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [{'timestamp': 1704153600000, 'open': 1, 'high': 2,
                      'low': 0.5, 'close': 1.5, 'volume': 100}],
            'count': 1, 'last_date': '2024-01-02',
            'source': 'sqlite', 'stale': True, 'background_refresh_started': True
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily&prefer_cache=1')
        assert r.status_code == 200
        j = r.get_json()
        assert j['stale'] is True
        assert j['source'] == 'sqlite'
        assert j['background_refresh_started'] is True

    @patch('api.kline_routes.get_kline_service')
    def test_default_timeout_from_config(self, mock_get_svc):
        """无 timeout query 时 service 应收到默认 KLINE_SYNC_TIMEOUT"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'data': [], 'count': 0, 'last_date': ''
        }, 200
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily')
        assert r.status_code == 200
        call_kwargs = mock_svc.get_kline.call_args
        assert call_kwargs.kwargs.get('timeout') == float(KLINE_SYNC_TIMEOUT)

    @patch('api.kline_routes.get_kline_service')
    def test_loading_response_202(self, mock_get_svc):
        """service 返回 loading 时响应应含 loading 字段且 status 202"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'loading': True, 'message': '数据加载中',
            'data': [], 'count': 0
        }, 202
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily')
        assert r.status_code == 202
        j = r.get_json()
        assert j['loading'] is True
        assert 'message' in j

    @patch('api.kline_routes.get_kline_service')
    def test_error_response_500(self, mock_get_svc):
        """service 返回 error 时响应应含 error 字段且 status 500"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'error': '加载失败', 'timeout': False,
            'data': [], 'count': 0
        }, 500
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily')
        assert r.status_code == 500
        j = r.get_json()
        assert j['error'] == '加载失败'
        assert j['timeout'] is False

    @patch('api.kline_routes.get_kline_service')
    def test_timeout_response_408(self, mock_get_svc):
        """service 返回 timeout 时响应应含 timeout 字段且 status 408"""
        mock_svc = MagicMock()
        mock_svc.get_kline.return_value = {
            'error': '加载超时', 'timeout': True,
            'data': [], 'count': 0
        }, 408
        mock_get_svc.return_value = mock_svc

        client = self._make_client()
        r = client.get('/api/kline/stock/600519?period=daily')
        assert r.status_code == 408
        j = r.get_json()
        assert j['error'] == '加载超时'
        assert j['timeout'] is True
