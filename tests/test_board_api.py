"""test_board_api.py — 板块 API 测试"""
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

from data.board_api import (
    _date_fmt, _clean_stock_name, _rate_limit,
    get_industry_boards, get_concept_boards,
    get_board_kline, get_trade_dates,
    _get_constituents,
)


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

    @patch('data.board_api._pro', None)
    def test_get_board_kline_pro_none(self):
        assert get_board_kline('industry', 'BK0001') is None

    @patch('data.board_api._pro', None)
    def test_get_trade_dates_pro_none(self):
        assert get_trade_dates() == set()


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
