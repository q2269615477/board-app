"""强制板块快照刷新接口契约。"""
from unittest.mock import MagicMock

from flask import Flask

from api.board_routes import bp


def _client():
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(bp)
    return app.test_client()


def test_snapshot_refresh_rebuilds_board_change_cache(monkeypatch):
    snapshot = MagicMock()
    snapshot.ensure_snapshot.return_value = True
    snapshot.stats.return_value = {
        'date': '2026-08-03',
        'captured_count_industry': 496,
        'captured_count_concept': 503,
    }
    spot = MagicMock()
    context = MagicMock()
    context.refresh_board_changes.return_value = {
        'industry:BK0001': 1.2,
        'BK0001': 1.2,
    }

    monkeypatch.setattr('services.board_snapshot.get_snapshot_cache', lambda: snapshot)
    monkeypatch.setattr('services.board_spot_cache.get_board_spot_cache', lambda: spot)
    monkeypatch.setattr('api.board_routes.get_app_context', lambda: context)

    response = _client().post('/api/snapshot/refresh')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ok'] is True
    assert payload['snapshot_ready'] is True
    assert payload['board_changes_count'] == 2
    snapshot.ensure_snapshot.assert_called_once_with(force=True)
    spot.invalidate_all.assert_called_once_with()
    context.refresh_board_changes.assert_called_once_with(force=False)


def test_snapshot_refresh_reports_failure_without_stale_success(monkeypatch):
    snapshot = MagicMock()
    snapshot.ensure_snapshot.return_value = False
    snapshot.stats.return_value = {
        'date': None,
        'captured_count_industry': 0,
        'captured_count_concept': 0,
    }
    spot = MagicMock()
    context = MagicMock()
    context.refresh_board_changes.return_value = {}

    monkeypatch.setattr('services.board_snapshot.get_snapshot_cache', lambda: snapshot)
    monkeypatch.setattr('services.board_spot_cache.get_board_spot_cache', lambda: spot)
    monkeypatch.setattr('api.board_routes.get_app_context', lambda: context)

    response = _client().post('/api/snapshot/refresh')

    assert response.status_code == 503
    assert response.get_json()['ok'] is False
    context.refresh_board_changes.assert_called_once_with(force=True)
