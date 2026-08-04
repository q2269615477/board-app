"""Contracts for the toolbar's genuinely fresh quote path."""
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from api.board_routes import bp


ROOT = Path(__file__).resolve().parents[1]


def _client():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client()


def test_frontend_force_refreshes_top_and_left_index_quotes():
    index_bar = (ROOT / 'static/js/index-bar.js').read_text(encoding='utf-8')
    nav = (ROOT / 'static/js/nav-panel.js').read_text(encoding='utf-8')
    sse = (ROOT / 'static/js/sse-client.js').read_text(encoding='utf-8')

    assert "if (force) params.set('force', '1');" in index_bar
    assert "if (force) params.set('force', '1');" in nav
    assert 'refreshIdxPrices(true)' in sse
    assert 'loadIndexBoardChanges(true)' in sse


def test_force_refresh_reports_each_area_without_short_circuiting():
    index_bar = (ROOT / 'static/js/index-bar.js').read_text(encoding='utf-8')
    nav = (ROOT / 'static/js/nav-panel.js').read_text(encoding='utf-8')
    sse = (ROOT / 'static/js/sse-client.js').read_text(encoding='utf-8')

    assert "if (force) throw e;" in index_bar
    assert "if (force) throw e;" in nav
    assert "Promise.all([snapP, topIdxP, leftIdxP, taskP])" in sse
    assert "以下数据未及时更新：" in sse
    assert "以下数据未及时完成：" in sse
    assert "background_state" in sse
    assert "consecutivePollErrors >= 3" in sse
    assert "reportPollFailure('5分钟内未返回最终状态')" in sse
    assert "if(d.background_state === 'running_elsewhere')" in sse
    for label in ('概念/行业板块实时数据', '顶部导航栏指数', '左侧指数功能区', '日线后台补齐'):
        assert label in sse


def test_force_index_route_uses_synchronous_fetch():
    cached = {
        'data': {
            'sh000001': {
                'price': 3760.0,
                'changePct': -0.5,
                'channel': 'sqlite',
            }
        },
        'meta': {'ok': True},
        'stale': False,
        'from_cache': False,
    }
    final_close = {
        'sh000001': {
            'price': 3810.5,
            'change_pct': 0.8,
            'channel': 'eastmoney_batch',
        }
    }
    with patch('services.nav_spot_service.fetch_nav_spots', return_value=cached) as sync, patch(
        'services.nav_spot_service.fetch_nav_spots_fast'
    ) as fast, patch(
        'data_loader.fetch_a_share_index_spots', return_value=final_close
    ) as batch:
        response = _client().get('/api/spot/indices?tickers=sh000001&force=1')

    assert response.status_code == 200
    assert response.get_json()['data']['sh000001']['price'] == 3810.5
    assert response.get_json()['meta']['nav']['forced'] is True
    sync.assert_called_once_with(force=True)
    fast.assert_not_called()
    batch.assert_called_once_with(['sh000001'])
