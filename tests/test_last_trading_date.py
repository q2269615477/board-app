"""最近交易日判定：周末/缓存/批处理目标日"""
from datetime import date
from unittest.mock import patch

from data.board_api import get_last_trading_date


class TestGetLastTradingDate:
    def test_weekend_fallback_skips_sat_sun(self):
        # 无 trade_cal 时：周日 → 周五
        with patch('data.board_api.get_trade_dates', return_value=set()):
            with patch('data.board_api._last_trade_date_cache', None):
                with patch('data.board_api._last_trade_date_cached_at', 0.0):
                    assert get_last_trading_date('2026-07-26') == '2026-07-24'

    def test_uses_trade_cal_when_available(self):
        cal = {'2026-07-23', '2026-07-24', '2026-07-27'}
        with patch('data.board_api.get_trade_dates', return_value=cal):
            with patch('data.board_api._last_trade_date_cache', None):
                with patch('data.board_api._last_trade_date_cached_at', 0.0):
                    assert get_last_trading_date('2026-07-26') == '2026-07-24'
                    assert get_last_trading_date('2026-07-24') == '2026-07-24'
                    assert get_last_trading_date('2026-07-23') == '2026-07-23'

    def test_holiday_monday_uses_prior_open(self):
        # 假设周一休市，日历只有到上周五
        cal = {'2026-07-23', '2026-07-24'}
        with patch('data.board_api.get_trade_dates', return_value=cal):
            with patch('data.board_api._last_trade_date_cache', None):
                with patch('data.board_api._last_trade_date_cached_at', 0.0):
                    assert get_last_trading_date('2026-07-27') == '2026-07-24'
