"""test_kline_cache_priority.py — K线缓存优先/cache_first 行为测试"""
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

from services.kline_service import (
    KLineService, _pending, _pending_lock, reset_bg_executor, _get_bg_executor,
)


@pytest.fixture(autouse=True)
def _reset_kline_bg_executor():
    """每个测试前重置后台线程池，避免跨测试污染。"""
    reset_bg_executor()
    yield
    reset_bg_executor()


def _make_daily_df(n=5):
    """构造测试用日线 DataFrame"""
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": range(n),
        "high": [x + 1 for x in range(n)],
        "low": range(n),
        "close": [x + 0.5 for x in range(n)],
        "volume": [100] * n,
    })


def _clear_pending():
    """清理模块级 _pending 状态，避免测试间互相干扰"""
    with _pending_lock:
        _pending.clear()


class TestKLineServiceCachePriority:
    """KLineService 缓存优先 / cache_first 行为"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def _make_service(self, db_has_data=True):
        """构造一个 KLineService，mock 掉 db/cache/qmt"""
        svc = KLineService.__new__(KLineService)
        svc._cache = MagicMock()
        svc._cache.get.return_value = None  # L1 内存缓存未命中
        svc._db = MagicMock()
        if db_has_data:
            svc._db.read_kline.return_value = _make_daily_df()
        else:
            svc._db.read_kline.return_value = None
        svc._qmt = MagicMock()
        return svc

    def test_prefer_cache_returns_sqlite_data_immediately(self):
        """cache_first=True 时，SQLite 有数据应立即返回，source=sqlite, stale=True"""
        svc = self._make_service(db_has_data=True)
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert result['source'] == 'sqlite'
        assert result['stale'] is True
        assert result['count'] == 5
        assert result['data'][0]['timestamp'] > 0

    def test_stale_ok_equivalent_to_prefer_cache(self):
        """cache_first=True 等效于旧行为"""
        svc = self._make_service(db_has_data=True)
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert result['source'] == 'sqlite'
        assert result['stale'] is True

    def test_prefer_cache_no_sqlite_data_falls_through(self):
        """cache_first=True 但 SQLite 无数据时，走正常加载流程"""
        svc = self._make_service(db_has_data=False)
        # mock _do_load 返回空
        svc._do_load = MagicMock(return_value=pd.DataFrame())
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=2)
        # 无数据可返回，走正常流程（可能 408/500）
        assert status in (200, 408, 500)

    def test_prefer_cache_triggers_background_refresh(self):
        """cache_first=True 时，应触发后台刷新"""
        svc = self._make_service(db_has_data=True)
        # 模拟后台线程池提交（与同步 _executor 隔离）
        with patch('services.kline_service._bg_executor') as mock_exec:
            mock_future = MagicMock()
            mock_exec.submit.return_value = mock_future
            result, status = svc.get_kline('stock', '600519', 'daily',
                                           cache_first=True,
                                           timeout=5)
            # 后台刷新应被提交
            assert result['background_refresh_started'] is True
            mock_exec.submit.assert_called_once()

    def test_background_refresh_dedup(self):
        """同一 cache_key 不应重复提交后台刷新"""
        svc = self._make_service(db_has_data=True)
        with patch('services.kline_service._bg_executor') as mock_exec:
            mock_future = MagicMock()
            mock_exec.submit.return_value = mock_future
            # 第一次
            r1, _ = svc.get_kline('stock', '600519', 'daily',
                                  cache_first=True, timeout=5)
            # 第二次（模拟并发，pending 中已有该 key）
            r2, _ = svc.get_kline('stock', '600519', 'daily',
                                  cache_first=True, timeout=5)
            # 只提交一次
            assert mock_exec.submit.call_count == 1

    def test_default_behavior_unchanged_without_params(self):
        """默认参数（无 prefer_cache/stale_ok）走原有流程，不读 SQLite"""
        svc = self._make_service(db_has_data=True)
        # mock _do_load 返回数据
        svc._do_load = MagicMock(return_value=_make_daily_df())
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       timeout=2)
        # 默认行为：走 _do_load 路径，source 不是 sqlite
        assert result['source'] != 'sqlite'
        assert not result.get('stale')

    def test_l1_cache_hit_takes_priority_over_stale(self):
        """L1 内存缓存命中时，直接返回 cache，不走 stale 路径"""
        svc = self._make_service(db_has_data=True)
        fake_data = [{'timestamp': 1704153600000, 'open': 1, 'high': 2, 'low': 0.5,
                      'close': 1.5, 'volume': 100}]
        svc._cache.get.return_value = fake_data
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert result['source'] == 'cache'
        assert result['cached'] is True
        assert not result.get('stale')

    def test_timeout_returns_408_on_failure(self):
        """超时且无 stale 数据时返回 408"""
        svc = self._make_service(db_has_data=False)
        # mock future.result 抛出 TimeoutError
        import concurrent.futures
        svc._do_load = MagicMock(side_effect=concurrent.futures.TimeoutError())
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=0.1)
        assert status == 408
        assert result['timeout'] is True

    def test_timeout_with_stale_ok_returns_sqlite(self):
        """超时 + cache_first 时，即使超时也返回 SQLite 旧数据"""
        svc = self._make_service(db_has_data=True)
        import concurrent.futures
        svc._do_load = MagicMock(side_effect=concurrent.futures.TimeoutError())
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=0.1)
        assert status == 200
        assert result['source'] == 'sqlite'
        assert result['stale'] is True


class TestBackgroundRefreshNoPendingLeak:
    """回归：后台刷新必须写缓存并清理 _pending。

    历史 bug：_submit_background_refresh 直接 submit(self._do_load)，而 _do_load
    既不写缓存也不清 _pending / set event（cache_key 参数根本没被使用）。同步路径
    由 get_kline 的 future.result() 善后，后台路径无人善后 → _pending 永久残留 →
    此后同一 cache_key 的请求全部落入「正在加载中」分支，等满 10s 返回 202
    loading，前端拿到空数组保持旧图，直到进程重启。表现为「图表长期显示旧交易日
    数据」，与 QMT/Tushare 是否可用无关。
    """

    KEY = 'stock:600519:daily'

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def _svc(self):
        svc = KLineService.__new__(KLineService)
        svc._cache = MagicMock()
        svc._cache.get.return_value = None
        svc._db = MagicMock()
        svc._db.read_kline.return_value = _make_daily_df()
        svc._qmt = MagicMock()
        return svc

    def _wait_bg(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with _pending_lock:
                if self.KEY not in _pending:
                    return True
            time.sleep(0.05)
        return False

    def test_background_refresh_clears_pending_and_sets_cache(self):
        svc = self._svc()
        with patch.object(svc, '_do_load', return_value=_make_daily_df(7)) as m:
            result, status = svc.get_kline('stock', '600519', 'daily',
                                           cache_first=True, timeout=5)
            assert status == 200
            assert result.get('background_refresh_started') is True
            assert self._wait_bg(), "_pending 未被后台任务清理（泄漏）"
            assert m.called
            svc._cache.set.assert_called()  # 刷新结果必须进缓存，否则等于白刷

    def test_second_request_not_stuck_in_loading(self):
        svc = self._svc()
        with patch.object(svc, '_do_load', return_value=_make_daily_df(7)):
            svc.get_kline('stock', '600519', 'daily', cache_first=True, timeout=5)
            assert self._wait_bg(), "_pending 未被清理"
            result2, status2 = svc.get_kline('stock', '600519', 'daily',
                                             cache_first=True, timeout=5)
        assert status2 == 200, f"第二次请求被卡在 loading: {result2}"
        assert not result2.get('loading')

    def test_background_failure_still_clears_pending(self):
        """后台任务抛异常也必须清理 _pending，否则同样永久卡住"""
        svc = self._svc()
        with patch.object(svc, '_do_load', side_effect=RuntimeError('boom')):
            svc.get_kline('stock', '600519', 'daily', cache_first=True, timeout=5)
            assert self._wait_bg(), "异常路径未清理 _pending"


class TestMarketCodeSplit:
    """回归：带市场前缀的指数代码（sh000001）必须被正确解析。

    历史 bug：_fetch_stock_supplement 只按裸码首位判前缀，
    'sh000001' 既不以 83/87/88/92/43 开头也不以 6/90/5 开头 →
    落到 else 拼成 'szsh000001'，东财 secid 拼成 '0.sh000001' →
    **指数的 HTTP 补齐永远返回空**（个股正常）。这是指数长期停在
    旧交易日、且行业指数无法入库的根因。
    """

    def test_prefixed_index_codes(self):
        from services.kline_service import _split_market_code
        assert _split_market_code('sh000001') == ('sh000001', '000001', 'sh')
        assert _split_market_code('sz399006') == ('sz399006', '399006', 'sz')
        assert _split_market_code('sh000933') == ('sh000933', '000933', 'sh')

    def test_bare_stock_codes_unchanged(self):
        from services.kline_service import _split_market_code
        assert _split_market_code('600519') == ('sh600519', '600519', 'sh')
        assert _split_market_code('000001') == ('sz000001', '000001', 'sz')
        assert _split_market_code('300760') == ('sz300760', '300760', 'sz')
        assert _split_market_code('920735') == ('bj920735', '920735', 'bj')
        assert _split_market_code('688981') == ('sh688981', '688981', 'sh')

    def test_never_double_prefixes(self):
        from services.kline_service import _split_market_code
        for code in ('sh000001', 'sz399006', 'bj920735'):
            sym, bare, mkt = _split_market_code(code)
            assert sym == code, f"{code} 被二次加前缀: {sym}"
            assert not bare.startswith(('sh', 'sz', 'bj'))
