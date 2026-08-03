"""test_kline_last_bar_refresh.py — 日K 末 bar 半成品校正

场景：本地已有「最近交易日」bar，但是盘中 QMT 半成品（close/high 偏），
旧逻辑因 last_local == target 不再补洞，图表长期显示错误收盘价。
"""
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from services.kline_service import (
    KLineService,
    _last_bar_differs,
    _should_refresh_last_bar,
    _mark_last_bar_refreshed,
    _last_bar_refresh_ts,
    _LAST_BAR_REFRESH_TTL,
)


class TestLastBarDiffers:
    def test_same_ohlc_no_diff(self):
        local = pd.DataFrame([
            {'date': '2026-07-27', 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 10},
        ])
        supp = pd.DataFrame([
            {'date': '2026-07-27', 'open': 1, 'high': 2, 'low': 0.5, 'close': 1.5, 'volume': 10},
        ])
        assert _last_bar_differs(local, supp) is False

    def test_close_diff_detected(self):
        local = pd.DataFrame([
            {'date': '2026-07-27', 'open': 3808.9, 'high': 3841.49, 'low': 3793.45,
             'close': 3829.39, 'volume': 1},
        ])
        supp = pd.DataFrame([
            {'date': '2026-07-27', 'open': 3808.9, 'high': 3858.31, 'low': 3793.45,
             'close': 3858.25, 'volume': 1},
        ])
        assert _last_bar_differs(local, supp) is True


class TestRefreshThrottle:
    def setup_method(self):
        _last_bar_refresh_ts.clear()

    def test_first_time_allows(self):
        assert _should_refresh_last_bar('sh000001') is True

    def test_within_ttl_blocks(self):
        _mark_last_bar_refreshed('sh000001')
        assert _should_refresh_last_bar('sh000001') is False

    def test_after_ttl_allows(self):
        _last_bar_refresh_ts['sh000001'] = 0.0  # epoch
        assert _should_refresh_last_bar('sh000001') is True


class TestDoLoadRefreshesStaleLastBar:
    """_do_load：末 bar 日期已对齐时仍应用权威源覆盖错误 close"""

    def setup_method(self):
        _last_bar_refresh_ts.clear()

    def test_overwrites_wrong_close_when_date_matches(self):
        local = pd.DataFrame([
            {'date': '2026-07-24', 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1},
            {'date': '2026-07-27', 'open': 3808.9, 'high': 3841.49, 'low': 3793.45,
             'close': 3829.39, 'volume': 100},
        ])
        supp = pd.DataFrame([
            {'date': '2026-07-24', 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1},
            {'date': '2026-07-27', 'open': 3808.9, 'high': 3858.31, 'low': 3793.45,
             'close': 3858.25, 'volume': 200},
        ])
        fixed = pd.DataFrame([
            {'date': '2026-07-24', 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1},
            {'date': '2026-07-27', 'open': 3808.9, 'high': 3858.31, 'low': 3793.45,
             'close': 3858.25, 'volume': 200},
        ])

        svc = KLineService.__new__(KLineService)
        svc._db = MagicMock()
        svc._db.read_kline.side_effect = [local, fixed]
        svc._cache = MagicMock()
        svc._qmt = MagicMock()

        with patch('services.kline_service._fetch_stock_supplement', return_value=supp), \
             patch('services.kline_service._should_refresh_history', return_value=False), \
             patch('services.kline_service._exchange_calendar_fn',
                   return_value=lambda code, now=None, data_type=None: '2026-07-27'), \
             patch('services.kline_service._qmt_http_ohlc', return_value={}), \
             patch('data.board_api.get_last_trading_date', return_value='2026-07-27'):
            out = svc._do_load('stock', '600000', 'daily', '', 'stock:600000:daily')

        svc._db.save_kline.assert_called_once()
        assert abs(float(out.iloc[-1]['close']) - 3858.25) < 0.01


def test_eastmoney_all_a_bypasses_generic_sqlite_qmt_chain():
    remote = pd.DataFrame([{
        'date': '2026-07-31', 'open': 6352.96, 'high': 6398.74,
        'low': 6345.24, 'close': 6350.92, 'volume': 100,
    }])
    svc = KLineService.__new__(KLineService)
    svc._db = MagicMock()

    with patch('services.kline_service.load_global_index_kline', return_value=remote) as load:
        out = svc._load_daily('index', '800000', '东方财富全A')

    load.assert_called_once_with('800000', 'daily')
    svc._db.read_kline.assert_not_called()
    assert out.attrs['source'] == 'global'
    assert out.iloc[-1]['close'] == pytest.approx(6350.92)
