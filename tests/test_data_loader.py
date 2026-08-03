"""test_data_loader.py — 数据加载层测试"""
import sqlite3
import tempfile
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import pandas as pd
import pytest

from data_loader import (
    _normalize_df, _resample, _safe_filename,
    _sqlite_spot, _qmt_live_spot, get_spot_index, get_spot_stock,
    get_global_index_spot, get_all_stocks,
)


class TestNormalizeDf:
    """列名映射测试"""

    def test_normal_df(self):
        df = pd.DataFrame({
            '日期': ['2025-01-01'],
            '开盘': [100], '收盘': [105],
            '最高': [110], '最低': [95],
            '成交量': [10000],
        })
        result = _normalize_df(df)
        assert 'date' in result.columns
        assert 'open' in result.columns
        assert len(result) == 1

    def test_empty_df(self):
        df = pd.DataFrame()
        result = _normalize_df(df)
        assert result.empty

    def test_none_df(self):
        result = _normalize_df(None)
        assert result.empty


class TestResample:
    """重采样测试"""

    def test_weekly(self):
        df = pd.DataFrame({
            'date': ['2025-01-02', '2025-01-03', '2025-01-09'],
            'open': [100, 101, 102],
            'high': [110, 111, 112],
            'low': [90, 91, 92],
            'close': [105, 106, 107],
            'volume': [1000, 2000, 3000],
        })
        result = _resample(df, 'weekly')
        assert len(result) >= 1
        assert 'date' in result.columns

    def test_monthly(self):
        df = pd.DataFrame({
            'date': ['2025-01-02', '2025-01-15', '2025-02-03'],
            'open': [100, 101, 102],
            'high': [110, 111, 112],
            'low': [90, 91, 92],
            'close': [105, 106, 107],
            'volume': [1000, 2000, 3000],
        })
        result = _resample(df, 'monthly')
        assert len(result) == 2  # 1月和2月

    def test_empty_df(self):
        df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        result = _resample(df, 'weekly')
        assert result.empty


class TestSafeFilename:
    """文件名清洗测试"""

    def test_normal(self):
        assert _safe_filename('正常名称') == '正常名称'

    def test_bad_chars(self):
        assert _safe_filename('a/b\\c:d*e?f<g>h|i') == 'a_b_c_d_e_f_g_h_i'


class TestSqliteSpot:
    """SQLite 行情测试"""

    def test_no_data(self, tmp_path):
        """无数据返回空 dict"""
        db_path = str(tmp_path / 'test.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
        conn.commit()
        conn.close()

        with patch('data_loader.DB_PATH', db_path):
            with patch('data_loader._conn_local') as mock_local:
                mock_conn = sqlite3.connect(db_path)
                mock_local.conn = mock_conn
                result = _sqlite_spot('sh000001')
                assert result == {}
                mock_conn.close()

    def test_with_data(self, tmp_path):
        """有数据返回正确价格"""
        db_path = str(tmp_path / 'test.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)')
        conn.execute("INSERT INTO kline VALUES ('sh000001', 'daily', '2025-01-01', 3000, 3100, 2950, 3050, 100000)")
        conn.execute("INSERT INTO kline VALUES ('sh000001', 'daily', '2024-12-31', 2980, 3010, 2970, 3000, 90000)")
        conn.commit()
        conn.close()

        with patch('data_loader._conn_local') as mock_local:
            mock_conn = sqlite3.connect(db_path)
            mock_local.conn = mock_conn
            result = _sqlite_spot('sh000001')
            assert result['price'] == 3050.0
            assert result['pre_close'] == 3000.0
            mock_conn.close()


class TestQmtLiveSpot:
    """QMT 子进程行情测试"""

    def test_qmt_disabled(self):
        """QMT 禁用时返回空"""
        with patch('core.lifecycle.is_qmt_available', return_value=False):
            result = _qmt_live_spot('sh000001')
            assert result == {}


class TestGetSpotIndex:
    """指数行情测试"""

    @patch('data_loader._qmt_live_spot', return_value={})
    @patch('data_loader._fetch_a_share_index_eastmoney', return_value={})
    @patch('data_loader._sqlite_spot', return_value={'price': 3000})
    def test_fallback_to_sqlite(self, mock_sqlite, mock_eastmoney, mock_qmt):
        result = get_spot_index('sh000001')
        assert result['price'] == 3000

    @patch('data_loader._qmt_live_spot', return_value={'price': 3100, 'change_pct': 1.5})
    @patch('data_loader._sqlite_spot', return_value={})
    def test_qmt_priority(self, mock_sqlite, mock_qmt):
        result = get_spot_index('sh000001')
        assert result['price'] == 3100


class TestGetSpotStock:
    """个股行情测试"""

    @patch('data_loader._qmt_live_spot', return_value={})
    @patch('data_loader._sqlite_spot', return_value={'price': 1800})
    def test_fallback_to_sqlite(self, mock_sqlite, mock_qmt):
        result = get_spot_stock('600519')
        assert result['price'] == 1800


class TestGetGlobalIndexSpot:
    """全球指数行情测试"""

    @pytest.fixture(autouse=True)
    def clear_global_spot_cache(self):
        import data_loader
        data_loader._GLOBAL_INDEX_SPOT_CACHE.clear()
        data_loader._GLOBAL_INDEX_SPOT_CACHE_TS.clear()
        data_loader._GLOBAL_INDEX_SPOT_KEY_LOCKS.clear()

    def test_unknown_code(self):
        result = get_global_index_spot('UNKNOWN')
        assert result == {}

    def test_different_indices_fetch_concurrently(self, monkeypatch):
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor

        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_fetch(code):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {'price': 1, 'channel': code}

        monkeypatch.setattr(
            'data_loader._fetch_global_index_spot_uncached', fake_fetch
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            rows = list(executor.map(get_global_index_spot, ('HSI', 'SPX')))

        assert [row['channel'] for row in rows] == ['HSI', 'SPX']
        assert max_active == 2

    def test_eastmoney_is_primary_and_scales_snapshot_fields(self, monkeypatch):
        requested = []

        class Response:
            def json(self):
                return {'data': {
                    'f43': 4311975,
                    'f57': 'TWII',
                    'f58': '台湾加权',
                    'f60': 3993330,
                    'f169': 318645,
                    'f170': 798,
                }}

        class Session:
            trust_env = True

            def get(self, url, **kwargs):
                requested.append((url, kwargs['params']['secid']))
                return Response()

        monkeypatch.setattr('requests.Session', Session)

        result = get_global_index_spot('^TWII')

        assert requested == [(
            'https://push2delay.eastmoney.com/api/qt/stock/get',
            '100.TWII',
        )]
        assert result == {
            'name': '台湾加权',
            'price': 43119.75,
            'change': 3186.45,
            'change_pct': 7.98,
            'channel': 'eastmoney_push2delay',
        }

    def test_eastmoney_all_a_uses_official_choice_secid(self, monkeypatch):
        requested = []

        class Response:
            def json(self):
                return {'data': {
                    'f43': 635092,
                    'f57': '800000',
                    'f58': '东方财富全A',
                    'f60': 626683,
                    'f169': 8409,
                    'f170': 134,
                }}

        class Session:
            trust_env = True

            def get(self, url, **kwargs):
                requested.append(kwargs['params']['secid'])
                return Response()

        monkeypatch.setattr('requests.Session', Session)

        result = get_global_index_spot('800000')

        assert requested == ['47.800000']
        assert result['name'] == '东方财富全A'
        assert result['price'] == pytest.approx(6350.92)
        assert result['change_pct'] == pytest.approx(1.34)

    def test_eastmoney_retries_one_transient_empty_response(self, monkeypatch):
        calls = 0

        class Response:
            def json(self):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return {'data': None}
                return {'data': {
                    'f43': 6436202,
                    'f57': 'N225',
                    'f58': '日经225',
                    'f60': 6186743,
                    'f169': 249459,
                    'f170': 403,
                }}

        class Session:
            trust_env = True

            def get(self, *args, **kwargs):
                return Response()

        monkeypatch.setattr('requests.Session', Session)
        monkeypatch.setattr('data_loader.time.sleep', lambda _seconds: None)

        result = get_global_index_spot('^N225')

        assert calls == 2
        assert result['price'] == 64362.02
        assert result['channel'] == 'eastmoney_push2delay'

    def test_asia_pacific_uses_yahoo_latest_two_closes(self, monkeypatch):
        class Response:
            def json(self):
                return {
                    'chart': {'result': [{
                        'meta': {
                            'regularMarketPrice': 64362.02,
                            'regularMarketTime': 1785480303,
                            'symbol': '^N225',
                        },
                        'indicators': {'quote': [{
                            'close': [61867.43, 64362.02],
                        }]},
                    }]},
                }

        class EastmoneyUnavailable:
            trust_env = True

            def get(self, *args, **kwargs):
                return type('EmptyResponse', (), {'json': lambda self: {'data': None}})()

        monkeypatch.setattr('requests.Session', EastmoneyUnavailable)
        monkeypatch.setattr('requests.get', lambda *args, **kwargs: Response())

        result = get_global_index_spot('^N225')

        assert result['price'] == 64362.02
        assert result['change_pct'] == pytest.approx(4.0321, rel=1e-3)
        assert result['channel'] == 'yahoo'

    def test_taiwan_uses_official_twse_realtime_feed(self, monkeypatch):
        class Response:
            def json(self):
                return {'msgArray': [{
                    'n': '發行量加權股價指數',
                    'z': '43119.75',
                    'y': '39933.30',
                    'tlong': '1785475980000',
                }]}

        class Session:
            trust_env = True

            def get(self, url, *args, **kwargs):
                if 'push2delay.eastmoney.com' in url:
                    return type('EmptyResponse', (), {'json': lambda self: {'data': None}})()
                return Response()

        monkeypatch.setattr('requests.Session', Session)

        result = get_global_index_spot('^TWII')

        assert result['price'] == 43119.75
        assert result['change_pct'] == pytest.approx(7.9794, rel=1e-3)
        assert result['channel'] == 'twse'

    def test_sp500_uses_current_tencent_index_code(self, monkeypatch):
        requested = []

        class Response:
            encoding = 'gbk'

            @property
            def text(self):
                parts = [''] * 33
                parts[1] = '标普500'
                parts[3] = '7460.25'
                parts[31] = '22.62'
                parts[32] = '0.30'
                return 'v_usINX="' + '~'.join(parts) + '";'

        def fake_get(url, **kwargs):
            requested.append(url)
            return Response()

        class EastmoneyUnavailable:
            trust_env = True

            def get(self, *args, **kwargs):
                return type('EmptyResponse', (), {'json': lambda self: {'data': None}})()

        monkeypatch.setattr('requests.Session', EastmoneyUnavailable)
        monkeypatch.setattr('requests.get', fake_get)

        result = get_global_index_spot('SPX')

        assert requested == ['https://qt.gtimg.cn/q=usINX']
        assert result == {
            'price': 7460.25,
            'change_pct': 0.3,
            'change': 22.62,
            'name': '标普500',
            'channel': 'tencent',
        }

    def test_stale_sina_asia_snapshot_is_rejected(self, monkeypatch):
        class SinaResponse:
            encoding = 'gbk'
            text = (
                'var hq_str_b_TWSE="台湾台北指数,25580.32,-443.53,'
                '-1.70,9/26/2025,2025-09-26";'
            )

        def fake_get(url, **kwargs):
            if 'query1.finance.yahoo.com' in url:
                raise RuntimeError('yahoo unavailable')
            if 'query2.finance.yahoo.com' in url:
                raise RuntimeError('yahoo unavailable')
            return SinaResponse()

        class FailedSession:
            trust_env = True

            def get(self, *args, **kwargs):
                raise RuntimeError('twse unavailable')

        monkeypatch.setattr('requests.Session', FailedSession)
        monkeypatch.setattr('requests.get', fake_get)

        assert get_global_index_spot('^TWII') == {}

    def test_tencent_timeout_continues_to_yahoo(self, monkeypatch):
        class YahooResponse:
            def json(self):
                return {
                    'chart': {'result': [{
                        'meta': {
                            'regularMarketPrice': 25308.43,
                            'symbol': '^IXIC',
                        },
                        'indicators': {'quote': [{
                            'close': [25122.18, 25308.43],
                        }]},
                    }]},
                }

        def fake_get(url, **kwargs):
            if 'qt.gtimg.cn' in url:
                raise TimeoutError('tencent timeout')
            if 'query1.finance.yahoo.com' in url:
                return YahooResponse()
            raise AssertionError('must not fall through to Sina')

        class EastmoneyUnavailable:
            trust_env = True

            def get(self, *args, **kwargs):
                return type('EmptyResponse', (), {'json': lambda self: {'data': None}})()

        monkeypatch.setattr('requests.Session', EastmoneyUnavailable)
        monkeypatch.setattr('requests.get', fake_get)

        result = get_global_index_spot('IXIC')

        assert result['price'] == 25308.43
        assert result['channel'] == 'yahoo'


class TestGetAllStocks:
    """个股列表测试"""

    @patch('core.lifecycle.is_qmt_available', return_value=False)
    def test_qmt_disabled_returns_empty(self, mock_avail):
        result = get_all_stocks()
        assert result == []
