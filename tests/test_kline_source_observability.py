"""tests/test_kline_source_observability.py — K 线数据源可观测性测试

验证 get_kline 响应中包含数据源可观测性字段：
  - source: 数据来源（cache/sqlite/qmt_http/qmt_xtdata/global/board_loader）
  - stale: 是否旧数据
  - background_refresh_started: 是否已启动后台刷新
  - load_ms: 本次加载耗时（毫秒）
  - fallback_chain: 实际尝试过的数据源列表

不改变 K 线数据排序、周期重采样和现有字段语义。
"""
import os
import sys
import time
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

from services.kline_service import (
    KLineService, _pending, _pending_lock, reset_bg_executor,
)


OBSERVABILITY_FIELDS = (
    'source', 'stale', 'background_refresh_started', 'load_ms',
    'fallback_chain',
)


def _assert_observability_contract(result):
    """Every service response status must expose the fixed metadata shape."""
    for field in OBSERVABILITY_FIELDS:
        assert field in result, f'missing observability field: {field}'
    assert isinstance(result['source'], str) and result['source']
    assert isinstance(result['stale'], bool)
    assert isinstance(result['background_refresh_started'], bool)
    assert isinstance(result['load_ms'], int)
    assert result['load_ms'] >= 0
    assert isinstance(result['fallback_chain'], list)


@pytest.fixture(autouse=True)
def _reset_kline_bg_executor():
    reset_bg_executor()
    yield
    reset_bg_executor()


def _clear_pending():
    with _pending_lock:
        _pending.clear()


def _make_daily_df(n=5):
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": range(n),
        "high": [x + 1 for x in range(n)],
        "low": range(n),
        "close": [x + 0.5 for x in range(n)],
        "volume": [100] * n,
    })


def _make_service(db_has_data=True):
    """构造 KLineService mock，cache 未命中。"""
    svc = KLineService.__new__(KLineService)
    svc._cache = MagicMock()
    svc._cache.get.return_value = None
    svc._db = MagicMock()
    if db_has_data:
        svc._db.read_kline.return_value = _make_daily_df()
    else:
        svc._db.read_kline.return_value = None
    svc._qmt = MagicMock()
    return svc


class TestLoadMs:
    """load_ms 字段测试。"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_cache_hit_includes_load_ms(self):
        """缓存命中时响应包含 load_ms。"""
        svc = _make_service()
        svc._cache.get.return_value = [{'timestamp': 1, 'open': 1, 'high': 1,
                                        'low': 1, 'close': 1, 'volume': 1}]
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert 'load_ms' in result
        assert isinstance(result['load_ms'], int)
        assert result['load_ms'] >= 0

    def test_fresh_load_includes_load_ms(self):
        """新加载时响应包含 load_ms。"""
        svc = _make_service()
        svc._do_load = MagicMock(return_value=_make_daily_df())
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert 'load_ms' in result
        assert isinstance(result['load_ms'], int)
        assert result['load_ms'] >= 0

    def test_stale_response_includes_load_ms(self):
        """cache_first 命中 SQLite 时响应包含 load_ms。"""
        svc = _make_service(db_has_data=True)
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert 'load_ms' in result
        assert isinstance(result['load_ms'], int)

    def test_error_response_includes_load_ms(self):
        """错误响应也包含 load_ms。"""
        svc = _make_service(db_has_data=False)
        import concurrent.futures
        svc._do_load = MagicMock(side_effect=concurrent.futures.TimeoutError())
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=0.1)
        assert status == 408
        assert 'load_ms' in result
        assert isinstance(result['load_ms'], int)


class TestFallbackChain:
    """fallback_chain 字段测试。"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_cache_hit_fallback_chain_is_empty(self):
        """缓存命中时 fallback_chain 为空列表。"""
        svc = _make_service()
        svc._cache.get.return_value = [{'timestamp': 1, 'open': 1, 'high': 1,
                                        'low': 1, 'close': 1, 'volume': 1}]
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert 'fallback_chain' in result
        assert isinstance(result['fallback_chain'], list)
        assert len(result['fallback_chain']) == 0

    def test_fresh_load_includes_fallback_chain(self):
        """新加载时响应包含 fallback_chain。"""
        svc = _make_service()
        svc._do_load = MagicMock(return_value=_make_daily_df())
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert 'fallback_chain' in result
        assert isinstance(result['fallback_chain'], list)

    def test_stale_response_includes_fallback_chain(self):
        """cache_first 命中 SQLite 时响应包含 fallback_chain。"""
        svc = _make_service(db_has_data=True)
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert 'fallback_chain' in result
        assert isinstance(result['fallback_chain'], list)


class TestSourceSpecificity:
    """source 字段在 _load_daily 中应返回具体数据源名称。"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_sqlite_hit_source_is_sqlite(self):
        """SQLite 有数据时 source 应为 'sqlite'。"""
        svc = _make_service(db_has_data=True)
        with patch("services.kline_service._qmt_http_daily",
                   lambda code, count=-1: pd.DataFrame()), \
             patch("services.kline_service.is_qmt_available", lambda: False):
            result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert result['source'] == 'sqlite'

    def test_qmt_http_source_is_qmt_http(self):
        """SQLite 空但 QMT HTTP 有数据时 source 应为 'qmt_http'。"""
        svc = _make_service(db_has_data=False)

        qmt_http_df = _make_daily_df()
        call_count = [0]

        def fake_read(code, period):
            call_count[0] += 1
            if call_count[0] <= 1:
                return None
            return qmt_http_df

        svc._db.read_kline = fake_read
        svc._db.save_kline = MagicMock()

        with patch("services.kline_service._qmt_http_daily",
                   lambda code, count=-1: qmt_http_df), \
             patch("services.kline_service.is_qmt_available", lambda: False):
            result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert result['source'] == 'qmt_http'

    def test_qmt_xtdata_source_is_qmt_xtdata(self):
        """SQLite 和 QMT HTTP 都空但 xtdata 有数据时 source 应为 'qmt_xtdata'。"""
        svc = _make_service(db_has_data=False)

        qmt_df = _make_daily_df()
        call_count = [0]

        def fake_read(code, period):
            call_count[0] += 1
            if call_count[0] <= 1:
                return None
            return qmt_df

        svc._db.read_kline = fake_read
        svc._db.save_kline = MagicMock()
        svc._qmt.get_daily = MagicMock(return_value=qmt_df)
        svc._qmt.to_qmt_code = lambda code, data_type: f"{code}.SH"

        with patch("services.kline_service._qmt_http_daily",
                   lambda code, count=-1: pd.DataFrame()), \
             patch("services.kline_service.is_qmt_available", lambda: True):
            result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert result['source'] == 'qmt_xtdata'

    def test_board_source_is_board_loader(self):
        """板块日线 source 应为 'board_loader'。"""
        svc = _make_service(db_has_data=False)

        board_df = _make_daily_df()
        svc._db.read_kline.return_value = None

        with patch("services.kline_service.load_board_kline",
                   lambda *a, **kw: board_df), \
             patch("services.kline_service.is_qmt_available", lambda: False):
            result, status = svc.get_kline('industry', 'BK1158', 'daily',
                                           board_name='半导体', timeout=5)
        assert status == 200
        assert result['source'] == 'board_loader'

    def test_fallback_chain_records_attempted_sources(self):
        """fallback_chain 应记录所有尝试过的数据源。"""
        svc = _make_service(db_has_data=False)
        svc._db.read_kline.return_value = None

        with patch("services.kline_service._qmt_http_daily",
                   lambda code, count=-1: pd.DataFrame()), \
             patch("services.kline_service.is_qmt_available", lambda: False):
            result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)

        # 所有源都失败 → 200 with empty data (not exception)
        assert status == 200
        assert result['count'] == 0
        assert 'fallback_chain' in result
        chain = result['fallback_chain']
        # 应该尝试过 sqlite 和 qmt_http
        assert 'sqlite' in chain
        assert 'qmt_http' in chain


class TestExistingFieldsPreserved:
    """现有字段不被破坏。"""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_cache_hit_still_has_cached_field(self):
        """缓存命中时 cached 字段仍为 True。"""
        svc = _make_service()
        svc._cache.get.return_value = [{'timestamp': 1, 'open': 1, 'high': 1,
                                        'low': 1, 'close': 1, 'volume': 1}]
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert result['cached'] is True
        assert result['source'] == 'cache'

    def test_stale_still_has_stale_and_bg_refresh(self):
        """cache_first 命中时 stale 和 background_refresh_started 仍存在。"""
        svc = _make_service(db_has_data=True)
        result, status = svc.get_kline('stock', '600519', 'daily',
                                       cache_first=True, timeout=5)
        assert status == 200
        assert result['stale'] is True
        assert 'background_refresh_started' in result

    def test_data_and_count_preserved(self):
        """data 和 count 字段不受影响。"""
        svc = _make_service()
        svc._do_load = MagicMock(return_value=_make_daily_df(3))
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert result['count'] == 3
        assert len(result['data']) == 3
        assert 'timestamp' in result['data'][0]
        assert 'close' in result['data'][0]

    def test_last_date_and_today_preserved(self):
        """last_date 和 today 字段不受影响。"""
        svc = _make_service()
        svc._do_load = MagicMock(return_value=_make_daily_df())
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=5)
        assert status == 200
        assert 'last_date' in result
        assert 'today' in result
        assert result['last_date']  # 非空


class TestObservabilityResponseMatrix:
    """The metadata contract is present on every service status branch."""

    def setup_method(self):
        _clear_pending()

    def teardown_method(self):
        _clear_pending()

    def test_deprecated_empty_200_contract(self):
        svc = _make_service()
        result, status = svc.get_kline('index', 'sh000938', 'daily', timeout=5)
        assert status == 200
        assert result['reason'] == 'deprecated_no_remote'
        _assert_observability_contract(result)
        assert result['stale'] is False
        assert result['background_refresh_started'] is False

    def test_loading_202_contract(self):
        svc = _make_service(db_has_data=False)
        from services.kline_service import _claim_pending
        _claim_pending('stock:600519:daily')
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=0.001)
        assert status == 202
        _assert_observability_contract(result)
        assert result['source'] == 'pending'

    def test_cache_stale_200_is_marked_stale(self):
        svc = _make_service()
        cached = [{'timestamp': 1, 'open': 1, 'high': 1,
                   'low': 1, 'close': 1, 'volume': 1}]
        svc._cache.get.return_value = cached
        svc._do_load = MagicMock(side_effect=TimeoutError('timeout'))
        # Seed the cache only for the error fallback, then force the loader.
        svc._cache.get.side_effect = [None, cached]
        result, status = svc.get_kline('stock', '600519', 'daily', timeout=0.1)
        assert status == 200
        _assert_observability_contract(result)
        assert result['source'] == 'cache_stale'
        assert result['stale'] is True

    def test_invalid_timeout_contract(self):
        svc = _make_service()
        result, status = svc.get_kline('stock', '600519', 'daily', timeout='bad')
        assert status == 400
        _assert_observability_contract(result)
        assert result['source'] == 'invalid_request'

    def test_ok_response_defaults_and_cache_stale_inference(self):
        svc = _make_service()
        result = svc._ok_response([], 'stock:600519:daily')
        _assert_observability_contract(result)
        assert result['stale'] is False
        assert result['background_refresh_started'] is False
        assert result['source'] == 'load'

        stale = svc._ok_response([], 'stock:600519:daily', source='cache_stale')
        assert stale['stale'] is True
