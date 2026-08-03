"""test_board_api.py — 板块 API 测试"""
from unittest.mock import patch, MagicMock
import requests
import pandas as pd
import pytest

from data.board_api import (
    _date_fmt, _clean_stock_name, _rate_limit,
    get_industry_boards, get_concept_boards,
    get_board_kline, get_trade_dates,
    _get_constituents, _get_spot,
    _get_board_kline_eastmoney, get_eastmoney_constituents,
)


class _FakeEMResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeEMSession:
    """记录 get/close，并模拟 requests.Session 的默认属性。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.get_calls = []
        self.trust_env = True
        self.proxies = {'inherited': 'must be replaced'}
        self.closed = False

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return _FakeEMResponse({'data': {'diff': []}})

    def close(self):
        self.closed = True


class TestDateFormat:
    """日期格式化测试"""

    def test_normal_date(self):
        assert _date_fmt('20250101') == '2025-01-01'

    def test_short_date(self):
        assert _date_fmt('2025') == '2025'

    def test_empty(self):
        assert _date_fmt('') == ''

    def test_none(self):
        assert _date_fmt(None) is None


class TestCleanStockName:
    """股票名称清洗测试"""

    def test_xd_prefix(self):
        assert _clean_stock_name('XD贵州茅台') == '贵州茅台'

    def test_dr_prefix(self):
        assert _clean_stock_name('DR某股票') == '某股票'

    def test_st_kept(self):
        assert _clean_stock_name('ST某股票') == 'ST某股票'

    def test_normal_name(self):
        assert _clean_stock_name('贵州茅台') == '贵州茅台'

    def test_empty(self):
        assert _clean_stock_name('') == ''


class TestRateLimit:
    """限流器测试"""

    def test_rate_limit_thread_safety(self):
        """多线程调用时间间隔 >= _MIN_INTERVAL"""
        import time
        import threading
        times = []
        barrier = threading.Barrier(3)

        def call_with_barrier():
            barrier.wait()
            _rate_limit()
            times.append(time.time())

        threads = [threading.Thread(target=call_with_barrier) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证调用间隔 >= MIN_INTERVAL（允许一定误差）
        from data.board_api import _MIN_INTERVAL
        if len(times) >= 2:
            intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
            for interval in intervals:
                assert interval >= _MIN_INTERVAL * 0.9  # 允许 10% 误差


class TestNullChecks:
    """_pro 为 None 时的安全检查"""

    @patch('data.board_api._pro', None)
    def test_get_industry_boards_pro_none(self):
        assert get_industry_boards() is None

    @patch('data.board_api._pro', None)
    def test_get_concept_boards_pro_none(self):
        assert get_concept_boards() is None

    @patch('data.board_api._pro', None)
    def test_get_constituents_pro_none(self):
        assert _get_constituents('BK0001') == []

    def test_eastmoney_constituents_parses_required_fields(self, monkeypatch):
        fake = _FakeEMSession([_FakeEMResponse({'data': {'diff': [
            {'f12': '600001', 'f14': '测试一', 'f2': 12.34,
             'f3': 1.25, 'f20': 12345678900, 'f5': 4567},
        ]}})])
        monkeypatch.setattr('requests.Session', lambda: fake)

        result = get_eastmoney_constituents('industry', 'BK0922')
        assert result == [{
            'code': '600001', 'name': '测试一', 'close': 12.34,
            'change_pct': 1.25, 'mkt_cap': 123.456789,
            'volume': 4567.0, 'source': 'eastmoney_push2delay',
        }]
        _url, kwargs = fake.get_calls[0]
        params = kwargs['params']
        assert params['fs'] == 'b:BK0922'
        assert params['fields'] == 'f12,f14,f2,f3,f20,f5'
        assert fake.trust_env is False
        assert fake.proxies == {}
        assert fake.closed is True

    def test_eastmoney_special_sector_codes_are_supported(self, monkeypatch):
        payload = {'data': {'diff': {
            '0': {'f12': '000001', 'f14': '平安银行', 'f2': 10,
                  'f3': -2, 'f20': 100000000, 'f5': 8},
        }}}
        fakes = [
            _FakeEMSession([_FakeEMResponse(dict(payload))])
            for _ in range(2)
        ]
        monkeypatch.setattr('requests.Session', lambda: fakes.pop(0))
        for code in ('BK1064', 'BK1128'):
            rows = get_eastmoney_constituents('concept', code)
            assert rows[0]['code'] == '000001'
            assert rows[0]['change_pct'] == -2
            assert rows[0]['mkt_cap'] == 1.0

    def test_eastmoney_constituents_fail_closed(self, monkeypatch):
        class BoomSession(_FakeEMSession):
            def get(self, url, **kwargs):
                raise RuntimeError('offline')

        fake = BoomSession()
        monkeypatch.setattr('requests.Session', lambda: fake)
        assert get_eastmoney_constituents('industry', 'BK0922') == []
        assert fake.closed is True

    def test_eastmoney_http_uses_local_direct_session_without_global_rewrite(
        self, monkeypatch
    ):
        real_session_cls = requests.Session
        original_init = real_session_cls.__init__
        fake = _FakeEMSession([_FakeEMResponse({'data': {'diff': []}})])
        monkeypatch.setattr('requests.Session', lambda: fake)

        assert get_eastmoney_constituents('industry', 'BK0922') == []
        assert fake.trust_env is False
        assert fake.proxies == {}
        assert real_session_cls.__init__ is original_init
        probe = real_session_cls()
        try:
            assert probe.trust_env is True
            assert probe.proxies == {}
        finally:
            probe.close()

    def test_board_kline_eastmoney_uses_local_direct_session(self, monkeypatch):
        fake = _FakeEMSession([_FakeEMResponse({'data': {'klines': [
            '2025-01-01,100,105,110,95,10000,1000000,5,5,0,1',
        ]}})])
        monkeypatch.setattr('requests.Session', lambda: fake)

        df = _get_board_kline_eastmoney('BK0001', start_date='20250101')
        assert df is not None
        assert list(df.columns) == ['日期', '开盘', '收盘', '最高', '最低', '成交量', '成交额']
        _url, kwargs = fake.get_calls[0]
        assert kwargs['params']['secid'] == '90.BK0001'
        assert fake.trust_env is False
        assert fake.proxies == {}
        assert fake.closed is True

    @patch('data.board_api._pro', None)
    def test_get_board_kline_pro_none(self):
        assert get_board_kline('industry', 'BK0001') is None

    @patch('data.board_api._pro', None)
    def test_get_trade_dates_pro_none(self):
        assert get_trade_dates() == set()


class TestTushareSpotDateSelection:
    """dc_index 多日响应必须只使用最新交易日。"""

    @patch('data.board_api._rate_limit')
    @patch('data.board_api._pro')
    def test_latest_trade_date_wins_and_source_is_preserved(self, mock_pro, mock_rl):
        mock_pro.dc_index.return_value = pd.DataFrame([
            {'ts_code': 'BK0001.DC', 'name': '测试板块', 'trade_date': '20260730',
             'pct_change': 1.0, 'close': 10.0},
            {'ts_code': 'BK0001.DC', 'name': '测试板块', 'trade_date': '20260731',
             'pct_change': 2.0, 'close': 11.0},
        ])

        result = _get_spot('industry')

        assert result['BK0001']['最新价'] == 11.0
        assert result['BK0001']['涨跌幅'] == 2.0
        assert result['BK0001']['trade_date'] == '20260731'
        assert result['BK0001']['source'] == 'tushare_dc_index'

    @patch('data.board_api._rate_limit')
    @patch('data.board_api._pro')
    def test_expected_trade_date_rejects_old_tushare_snapshot(self, mock_pro, mock_rl):
        mock_pro.dc_index.return_value = pd.DataFrame([
            {'ts_code': 'BK0001.DC', 'name': '测试板块', 'trade_date': '20260730',
             'pct_change': 1.0, 'close': 10.0},
        ])

        assert _get_spot('industry', expected_trade_date='20260731') is None


class TestGetBoardKline:
    """板块K线测试"""

    @patch('data.board_api._pro')
    @patch('data.board_api._rate_limit')
    def test_returns_dataframe(self, mock_rl, mock_pro):
        mock_df = pd.DataFrame({
            'trade_date': ['20250101'],
            'open': [100], 'close': [105], 'high': [110], 'low': [95],
            'vol': [10000], 'amount': [1000000],
        })
        mock_pro.dc_daily.return_value = mock_df
        result = get_board_kline('industry', 'BK0001')
        assert result is not None
        assert len(result) == 1
        assert '日期' in result.columns

    @patch('data.board_api._pro')
    @patch('data.board_api._rate_limit')
    def test_empty_returns_none(self, mock_rl, mock_pro):
        mock_pro.dc_daily.return_value = pd.DataFrame()
        assert get_board_kline('industry', 'BK0001') is None

    def test_tushare_refresh_delegates_to_data_loader_factory(self, monkeypatch):
        import data_loader
        import data.board_api as board_api

        client = MagicMock()
        client.dc_daily.return_value = pd.DataFrame({
            'trade_date': ['20250101'],
            'open': [100], 'close': [105], 'high': [110], 'low': [95],
            'vol': [10000], 'amount': [1000000],
        })
        monkeypatch.setattr(board_api, '_pro', None)
        monkeypatch.setattr(
            'core.env_bootstrap.ensure_tushare_token', lambda: True
        )
        monkeypatch.setattr(data_loader, 'get_tushare_pro', lambda: client)

        result = get_board_kline('industry', 'BK0001')
        assert result is not None
        assert len(result) == 1
        assert board_api._pro is client
        client.dc_daily.assert_called_once()
