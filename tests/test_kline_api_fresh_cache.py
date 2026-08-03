"""tests/test_kline_api_fresh_cache.py — API 层 K 线 fresh-cache 测试

验证用户真实点击路径：/api/kline/stock/603259?period=daily
在本地缓存为空时，API 返回的 JSON 包含前端可消费的稳定字段。
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)


def _make_client():
    """构造 Flask test client（使用 patch 隔离 app 启动副作用）"""
    with patch('app.start_app'), patch('app.realtime_websocket'):
        from app import app
        app.config['TESTING'] = True
        return app.test_client()


def _mock_svc_with_data():
    """模拟 KLineService 返回正常日线数据。"""
    mock_svc = MagicMock()
    bar_ts = int(pd.Timestamp('2026-07-29').timestamp() * 1000)
    mock_svc.get_kline.return_value = {
        'data': [{
            'timestamp': bar_ts,
            'open': 1680.0,
            'high': 1695.0,
            'low': 1675.0,
            'close': 1690.0,
            'volume': 12345678,
        }],
        'count': 1,
        'last_date': '2026-07-29',
        'today': '2026-07-30',
        'range': f'{bar_ts}~{bar_ts}',
        'cached': False,
        'source': 'load',
    }, 200
    return mock_svc


def _mock_svc_with_history():
    mock_svc = MagicMock()
    rows = []
    for day in range(1, 6):
        timestamp = int(pd.Timestamp(f'2026-07-{day:02d}').timestamp() * 1000)
        rows.append({
            'timestamp': timestamp,
            'open': 100.0 + day,
            'high': 102.0 + day,
            'low': 99.0 + day,
            'close': 101.0 + day,
            'volume': 1000 * day,
        })
    mock_svc.get_kline.return_value = {
        'data': rows,
        'count': len(rows),
        'last_date': '2026-07-05',
        'range': f'{rows[0]["timestamp"]}~{rows[-1]["timestamp"]}',
        'source': 'sqlite',
        'intraday': {'close': 999.0},
    }, 200
    return mock_svc


def _mock_svc_stale():
    """模拟 KLineService 返回 stale 数据（cache_first 命中 SQLite）。"""
    mock_svc = MagicMock()
    bar_ts = int(pd.Timestamp('2026-07-28').timestamp() * 1000)
    mock_svc.get_kline.return_value = {
        'data': [{
            'timestamp': bar_ts,
            'open': 1670.0,
            'high': 1685.0,
            'low': 1665.0,
            'close': 1680.0,
            'volume': 9876543,
        }],
        'count': 1,
        'last_date': '2026-07-28',
        'today': '2026-07-30',
        'range': f'{bar_ts}~{bar_ts}',
        'cached': False,
        'source': 'sqlite',
        'stale': True,
        'background_refresh_started': True,
    }, 200
    return mock_svc


def _mock_svc_loading():
    """模拟 KLineService 返回 loading。"""
    mock_svc = MagicMock()
    mock_svc.get_kline.return_value = {
        'loading': True,
        'message': '数据加载中',
        'data': [],
        'count': 0,
    }, 202
    return mock_svc


def _mock_svc_error():
    """模拟 KLineService 返回错误。"""
    mock_svc = MagicMock()
    mock_svc.get_kline.return_value = {
        'error': '加载超时',
        'timeout': True,
        'data': [],
        'count': 0,
    }, 408
    return mock_svc


class TestKlineApiFreshCache:
    """验证 /api/kline/stock/<code>?period=daily 在 fresh cache 下的响应契约。"""

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_returns_200_with_required_fields(self, mock_get_svc):
        """Fresh cache 下 API 返回 200，JSON 包含 data/count/source/last_date。"""
        mock_get_svc.return_value = _mock_svc_with_data()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily')
        assert r.status_code == 200
        j = r.get_json()
        assert 'data' in j
        assert 'count' in j
        assert 'source' in j
        assert 'last_date' in j
        assert isinstance(j['data'], list)
        assert j['count'] == len(j['data'])
        if j['data']:
            bar = j['data'][0]
            for field in ('timestamp', 'open', 'high', 'low', 'close', 'volume'):
                assert field in bar, f"K线 bar 缺少 {field}"

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_stale_response_contract(self, mock_get_svc):
        """cache_first=1 时返回 stale 数据，包含 stale/source/background_refresh_started。"""
        mock_get_svc.return_value = _mock_svc_stale()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily&prefer_cache=1')
        assert r.status_code == 200
        j = r.get_json()
        assert j['stale'] is True
        assert j['source'] == 'sqlite'
        assert j['background_refresh_started'] is True
        assert 'data' in j and len(j['data']) > 0

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_loading_returns_202(self, mock_get_svc):
        """Fresh cache 下首次请求可能返回 202 loading。"""
        mock_get_svc.return_value = _mock_svc_loading()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily')
        assert r.status_code == 202
        j = r.get_json()
        assert j['loading'] is True
        assert 'message' in j

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_all_sources_fail_returns_408(self, mock_get_svc):
        """所有数据源不可用时返回 408 + error 字段。"""
        mock_get_svc.return_value = _mock_svc_error()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily')
        assert r.status_code == 408
        j = r.get_json()
        assert 'error' in j
        assert j['timeout'] is True
        assert j['data'] == []

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_timestamp_is_ms_int(self, mock_get_svc):
        """K线 bar 的 timestamp 必须是毫秒级整数。"""
        mock_get_svc.return_value = _mock_svc_with_data()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily')
        assert r.status_code == 200
        j = r.get_json()
        assert j['data']
        ts = j['data'][0]['timestamp']
        assert isinstance(ts, int)
        assert 1577836800000 <= ts <= 1893456000000

    @patch('api.kline_routes.get_kline_service')
    def test_fresh_cache_ohlc_types_and_logic(self, mock_get_svc):
        """K线 bar 的 OHLCV 字段类型和逻辑约束。"""
        mock_get_svc.return_value = _mock_svc_with_data()
        client = _make_client()
        r = client.get('/api/kline/stock/603259?period=daily')
        assert r.status_code == 200
        j = r.get_json()
        bar = j['data'][0]
        assert isinstance(bar['open'], (int, float))
        assert isinstance(bar['high'], (int, float))
        assert isinstance(bar['low'], (int, float))
        assert isinstance(bar['close'], (int, float))
        assert isinstance(bar['volume'], int)
        assert bar['high'] >= bar['low']
        assert bar['high'] >= bar['open']
        assert bar['high'] >= bar['close']

    @patch('api.kline_routes.get_kline_service')
    def test_history_window_returns_only_requested_bars(self, mock_get_svc):
        mock_get_svc.return_value = _mock_svc_with_history()
        client = _make_client()
        start = int(pd.Timestamp('2026-07-02').timestamp() * 1000)
        end = int(pd.Timestamp('2026-07-04').timestamp() * 1000)

        r = client.get(
            f'/api/kline/stock/603259?period=daily&from={start}&to={end}'
        )

        assert r.status_code == 200
        j = r.get_json()
        assert j['count'] == 3
        assert j['total_count'] == 5
        assert j['windowed'] is True
        assert [row['timestamp'] for row in j['data']] == [
            int(pd.Timestamp(f'2026-07-{day:02d}').timestamp() * 1000)
            for day in range(2, 5)
        ]
        assert 'intraday' not in j

    @patch('api.kline_routes.get_kline_service')
    def test_history_limit_keeps_latest_bars(self, mock_get_svc):
        mock_get_svc.return_value = _mock_svc_with_history()
        client = _make_client()

        r = client.get('/api/kline/stock/603259?period=daily&limit=2')

        assert r.status_code == 200
        j = r.get_json()
        assert j['count'] == 2
        assert j['total_count'] == 5
        assert j['last_date'] == '2026-07-05'
