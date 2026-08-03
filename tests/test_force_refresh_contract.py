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


def test_force_index_route_uses_synchronous_fetch():
    fresh = {
        'data': {
            'sh000001': {
                'price': 3810.5,
                'changePct': 0.8,
                'channel': 'qmt18080',
            }
        },
        'meta': {'ok': True},
        'stale': False,
        'from_cache': False,
    }
    with patch('services.nav_spot_service.fetch_nav_spots', return_value=fresh) as sync, patch(
        'services.nav_spot_service.fetch_nav_spots_fast'
    ) as fast:
        response = _client().get('/api/spot/indices?tickers=sh000001&force=1')

    assert response.status_code == 200
    assert response.get_json()['data']['sh000001']['price'] == 3810.5
    assert response.get_json()['meta']['nav']['forced'] is True
    sync.assert_called_once_with(force=True)
    fast.assert_not_called()
