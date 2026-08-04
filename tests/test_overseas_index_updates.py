"""Regression coverage for the formal overseas-index daily-update path."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import data_update_manager as dum


OVERSEAS = ('^N225', '日经225', 'global_index')


def _capture_status(monkeypatch):
    captured = {}

    def capture(mutator):
        status = {}
        mutator(status)
        captured.clear()
        captured.update(status)
        return status

    monkeypatch.setattr(dum, '_update_status', capture)
    return captured


def _configure_global_only(monkeypatch, db_path):
    monkeypatch.setattr(dum, '_LEDGER_DB', str(db_path))
    monkeypatch.setattr(dum, 'PREWARM_TARGETS', [OVERSEAS])
    monkeypatch.setattr(dum, 'QMT_INDEX_MAP', {})
    monkeypatch.setattr(dum, 'EASTMONEY_INDEX_CODES', frozenset())
    monkeypatch.setattr(dum, 'BOARD_ONLY_PREWARM', frozenset())
    monkeypatch.setattr(dum, 'PERMANENT_SKIP_INDICES', {})
    monkeypatch.setattr(
        dum,
        '_index_session_target',
        lambda *args, **kwargs: ('20260803', False),
    )
    dum._ensure_ledger_schema()


def test_overseas_index_uses_global_loader_and_reaches_exchange_target(
    monkeypatch, tmp_path
):
    """A global target is refreshed without touching QMT or Tushare."""
    db_path = tmp_path / 'kline.db'
    _configure_global_only(monkeypatch, db_path)
    status = _capture_status(monkeypatch)
    calls = []

    def fail_qmt():
        raise AssertionError('global-only update must not connect to QMT')

    monkeypatch.setattr(dum, '_qmt_connect', fail_qmt)
    monkeypatch.setattr(
        dum,
        '_tushare_fallback_single_index',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError('global-only update must not use Tushare')
        ),
    )

    from data.sqlite_repo import SqliteRepo

    def fake_loader(code, period, *, target_date=None):
        calls.append((code, period, target_date))
        frame = pd.DataFrame([
            {
                'date': '2026-08-03',
                'open': 100,
                'high': 101,
                'low': 99,
                'close': 100.5,
                'volume': 1000,
            }
        ])
        SqliteRepo(db_path=Path(db_path)).save_kline(code, period, frame)
        frame.attrs['source'] = 'eastmoney_history'
        frame.attrs['fallback_chain'] = ['sqlite', 'eastmoney_history']
        return frame

    monkeypatch.setattr('data.global_index_kline.load_global_index_kline', fake_loader)

    result = dum.update_all_indices_qmt(max_retries=1)

    assert result['success'] == 1
    assert result['failed'] == 0
    assert result['completion_ready'] is True
    assert calls == [('^N225', 'daily', '20260803')]
    assert status['indices']['^N225']['source'] == 'eastmoney_history'
    assert status['indices']['^N225']['fallback_chain'] == [
        'sqlite', 'eastmoney_history'
    ]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT MAX(date) FROM kline WHERE code='^N225' AND period='daily'"
        ).fetchone()[0] == '2026-08-03'


def test_overseas_index_stays_stale_when_loader_cannot_persist_target(
    monkeypatch, tmp_path
):
    """A non-empty remote response is not success until local DB reaches target."""
    db_path = tmp_path / 'kline.db'
    _configure_global_only(monkeypatch, db_path)
    status = _capture_status(monkeypatch)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO kline "
            "(code, period, date, open, high, low, close, volume, updated_at) "
            "VALUES ('^N225', 'daily', '2026-07-31', 1, 2, 0.5, 1.5, 1, '')"
        )
        conn.commit()

    frame = pd.DataFrame([
        {
            'date': '2026-08-03',
            'open': 100,
            'high': 101,
            'low': 99,
            'close': 100.5,
            'volume': 1000,
        }
    ])
    frame.attrs['source'] = 'eastmoney_history'
    frame.attrs['fallback_chain'] = ['sqlite', 'eastmoney_history']
    monkeypatch.setattr(
        'data.global_index_kline.load_global_index_kline',
        lambda code, period, **kwargs: frame,
    )

    result = dum.update_all_indices_qmt(max_retries=1)

    assert result['success'] == 0
    assert result['failed'] == 1
    assert result['completion_ready'] is False
    item = status['indices']['^N225']
    assert item['status'] == 'stale_no_source'
    assert item['local_max'] == '20260731'
    assert item['target_date'] == '20260803'
    assert item['source'] == 'eastmoney_history'
    assert item['fallback_chain'] == ['sqlite', 'eastmoney_history']


def test_global_index_is_in_debt_scan_and_status_projection(monkeypatch, tmp_path):
    db_path = tmp_path / 'kline.db'
    _configure_global_only(monkeypatch, db_path)
    monkeypatch.setattr(dum, 'get_all_cached_stocks', lambda: [])
    monkeypatch.setattr(
        dum, '_load_classified_boards', lambda *args, **kwargs: []
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO kline_meta "
            "(code, period, rows, first_date, last_date, updated_at) "
            "VALUES ('^N225', 'daily', 100, '2026-01-01', '2026-07-31', '')"
        )
        conn.commit()

    debt = dum.scan_update_debt(sample_limit=2)
    assert debt['indices']['total'] == 1
    assert debt['indices']['lagging'] == 1
    assert debt['indices']['samples'][0]['code'] == '^N225'

    monkeypatch.setattr(
        dum,
        '_load_status',
        lambda: {'indices': {'^N225': {'status': 'stale_no_source'}}},
    )
    projected = dum.get_update_status()
    assert projected['index_stats']['total'] == 1
    assert projected['index_stats']['failed'] == 1


def test_non_trade_index_poll_wait_policy(monkeypatch):
    now = datetime(2026, 8, 4, 12, 0)
    monkeypatch.setattr(
        dum,
        '_next_trade_close',
        lambda current=None: now + timedelta(hours=8),
    )
    assert dum._wait_after_non_trade_index_poll(now, False) == 600
    assert dum._wait_after_non_trade_index_poll(now, True) == 3600


def test_a_share_holiday_still_runs_exchange_aware_index_stage(monkeypatch):
    now = datetime(2026, 10, 2, 15, 30)
    calls = []
    monkeypatch.setattr(
        dum,
        'update_all_indices_qmt',
        lambda: calls.append('indices') or {'completion_ready': False},
    )

    result, wait_seconds = dum._poll_indices_on_a_share_holiday(now)

    assert calls == ['indices']
    assert result['completion_ready'] is False
    assert wait_seconds == 600


def test_daily_entry_does_not_gate_global_index_stage_on_qmt_connection(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv('BOARD_HISTORY_REPAIR_BATCH', '0')
    monkeypatch.setattr(dum, 'is_today_updated', lambda: False)
    monkeypatch.setattr(dum, '_is_trading_day', lambda: False)
    monkeypatch.setattr(
        dum,
        '_qmt_connect',
        lambda: (_ for _ in ()).throw(
            AssertionError('daily entry must not gate the index stage on QMT')
        ),
    )
    monkeypatch.setattr(
        dum,
        'update_all_indices_qmt',
        lambda retries: calls.append(('indices', retries)) or {
            'success': 1, 'failed': 0, 'total': 1,
            'completion_ready': True, 'updated_codes': [],
        },
    )
    monkeypatch.setattr(
        dum,
        'qmt_update_all_stocks',
        lambda *args, **kwargs: {
            'success': 0, 'failed': 0, 'total': 0,
            'completion_ready': True, 'updated_codes': [],
        },
    )
    monkeypatch.setattr(dum, 'materialize_higher_periods', lambda **kwargs: {})
    monkeypatch.setattr(dum, '_mark_daily_stage_running', lambda *args: None)
    monkeypatch.setattr(dum, '_mark_daily_update_pending', lambda *args: None)
    monkeypatch.setattr(dum, '_mark_today_done', lambda: None)

    result = dum._update_all_today_impl(max_retries=2)

    assert calls == [('indices', 2)]
    assert result['indices']['completion_ready'] is True
