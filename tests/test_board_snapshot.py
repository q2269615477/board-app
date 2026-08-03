"""
tests/test_board_snapshot.py — BoardSnapshotCache 单元测试

使用 monkeypatch 模拟 requests.Session.get 返回构造的东财 clist JSON，
覆盖 capture_all / ensure_snapshot 逻辑。
覆盖午休冻结、盘中实时和动态分页行为。
"""
import sys
import os
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

# 确保项目根在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# 测试环境变量
os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')

import pytest
from services.board_snapshot import (
    BoardSnapshotCache,
    _is_a_share_session,
    _safe_float,
    get_snapshot_cache,
)


# ===== 模拟东财 push2delay 返回的 clist JSON =====

def _make_clist_response(codes: list, page: int = 1, page_size: int = 100, total_pages: int = 1):
    """构造东财 clist get 的响应 JSON"""
    diff = []
    for code in codes:
        diff.append({
            'f12': code,
            'f14': f'板块{code}',
            'f2': 1234.56,     # 最新价
            'f3': 1.23,        # 涨跌幅
            'f4': 15.67,       # 涨跌额
            'f5': 100000,      # 成交量(手)
            'f6': 500000000,   # 成交额(元)
            'f7': 2.5,         # 振幅
            'f8': 3.14,        # 换手
            'f15': 1250.00,    # 最高
            'f16': 1200.00,    # 最低
            'f17': 1210.00,    # 今开
            'f18': 1220.00,    # 昨收
        })
    return {
        'data': {
            'diff': diff,
            'total': len(codes) * total_pages,
        },
        'rc': 0,
        'rt': 1,
        'svr': 1,
    }


def _make_empty_clist_response():
    """空数据响应"""
    return {'data': {'diff': [], 'total': 0}, 'rc': 0, 'rt': 1, 'svr': 1}


class MockResponse:
    def __init__(self, json_data, status_code=200, text=None):
        self._json = json_data
        self.status_code = status_code
        self.text = text or str(json_data)

    def json(self):
        return self._json


class MockSession:
    """模拟 requests.Session"""
    def __init__(self):
        self.get_calls = []
        self.get_responses = []
        self._response_iter = iter(self.get_responses)
        self.proxies = {}
        self.trust_env = True

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        try:
            return next(self._response_iter)
        except StopIteration:
            return MockResponse(_make_empty_clist_response())

    def close(self):
        pass


# ===== Fixtures =====

@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前重置 BoardSnapshotCache 单例"""
    BoardSnapshotCache._instance = None
    BoardSnapshotCache._initialized = False
    yield
    BoardSnapshotCache._instance = None
    BoardSnapshotCache._initialized = False


@pytest.fixture
def mock_session():
    """准备 mock Session"""
    session = MockSession()
    return session


# ===== 测试 _safe_float =====

class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_valid_float(self):
        assert _safe_float('3.14') == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_invalid_string(self):
        assert _safe_float('abc') == 0.0

    def test_empty_string(self):
        assert _safe_float('') == 0.0

    def test_zero(self):
        assert _safe_float(0) == 0.0


# ===== 测试 _is_a_share_session / _is_morning_close_window =====

class TestSessionDetection:
    @patch('services.board_snapshot.datetime')
    def test_weekday_in_session(self, mock_dt):
        # 周一 10:00
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)
        assert _is_a_share_session() is True

    @patch('services.board_snapshot.datetime')
    def test_weekday_before_open(self, mock_dt):
        # 周二 09:00
        mock_dt.now.return_value = datetime(2026, 7, 28, 9, 0, 0)
        assert _is_a_share_session() is False

    @patch('services.board_snapshot.datetime')
    def test_weekday_after_close(self, mock_dt):
        # 周三 15:06
        mock_dt.now.return_value = datetime(2026, 7, 29, 15, 6, 0)
        assert _is_a_share_session() is False

    @patch('services.board_snapshot.datetime')
    def test_weekend(self, mock_dt):
        # 周六 10:00
        mock_dt.now.return_value = datetime(2026, 7, 25, 10, 0, 0)
        assert _is_a_share_session() is False


# ===== 测试 capture_all =====

class TestCaptureAll:
    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_capture_concept(self, mock_session_cls, mock_dt):
        """capture_all('concept') 应返回 ≥1，字段齐全，open/high/low/close 不为 None"""
        # 模拟时间：周一 10:00（盘中）
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0800', 'BK0900'])),
            MockResponse(_make_empty_clist_response()),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        n = cache.capture_all('concept')
        assert n >= 1

        # 验证行数据完整性
        row = cache.get_board_today('concept', 'BK0800')
        assert row is not None
        assert row['code'] == 'BK0800'
        assert row['name'] == '板块BK0800'
        assert row['open'] is not None and row['open'] != 0
        assert row['high'] is not None and row['high'] != 0
        assert row['low'] is not None and row['low'] != 0
        assert row['close'] is not None and row['close'] != 0
        assert row['pre_close'] is not None
        assert row['pct'] is not None
        assert row['volume'] is not None
        assert row['amount'] is not None
        assert row['channel'] == 'eastmoney_push2delay'
        assert 'ts' in row
        assert 'trade_date' in row

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_capture_industry(self, mock_session_cls, mock_dt):
        """capture_all('industry') 正常拉取"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001', 'BK0002'])),
            MockResponse(_make_empty_clist_response()),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        n = cache.capture_all('industry')
        assert n == 2

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_capture_uses_api_total_for_pagination(self, mock_session_cls, mock_dt):
        """分页必须按接口 total 终止，不能依赖固定页数。"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        pages = []
        for codes in (['BK0001', 'BK0002'], ['BK0003', 'BK0004'], ['BK0005']):
            payload = _make_clist_response(codes)
            payload['data']['total'] = 5
            pages.append(MockResponse(payload))
        mock_sess = MockSession()
        mock_sess.get_responses = pages
        mock_sess._response_iter = iter(pages)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        assert cache.capture_all('industry') == 5
        assert len(mock_sess.get_calls) == 3
        assert len(cache.get_all('industry')) == 5

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_incomplete_total_is_not_accepted_as_success(self, mock_session_cls, mock_dt):
        """接口声称还有数据但分页中断时，不得静默保存部分结果。"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)
        first = _make_clist_response(['BK0001'])
        first['data']['total'] = 3
        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(first),
            MockResponse(_make_empty_clist_response()),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        assert cache.capture_all('industry') == 0
        assert cache.get_all('industry') == {}

    @patch('requests.Session', create=True)
    def test_capture_api_failure(self, mock_session_cls):
        """API 失败时返回 0"""
        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse({}, status_code=500, text='error'),
            MockResponse(_make_empty_clist_response()),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        n = cache.capture_all('concept')
        assert n == 0

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_none_and_invalid_values(self, mock_session_cls, mock_dt):
        """验证 None / 非数字值转为 0"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        bad_response = {
            'data': {
                'diff': [{
                    'f12': 'BK9999',
                    'f14': 'BadBoard',
                    'f2': None,       # 最新价 None
                    'f3': 'invalid',  # 涨跌幅非数字
                    'f4': None,
                    'f5': None,
                    'f6': None,
                    'f7': None,
                    'f8': None,
                    'f15': None,      # 最高 None
                    'f16': None,      # 最低 None
                    'f17': None,      # 今开 None
                    'f18': None,      # 昨收 None
                }],
                'total': 1,
            },
            'rc': 0,
        }
        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(bad_response),
            MockResponse(_make_empty_clist_response()),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        n = cache.capture_all('concept')
        assert n == 1

        row = cache.get_board_today('concept', 'BK9999')
        assert row is not None
        assert row['close'] == 0.0   # None -> 0
        assert row['pct'] == 0.0     # invalid -> 0
        assert row['high'] == 0.0
        assert row['low'] == 0.0
        assert row['open'] == 0.0
        assert row['volume'] == 0.0
        assert row['amount'] == 0.0


# ===== 测试 ensure_snapshot =====

class TestEnsureSnapshot:
    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_lunch_uses_morning_snapshot_without_refetch(self, mock_session_cls, mock_dt):
        """午休使用上午最后快照，不再访问东财。"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        # industry: 2 boards, concept: 2 boards
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001', 'BK0002'])),
            MockResponse(_make_clist_response(['BK0800', 'BK0900'])),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        assert cache.is_frozen() is False
        assert cache.ensure_snapshot() is True
        calls_before_lunch = len(mock_sess.get_calls)

        mock_dt.now.return_value = datetime(2026, 7, 27, 11, 35, 0)
        result = cache.ensure_snapshot(force=True)
        assert result is True
        assert cache.is_frozen() is True
        assert len(mock_sess.get_calls) == calls_before_lunch

        stats = cache.stats()
        assert stats['captured_count_industry'] == 2
        assert stats['captured_count_concept'] == 2
        assert stats['mode'] == 'frozen'

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_real_time_mode_no_freeze(self, mock_session_cls, mock_dt):
        """盘中实时模式（10:00）应 capture 但不 freeze"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001', 'BK0002'])),
            MockResponse(_make_clist_response(['BK0800', 'BK0900'])),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        result = cache.ensure_snapshot()
        assert result is True
        assert cache.is_frozen() is False  # 实时模式不 freeze

    @patch('services.board_snapshot.datetime')
    def test_no_capture_on_weekend(self, mock_dt):
        """非交易日不 capture，返回当前是否有数据（无数据返回 False）"""
        mock_dt.now.return_value = datetime(2026, 7, 25, 10, 0, 0)  # 周六

        cache = get_snapshot_cache()
        result = cache.ensure_snapshot()
        # 实时模式：返回当前是否有数据，周末无数据 → False
        assert result is False
        assert cache.is_frozen() is False
        stats = cache.stats()
        assert stats['captured_count_industry'] == 0
        assert stats['captured_count_concept'] == 0
        assert stats['mode'] == 'off'


# ===== 测试 get_board_today / get_all / stats / get_date =====

class TestGetters:
    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_get_board_today(self, mock_session_cls, mock_dt):
        """get_board_today('concept', 'BK0800') 取值正确"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001', 'BK0002'])),
            MockResponse(_make_clist_response(['BK0800', 'BK0900'])),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        cache.ensure_snapshot()

        row = cache.get_board_today('concept', 'BK0800')
        assert row is not None
        assert row['code'] == 'BK0800'
        assert row['name'] == '板块BK0800'
        assert row['close'] == 1234.56
        assert row['pct'] == 1.23
        assert row['channel'] == 'eastmoney_push2delay'

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_get_all(self, mock_session_cls, mock_dt):
        """get_all 返回全部行业板块"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001', 'BK0002', 'BK0003'])),
            MockResponse(_make_clist_response(['BK0800'])),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        cache.ensure_snapshot()

        all_ind = cache.get_all('industry')
        assert len(all_ind) == 3
        assert 'BK0001' in all_ind
        assert 'BK0002' in all_ind
        assert 'BK0003' in all_ind

    def test_get_date_empty(self):
        """空 snapshot 时 get_date 返回 None"""
        cache = get_snapshot_cache()
        assert cache.get_date() is None

    def test_stats_empty(self):
        """空 snapshot 时 stats 各字段为 0"""
        cache = get_snapshot_cache()
        stats = cache.stats()
        assert stats['date'] is None
        assert stats['captured_count_industry'] == 0
        assert stats['captured_count_concept'] == 0
        assert stats['frozen'] is False
        assert stats['captured_at'] is None

    @patch('services.board_snapshot.datetime')
    @patch('requests.Session', create=True)
    def test_get_board_today_none(self, mock_session_cls, mock_dt):
        """不存在的板块返回 None"""
        mock_dt.now.return_value = datetime(2026, 7, 27, 10, 0, 0)

        mock_sess = MockSession()
        mock_sess.get_responses = [
            MockResponse(_make_clist_response(['BK0001'])),
            MockResponse(_make_clist_response(['BK0800'])),
        ]
        mock_sess._response_iter = iter(mock_sess.get_responses)
        mock_session_cls.return_value = mock_sess

        cache = get_snapshot_cache()
        cache.ensure_snapshot()

        assert cache.get_board_today('concept', 'NOTEXIST') is None


# ===== 测试单例 =====

class TestSingleton:
    def test_singleton(self):
        """多次 get_snapshot_cache 返回同一实例"""
        a = get_snapshot_cache()
        b = get_snapshot_cache()
        assert a is b
