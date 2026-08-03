"""Fast-path contracts for the top navigation quotes."""
from pathlib import Path
from unittest.mock import patch

import services.nav_spot_service as nav


def _reset():
    nav._cache = {}
    nav._cache_ts = 0
    nav._cache_meta = {}
    nav._inflight = False
    nav._refresh_thread = None
    nav._inflight_evt.set()


def test_fast_path_returns_local_snapshot_and_schedules_remote_refresh():
    _reset()
    local = {'sh000001': {'price': 3800, 'channel': 'sqlite'}}
    with patch.object(nav, '_load_local_nav_spots', return_value=local), patch.object(
        nav, '_schedule_nav_refresh', return_value=True
    ) as schedule:
        result = nav.fetch_nav_spots_fast()

    assert result['data']['sh000001']['price'] == 3800
    assert result['data']['sh000001']['market'] == 'a_share'
    assert result['meta']['background_refresh_started'] is True
    assert result['from_cache'] is True
    schedule.assert_called_once_with(force=True)


def test_fast_path_does_not_refresh_a_fresh_cache():
    _reset()
    nav._cache = {'sh000001': {'price': 3800}}
    nav._cache_ts = nav.time.time()
    nav._cache_meta = {'count': 1}
    with patch.object(nav, '_schedule_nav_refresh') as schedule:
        result = nav.fetch_nav_spots_fast()

    assert result['data']['sh000001']['price'] == 3800
    assert result['stale'] is False
    schedule.assert_not_called()


def test_market_boundary_signature_forces_immediate_background_refresh():
    _reset()
    nav._cache = {'sh000001': {'price': 3800}}
    nav._cache_ts = nav.time.time()
    nav._cache_meta = {'market_signature': ['a_share:live_morning:1']}
    market_meta = {
        'active_markets': [],
        'market_signature': ['a_share:lunch:0'],
        'all_markets_closed': True,
    }
    with patch.object(nav, '_market_meta', return_value=market_meta), patch.object(
        nav, '_schedule_nav_refresh', return_value=True
    ) as schedule:
        result = nav.fetch_nav_spots_fast()

    assert result['stale'] is True
    assert result['meta']['market_boundary_changed'] is True
    schedule.assert_called_once_with(force=True)


def test_nav_targets_include_all_default_global_indices():
    codes = {code for code, _name, _typ in nav.get_nav_targets()}

    assert {'HSI', 'HSTECH', '^N225', '^KS11', '^TWII'} <= codes
    assert {'SPX', 'IXIC', 'DJI', '800000'} <= codes


def test_failed_force_refresh_does_not_rejuvenate_old_cache():
    _reset()
    nav._cache = {'SPX': {'price': 6000, 'channel': 'old'}}
    nav._cache_ts = nav.time.time() - 600
    old_ts = nav._cache_ts
    nav._cache_meta = {'market_signature': ['us:live:1']}
    market_meta = {
        'active_markets': ['us'],
        'market_signature': ['us:live:1'],
        'all_markets_closed': False,
    }
    state = {'market': 'us', 'market_phase': 'live', 'market_open': True}
    with patch.object(nav, 'get_nav_targets', return_value=[('SPX', '标普', 'global')]), patch.object(
        nav, '_split_targets', return_value=([], [('SPX', '标普', 'global')])
    ), patch.object(nav, '_market_meta', return_value=market_meta), patch.object(
        nav, 'market_state', return_value=state
    ), patch.object(nav, '_load_local_nav_spots', return_value={}), patch.object(
        nav, '_fetch_http_spots', return_value={}
    ):
        result = nav.fetch_nav_spots(force=True)

    assert result['stale'] is True
    assert result['from_cache'] is True
    assert result['meta']['stale_active_codes'] == ['SPX']
    assert nav._cache_ts == old_ts


def test_http_route_uses_nonblocking_nav_fast_path():
    source = (Path(nav.__file__).parents[1] / 'api' / 'board_routes.py').read_text(
        encoding='utf-8'
    )

    assert 'fetch_nav_spots_fast' in source
    assert 'wait(tasks, timeout=2.0)' in source
    assert 'executor.shutdown(wait=False, cancel_futures=True)' in source
