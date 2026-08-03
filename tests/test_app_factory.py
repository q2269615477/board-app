"""P1 regression tests for the Flask application factory and lifecycle."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def reset_runtime_owner(monkeypatch):
    """Keep process-level runtime ownership isolated between tests."""
    import app as app_module

    monkeypatch.setattr(app_module, '_runtime_owner', None, raising=False)
    monkeypatch.setattr(app_module, '_runtime_context', None, raising=False)
    monkeypatch.setattr(app_module, '_runtime_websocket_started', False, raising=False)


def test_create_app_returns_independent_apps_and_websocket_extensions():
    from app import create_app
    from services.realtime_websocket import RealtimeWebSocket

    first = create_app({'TESTING': True})
    second = create_app()

    assert first is not second
    assert first.config['TESTING'] is True
    assert second.config['TESTING'] is False

    first_ws = first.extensions['realtime_websocket']
    second_ws = second.extensions['realtime_websocket']
    assert isinstance(first_ws, RealtimeWebSocket)
    assert first_ws is not second_ws
    assert first_ws._running is False
    assert second_ws._running is False


def test_create_app_start_runtime_starts_its_context_and_websocket():
    from app import create_app
    from services.realtime_websocket import RealtimeWebSocket

    context = Mock(name='app_context')
    with patch('app.start_app', return_value=context) as start_app, patch.object(
        RealtimeWebSocket, 'start'
    ) as websocket_start:
        flask_app = create_app(start_runtime=True)

    start_app.assert_called_once_with()
    websocket_start.assert_called_once_with()
    assert flask_app.extensions['app_context'] is context


def test_same_app_bootstrap_is_idempotent():
    import app as app_module
    from app import create_app

    flask_app = create_app()
    context = Mock(name='app_context')
    websocket = flask_app.extensions['realtime_websocket']

    with patch('app.start_app', return_value=context) as start_app, patch.object(
        websocket, 'start'
    ) as websocket_start:
        app_module._bootstrap(flask_app)
        app_module._bootstrap(flask_app)

    start_app.assert_called_once_with()
    websocket_start.assert_called_once_with()


def test_second_app_cannot_take_runtime_owner_or_start_second_websocket():
    import app as app_module
    from app import create_app

    first = create_app()
    second = create_app()
    context = Mock(name='app_context')
    first_websocket = first.extensions['realtime_websocket']
    second_websocket = second.extensions['realtime_websocket']

    with patch('app.start_app', return_value=context) as start_app, patch.object(
        first_websocket, 'start'
    ) as first_start, patch.object(second_websocket, 'start') as second_start:
        app_module._bootstrap(first)
        with pytest.raises(RuntimeError, match='runtime already owned'):
            app_module._bootstrap(second)

    start_app.assert_called_once_with()
    first_start.assert_called_once_with()
    second_start.assert_not_called()


def test_import_app_does_not_start_runtime():
    """Importing app must not start threads, install signals, or write a PID."""
    code = r'''
import signal
import threading
from unittest.mock import patch

import services.realtime_websocket
import core.lifecycle

with patch("threading.Thread.start") as thread_start, \
     patch("signal.signal") as signal_install, \
     patch("core.cache.write_pid") as write_pid, \
     patch("core.cache.register_cleanup") as register_cleanup, \
     patch("core.lifecycle.AppContext.start") as context_start, \
     patch("services.realtime_websocket.RealtimeWebSocket.start") as websocket_start:
    import app  # noqa: F401

assert thread_start.call_count == 0
assert signal_install.call_count == 0
assert write_pid.call_count == 0
assert register_cleanup.call_count == 0
assert context_start.call_count == 0
assert websocket_start.call_count == 0
'''
    env = os.environ.copy()
    env.pop('BOARD_APP_AUTO_BOOTSTRAP', None)
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_app_context_start_is_idempotent_and_concurrent_safe(monkeypatch):
    from core.lifecycle import AppContext

    context = AppContext()
    calls = []

    def check_dependencies():
        calls.append('check')
        time.sleep(0.02)

    monkeypatch.setattr(context, '_check_dependencies', check_dependencies)
    monkeypatch.setattr(context, '_start_background_services', lambda: calls.append('background'))
    monkeypatch.setattr(context, '_start_qmt', lambda: calls.append('qmt'))
    monkeypatch.setattr('core.lifecycle.kill_orphaned', lambda: calls.append('kill'))
    monkeypatch.setattr('core.lifecycle.write_pid', lambda: calls.append('pid'))
    monkeypatch.setattr('core.lifecycle.register_cleanup', lambda: calls.append('cleanup'))

    workers = [threading.Thread(target=context.start) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()

    assert calls.count('check') == 1
    assert calls.count('background') == 1
    assert calls.count('qmt') == 1
    assert calls.count('kill') == 1
    assert calls.count('pid') == 1
    assert calls.count('cleanup') == 1


def test_app_context_start_failure_can_be_retried(monkeypatch):
    from core.lifecycle import AppContext

    context = AppContext()
    attempts = []

    def check_dependencies():
        attempts.append('check')
        if len(attempts) == 1:
            raise RuntimeError('transient startup failure')

    monkeypatch.setattr(context, '_check_dependencies', check_dependencies)
    monkeypatch.setattr(context, '_start_background_services', lambda: None)
    monkeypatch.setattr(context, '_start_qmt', lambda: None)
    monkeypatch.setattr('core.lifecycle.kill_orphaned', lambda: None)
    monkeypatch.setattr('core.lifecycle.write_pid', lambda: None)
    monkeypatch.setattr('core.lifecycle.register_cleanup', lambda: None)

    with pytest.raises(RuntimeError, match='transient startup failure'):
        context.start()

    context.start()
    context.start()
    assert attempts == ['check', 'check']
