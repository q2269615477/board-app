"""kline_service 接入交易所日历（services.exchange_calendar_service）

覆盖：
- A股休市而港股开市时，按各自市场日历判断目标交易日；
- 美国本地日期/DST（UTC 时间 → America/New_York，EDT 生效）；
- HK 欠更绝不按 A 股交易日判断，且绝不误走 QMT 补全；
- 海外指数（纳指/日经等）只允许 global history fetch；
- 日历服务缺失时的回退行为。
"""
import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from services.kline_service import KLineService, _kline_market_key


def _svc():
    svc = KLineService.__new__(KLineService)
    svc._db = MagicMock()
    svc._cache = MagicMock()
    svc._qmt = MagicMock()
    return svc


def _frame(*dates):
    return pd.DataFrame([
        {'date': d, 'open': 1.0, 'high': 2.0, 'low': 0.5,
         'close': 1.5, 'volume': 1}
        for d in dates
    ])


def _calendar(impl):
    """Patch the exchange-calendar seam with a controllable implementation."""
    return patch('services.kline_service._exchange_calendar_fn',
                 return_value=impl)


class TestMarketKey:
    def test_market_key_mapping(self):
        assert _kline_market_key('stock', '600000') == 'a_share'
        assert _kline_market_key('index', 'sh000001') == 'a_share'
        assert _kline_market_key('hk_index', 'HSI') == 'hong_kong'
        assert _kline_market_key('global_index', 'HSTECH') == 'hong_kong'
        assert _kline_market_key('us', 'IXIC') == 'us'
        assert _kline_market_key('us', 'SPX') == 'us'
        assert _kline_market_key('global_index', '^N225') == 'japan'
        assert _kline_market_key('global_index', '^KS11') == 'south_korea'


class TestRoutingByExchangeCalendar:
    def test_a_share_closed_hk_open_each_uses_own_market_date(self):
        # 2026-10-01（周四，国庆）：A股休市、港股开市。
        now = datetime.datetime(2026, 10, 1, 10, 30,
                                tzinfo=ZoneInfo('Asia/Shanghai'))
        calls = []

        def calendar(code_or_market, now=None, data_type=None):
            calls.append((str(code_or_market), now, data_type))
            if str(code_or_market).upper() in ('HSI', 'HSTECH') \
                    or data_type == 'hk_index':
                return '2026-10-01'  # 香港当天开市
            return '2026-09-30'      # A股国庆休市，上一交易日 9/30

        svc = _svc()
        hk_stale = _frame('2026-09-30')   # 港股最新 bar 停在 9/30，按港历欠更
        a_fresh = _frame('2026-09-30')    # A股本地 9/30 即最近交易日，不缺

        with _calendar(calendar), \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for HK/overseas symbols')), \
                patch('services.kline_service.load_global_index_kline',
                      return_value=pd.DataFrame()) as global_load, \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share calendar must not drive HK freshness')), \
                patch('services.kline_service._should_refresh_last_bar',
                      return_value=False):
            out_hk = svc._ensure_latest_kline_bar(
                'hk_index', 'HSI', 'daily', hk_stale, now=now)
            out_a = svc._ensure_latest_kline_bar(
                'stock', '600000', 'daily', a_fresh, now=now)

        # HK 目标 10/1（开市）→ 9/30 欠更 → 走 global history fetch
        global_load.assert_called_once_with('HSI', 'daily')
        pd.testing.assert_frame_equal(out_hk, hk_stale)
        # A股目标 9/30（休市）→ 本地已最新 → 不再补全
        pd.testing.assert_frame_equal(out_a, a_fresh)
        assert ('HSI', now, 'hk_index') in calls
        assert ('600000', now, 'stock') in calls

    def test_hk_fresh_bar_needs_no_supplement_and_no_qmt(self):
        # 旧逻辑对已对齐的 hk_index 也会调用 _fetch_stock_supplement(QMT)。
        now = datetime.datetime(2026, 7, 27, 9, 30,
                                tzinfo=ZoneInfo('Asia/Hong_Kong'))

        def calendar(code_or_market, now=None, data_type=None):
            return '2026-07-27'

        svc = _svc()
        fresh = _frame('2026-07-27')
        with _calendar(calendar), \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for HK symbols')), \
                patch('services.kline_service.load_global_index_kline',
                      side_effect=AssertionError(
                          'fresh HK bar must not trigger a fetch')) as global_load, \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share calendar must not drive HK freshness')):
            out = svc._ensure_latest_kline_bar(
                'hk_index', 'HSTECH', 'daily', fresh, now=now)

        global_load.assert_not_called()
        pd.testing.assert_frame_equal(out, fresh)

    def test_us_local_date_with_dst_is_used_not_shanghai_or_qmt(self):
        # 2026-03-08 23:30 UTC：纽约已进入 EDT(UTC-4)，本地为周日 19:30；
        # 上海已是周一 03-09 07:30。最近美股交易日为周五 03-06。
        now = datetime.datetime(2026, 3, 8, 23, 30,
                                tzinfo=datetime.timezone.utc)
        calls = []

        def calendar(code_or_market, now=None, data_type=None):
            calls.append((str(code_or_market), now, data_type))
            local = now.astimezone(ZoneInfo('America/New_York'))
            assert local.utcoffset() == datetime.timedelta(hours=-4)  # EDT
            assert local.date() == datetime.date(2026, 3, 8)          # 本地周日
            return '2026-03-06'  # 最近美股交易日（周五）

        svc = _svc()
        fresh = _frame('2026-03-06')
        with _calendar(calendar), \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for US indices')), \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share calendar must not drive US freshness')):
            out = svc._ensure_latest_kline_bar(
                'us', 'IXIC', 'daily', fresh, now=now)

        pd.testing.assert_frame_equal(out, fresh)
        assert ('IXIC', now, 'us') in calls

    def test_us_and_nikkei_stale_use_global_history_only(self):
        # 欠更的美股/日经只允许 global history fetch，绝不误走 QMT。
        now = datetime.datetime(2026, 3, 8, 23, 30,
                                tzinfo=datetime.timezone.utc)

        def calendar(code_or_market, now=None, data_type=None):
            return '2026-03-06'

        remote = _frame('2026-03-05', '2026-03-06')
        svc = _svc()
        with _calendar(calendar), \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for overseas indices')), \
                patch('services.kline_service.load_global_index_kline',
                      return_value=remote) as global_load, \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share calendar must not drive overseas freshness')):
            out = svc._ensure_latest_kline_bar(
                'global_index', '^N225', 'daily',
                _frame('2026-03-05'), now=now)

        global_load.assert_called_once_with('^N225', 'daily')
        assert str(out['date'].max())[:10] == '2026-03-06'

    def test_hk_stale_never_judged_by_a_share_date(self):
        # 2026-07-27（周一）：A股最近交易日为 07-24（周五），港股周一开市。
        # 若误用 A股日期，07-24 的 HK bar 会被当作“最新”而漏补。
        now = datetime.datetime(2026, 7, 27, 9, 30,
                                tzinfo=ZoneInfo('Asia/Shanghai'))
        calls = []

        def calendar(code_or_market, now=None, data_type=None):
            calls.append((str(code_or_market), data_type))
            if str(code_or_market).upper() in ('HSI', 'HSTECH') \
                    or data_type == 'hk_index':
                return '2026-07-27'
            return '2026-07-24'

        remote = _frame('2026-07-24', '2026-07-27')
        svc = _svc()
        with _calendar(calendar), \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for HK indices')), \
                patch('services.kline_service.load_global_index_kline',
                      return_value=remote) as global_load, \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share date must not drive HK freshness')):
            out = svc._ensure_latest_kline_bar(
                'hk_index', 'HSTECH', 'daily', _frame('2026-07-24'), now=now)

        global_load.assert_called_once_with('HSTECH', 'daily')
        assert str(out['date'].max())[:10] == '2026-07-27'
        assert ('HSTECH', 'hk_index') in calls


class TestCalendarFallback:
    def test_hk_fallback_uses_hk_weekday_not_a_share_calendar(self):
        # 日历服务缺失时：HK 走本地工作日回退，仍不调用 A 股日历/QMT。
        now = datetime.datetime(2026, 7, 27, 9, 30,
                                tzinfo=ZoneInfo('Asia/Hong_Kong'))
        svc = _svc()
        with patch('services.kline_service._exchange_calendar_fn',
                   return_value=None), \
                patch('services.kline_service.load_global_index_kline',
                      return_value=pd.DataFrame()) as global_load, \
                patch('services.kline_service._qmt_http_daily',
                      side_effect=AssertionError(
                          'QMT must not run for HK symbols')), \
                patch('data.board_api.get_last_trading_date',
                      side_effect=AssertionError(
                          'A-share calendar must not drive HK freshness')):
            out = svc._ensure_latest_kline_bar(
                'hk_index', 'HSI', 'daily', _frame('2026-07-24'), now=now)

        global_load.assert_called_once_with('HSI', 'daily')
        pd.testing.assert_frame_equal(out, _frame('2026-07-24'))

    def test_a_share_fallback_keeps_existing_calendar_behavior(self):
        now = datetime.datetime(2026, 10, 1, 10, 30,
                                tzinfo=ZoneInfo('Asia/Shanghai'))
        svc = _svc()
        with patch('services.kline_service._exchange_calendar_fn',
                   return_value=None), \
                patch('data.board_api.get_last_trading_date',
                      return_value='2026-09-30') as a_cal, \
                patch('services.kline_service._should_refresh_last_bar',
                      return_value=False):
            out = svc._ensure_latest_kline_bar(
                'stock', '600000', 'daily', _frame('2026-09-30'), now=now)

        a_cal.assert_called_once_with('2026-10-01')
        pd.testing.assert_frame_equal(out, _frame('2026-09-30'))
