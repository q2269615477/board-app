"""BoardSpotCache 的时段、来源和结算替换契约。"""

from unittest.mock import MagicMock, patch

import pytest

from services.board_spot_cache import BoardSpotCache


@pytest.fixture(autouse=True)
def reset_cache():
    import services.board_spot_cache as module
    module._instance = None
    yield
    module._instance = None


def _snapshot(rows):
    snapshot = MagicMock()
    snapshot.get_all.return_value = rows
    snapshot.is_frozen.return_value = False
    return snapshot


def test_lunch_uses_morning_snapshot_without_external_refetch():
    row = {
        'name': '测试板块', 'pct': 1.2, 'close': 10.0,
        'trade_date': '20260731',
    }
    snapshot = _snapshot({'BK0001': row})
    with patch('services.board_snapshot._is_a_share_session', return_value=True), \
         patch('services.board_snapshot._is_lunch_break', return_value=True), \
         patch('services.board_snapshot._is_a_share_live_session', return_value=False), \
         patch('services.board_snapshot.get_snapshot_cache', return_value=snapshot), \
         patch('data.board_api._fetch_em_board_spot', side_effect=AssertionError('must not refetch')), \
         patch('data.board_api._fetch_tushare_board_spot', side_effect=AssertionError('must not query')):
        result = BoardSpotCache.get_instance().get('industry', force=True)

    assert result['BK0001']['channel'] == 'eastmoney_push2delay_frozen'
    assert result['BK0001']['settled'] is False


def test_after_hours_settled_tushare_replaces_close_candidate():
    candidate = {'BK0001': {'涨跌幅': 1.0, 'source': 'eastmoney_push2delay'}}
    settled = {'BK0001': {'涨跌幅': 2.0, 'source': 'tushare_dc_index', 'settled': True}}
    snapshot = _snapshot(candidate)
    with patch('services.board_snapshot._is_a_share_session', return_value=False), \
         patch('services.board_snapshot._is_lunch_break', return_value=False), \
         patch('services.board_snapshot._is_a_share_live_session', return_value=False), \
         patch('services.board_snapshot.get_snapshot_cache', return_value=snapshot), \
         patch('services.board_spot_cache._tushare_expected_trade_date', return_value='2026-07-31'), \
         patch('data.board_api._fetch_tushare_board_spot', return_value=settled), \
         patch('data.board_api._fetch_em_board_spot', side_effect=AssertionError('candidate already exists')):
        result = BoardSpotCache.get_instance().get('industry', force=True)

    assert result == settled


def test_after_hours_without_settled_tushare_keeps_close_candidate():
    candidate = {'BK0001': {'涨跌幅': 1.0, 'source': 'eastmoney_push2delay'}}
    snapshot = _snapshot(candidate)
    with patch('services.board_snapshot._is_a_share_session', return_value=False), \
         patch('services.board_snapshot._is_lunch_break', return_value=False), \
         patch('services.board_snapshot._is_a_share_live_session', return_value=False), \
         patch('services.board_snapshot.get_snapshot_cache', return_value=snapshot), \
         patch('services.board_spot_cache._tushare_expected_trade_date', return_value='2026-07-31'), \
         patch('data.board_api._fetch_tushare_board_spot', return_value=None):
        result = BoardSpotCache.get_instance().get('industry', force=True)

    assert result['BK0001']['source'] == 'eastmoney_push2delay'


def test_close_wait_before_1700_keeps_candidate_without_tushare():
    candidate = {'BK0001': {'涨跌幅': 1.0, 'source': 'eastmoney_push2delay'}}
    snapshot = _snapshot(candidate)
    with patch('services.board_snapshot._is_a_share_session', return_value=False), \
         patch('services.board_snapshot._is_lunch_break', return_value=False), \
         patch('services.board_snapshot._is_a_share_live_session', return_value=False), \
         patch('services.board_snapshot.get_snapshot_cache', return_value=snapshot), \
         patch('services.board_spot_cache._tushare_expected_trade_date', return_value=''), \
         patch(
             'data.board_api._fetch_tushare_board_spot',
             side_effect=AssertionError('must wait until 17:00'),
         ):
        result = BoardSpotCache.get_instance().get('industry', force=True)

    assert result['BK0001']['source'] == 'eastmoney_push2delay'


def test_get_chgs_preserves_typed_keys_and_industry_bare_key_priority():
    cache = BoardSpotCache.get_instance()
    cache._data = {
        'industry': {'BK0001': {'涨跌幅': 3.0}},
        'concept': {'BK0001': {'涨跌幅': -1.0}, 'BK0002': {'涨跌幅': 0.5}},
    }

    result = cache.get_chgs()

    assert result['industry:BK0001'] == 3.0
    assert result['concept:BK0001'] == -1.0
    assert result['BK0001'] == 3.0
    assert result['BK0002'] == 0.5
