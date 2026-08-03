"""Regression tests for the lightweight startup contract."""
from pathlib import Path
from unittest.mock import Mock, patch

from core.runtime_limits import configure_native_thread_limits


ROOT = Path(__file__).resolve().parents[1]


def test_native_thread_defaults_are_capped_without_overwriting_user_values():
    env = {'OPENBLAS_NUM_THREADS': '3'}

    result = configure_native_thread_limits(env)

    assert result['OPENBLAS_NUM_THREADS'] == '3'
    assert result['OMP_NUM_THREADS'] == '1'
    assert result['MKL_NUM_THREADS'] == '1'
    assert all(name in env for name in result)


def test_qmt_startup_history_sync_is_opt_in():
    from core.lifecycle import AppContext

    ctx = AppContext()
    with patch('core.lifecycle.QMT_STARTUP_HISTORY_SYNC', False), patch(
        'core.lifecycle.threading.Thread'
    ) as thread_cls:
        ctx._start_qmt_history_sync()

    thread_cls.assert_not_called()
    assert ctx._threads == []


def test_qmt_startup_history_sync_tracks_worker_when_enabled():
    from core.lifecycle import AppContext

    worker = Mock()
    ctx = AppContext()
    with patch('core.lifecycle.QMT_STARTUP_HISTORY_SYNC', True), patch(
        'core.lifecycle.threading.Thread', return_value=worker
    ) as thread_cls:
        ctx._start_qmt_history_sync()

    thread_cls.assert_called_once_with(target=ctx._qmt_sync_all, daemon=True)
    worker.start.assert_called_once_with()
    assert ctx._threads == [worker]


def test_run_server_has_one_scheduler_owner_and_disables_reloader():
    source = (ROOT / 'run_server.py').read_text(encoding='utf-8')

    assert 'start_scheduler()' not in source
    assert 'configure_runtime_limits()' in source
    assert 'use_reloader=False' in source


def test_index_prewarm_is_disabled_by_default():
    config = (ROOT / 'core' / 'config.py').read_text(encoding='utf-8')
    lifecycle = (ROOT / 'core' / 'lifecycle.py').read_text(encoding='utf-8')

    assert "BOARD_APP_STARTUP_PREWARM', '0'" in config
    assert 'if STARTUP_PREWARM:' in lifecycle
