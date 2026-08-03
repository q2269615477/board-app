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
    snapshot.refresh_snapshot.return_value = {
        'available': True,
        'refreshed': True,
        'stale': False,
        'reason': 'refreshed',
        'captured_at': 1785722400,
    }
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
    snapshot.refresh_snapshot.assert_called_once_with(force=True)
    spot.invalidate_all.assert_called_once_with()
    context.refresh_board_changes.assert_called_once_with(force=False)


def test_snapshot_refresh_reports_failure_without_stale_success(monkeypatch):
    snapshot = MagicMock()
    snapshot.refresh_snapshot.return_value = {
        'available': False,
        'refreshed': False,
        'stale': True,
        'reason': 'refresh_failed',
        'captured_at': None,
    }
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
    assert response.get_json()['stale'] is True
    context.refresh_board_changes.assert_called_once_with(force=True)


def test_snapshot_refresh_does_not_report_old_snapshot_as_success(monkeypatch):
    snapshot = MagicMock()
    snapshot.refresh_snapshot.return_value = {
        'available': True,
        'refreshed': False,
        'stale': True,
        'reason': 'refresh_failed',
        'captured_at': 1785717000,
    }
    snapshot.stats.return_value = {
        'date': '20260803',
        'captured_count_industry': 496,
        'captured_count_concept': 503,
    }
    spot = MagicMock()
    context = MagicMock()
    context.refresh_board_changes.return_value = {'industry:BK0001': 1.2}

    monkeypatch.setattr('services.board_snapshot.get_snapshot_cache', lambda: snapshot)
    monkeypatch.setattr('services.board_spot_cache.get_board_spot_cache', lambda: spot)
    monkeypatch.setattr('api.board_routes.get_app_context', lambda: context)

    response = _client().post('/api/snapshot/refresh')
    payload = response.get_json()

    assert response.status_code == 503
    assert payload['ok'] is False
    assert payload['snapshot_ready'] is True
    assert payload['stale'] is True
    assert '旧快照' in payload['message']
    context.refresh_board_changes.assert_called_once_with(force=False)
