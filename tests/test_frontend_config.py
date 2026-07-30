"""test_frontend_config.py — /api/system/frontend-config 路由测试"""
from unittest.mock import patch
import pytest


class TestFrontendConfigRoute:
    """验证 GET /api/system/frontend-config 返回正确的配置键与类型"""

    def _make_client(self):
        """构造 Flask test client（隔离 app 启动副作用）"""
        with patch('app.start_app'), patch('app.realtime_websocket'):
            from app import app
            app.config['TESTING'] = True
            return app.test_client()

    def test_route_returns_200(self):
        """路由可访问，状态码 200"""
        client = self._make_client()
        r = client.get('/api/system/frontend-config')
        assert r.status_code == 200

    def test_response_contains_required_keys(self):
        """响应包含全部必需键"""
        client = self._make_client()
        r = client.get('/api/system/frontend-config')
        data = r.get_json()
        assert 'kline_sync_timeout' in data
        assert 'kline_fetch_timeout_ms' in data
        assert 'loading_min_ms' in data
        assert 'loading_max_ms' in data

    def test_all_values_are_numbers(self):
        """所有配置值均为数字（int 或 float）"""
        client = self._make_client()
        r = client.get('/api/system/frontend-config')
        data = r.get_json()
        for key in ('kline_sync_timeout', 'kline_fetch_timeout_ms',
                    'loading_min_ms', 'loading_max_ms'):
            assert isinstance(data[key], (int, float)), \
                f"{key} 应为数字，实际为 {type(data[key])}"

    def test_default_loading_min_ms(self):
        """loading_min_ms 默认值为 300"""
        client = self._make_client()
        r = client.get('/api/system/frontend-config')
        data = r.get_json()
        assert data['loading_min_ms'] == 300

    def test_default_loading_max_ms(self):
        """loading_max_ms 默认值为 8000"""
        client = self._make_client()
        r = client.get('/api/system/frontend-config')
        data = r.get_json()
        assert data['loading_max_ms'] == 8000
