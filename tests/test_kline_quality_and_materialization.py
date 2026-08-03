"""P0 regression tests for daily quality, derived periods and scheduler gates."""
from datetime import datetime
import sqlite3

import pandas as pd

import data_update_manager as dum
from data_loader import _resample
from data.sqlite_repo import SqliteRepo
from services.kline_quality_service import scan_daily_frame


def _bars(start='2026-01-05', days=45):
    idx = pd.bdate_range(start, periods=days)
    return pd.DataFrame({
        'date': idx.strftime('%Y-%m-%d'),
        'open': [10 + i for i in range(days)],
        'high': [11 + i for i in range(days)],
        'low': [9 + i for i in range(days)],
        'close': [10.5 + i for i in range(days)],
        'volume': [1000] * days,
    })


def test_quality_report_contains_range_anomaly_and_suspicious_gap():
    frame = pd.DataFrame([
        {'date': '2026-01-02', 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 1},
        {'date': '2026-03-20', 'open': 0, 'high': 1, 'low': 2, 'close': 1, 'volume': 1},
    ])
    report = scan_daily_frame(frame, code='000001')
    assert report['first_date'] == '2026-01-02'
    assert report['last_date'] == '2026-03-20'
    assert report['rows'] == 2
    assert report['repair_pending'] is True
    assert report['blocking_failure'] is True
    assert any(item['reason'] == 'ohlc_out_of_range' for item in report['abnormal_ohlc'])
    assert report['suspicious_gaps'][0]['reason'] == 'suspicious_gap_unexplained'
    assert '停牌' not in str(report['suspicious_gaps'])


def test_replace_kline_period_removes_old_derived_rows_and_refreshes_meta(tmp_path):
    repo = SqliteRepo(db_path=tmp_path / 'kline.db')
    code = 'TEST001'
    repo.save_kline(code, 'weekly', _bars(days=5))
    replacement = _bars(start='2026-01-05', days=10).iloc[[4, 9]].copy()
    written = repo.replace_kline_period(code, 'weekly', replacement)
    assert written == 2
    got = repo.read_kline(code, 'weekly')
    assert got is not None and len(got) == 2
    assert set(got['date']) == set(replacement['date'])
    conn = sqlite3.connect(str(tmp_path / 'kline.db'))
    meta = conn.execute(
        'SELECT rows, first_date, last_date FROM kline_meta WHERE code=? AND period=?',
        (code, 'weekly'),
    ).fetchone()
    conn.close()
    assert meta == (2, '2026-01-09', '2026-01-16')


def test_materialize_default_covers_ledger_code_and_replaces_ghosts(monkeypatch, tmp_path):
    db = tmp_path / 'kline.db'
    repo = SqliteRepo(db_path=db)
    code = '600001'
    daily = _bars()
    repo.save_kline(code, 'daily', daily)
    repo.record_stock_cache(code, '测试股')
    repo.save_kline(code, 'weekly', daily.iloc[:3])

    monkeypatch.setattr(dum, '_LEDGER_DB', str(db))
    result = dum.materialize_higher_periods(periods=('weekly',))
    assert result['completion_ready'] is True
    assert result['success'] == 1
    expected = _resample(daily, 'weekly')
    got = repo.read_kline(code, 'weekly')
    assert got is not None
    assert len(got) == len(expected)
    assert len(got) < len(daily)


def test_quality_report_marks_gap_as_repair_pending_for_stock_selection():
    report = scan_daily_frame(
        _bars().drop(index=list(range(20, 36))),
        code='600002',
    )
    built = dum.build_stock_pending_from_ledger(
        [('600002', '测试股')],
        {'600002': report['last_date']},
        {'600002': report['rows']},
        report['last_date'].replace('-', ''),
        quality_reports={'600002': report},
    )
    assert report['repair_pending'] is True
    assert report['blocking_failure'] is False
    assert built['pending_repair'] == 0
    assert built['repair_pending_codes'] == []
    # Daily settlement only requires the target-day bar. Historical gaps are
    # repaired separately by the bounded maintenance cursor.
    assert built['pending_sparse'] == 0
    assert built['pending'] == []


def test_scheduler_windows_pause_at_lunch_and_settlement():
    assert dum._intraday_sync_window(datetime(2026, 7, 31, 10, 0)) == 'morning'
    assert dum._intraday_sync_window(datetime(2026, 7, 31, 12, 0)) == 'lunch'
    assert dum._intraday_sync_window(datetime(2026, 7, 31, 14, 0)) == 'afternoon'
    assert dum._intraday_sync_window(datetime(2026, 7, 31, 15, 10)) == 'settlement'
    assert dum._intraday_sync_window(datetime(2026, 7, 31, 15, 30)) == 'after_close'
    assert dum._startup_allows_full_update(datetime(2026, 7, 31, 15, 0)) is False
    assert dum._startup_allows_full_update(datetime(2026, 7, 31, 15, 30)) is True


def test_intraday_scheduler_only_refreshes_ephemeral_nav_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        'services.nav_spot_service.fetch_nav_spots',
        lambda force=False: calls.append(force) or {
            'data': {'sh000300': {'price': 4588.2}},
        },
    )
    dum._intraday_sync_qmt()
    assert calls == [True]


def test_qmt_stock_update_rejects_invalid_settlement_bar(monkeypatch, tmp_path):
    db = tmp_path / 'kline.db'
    repo = SqliteRepo(db_path=db)
    code = '600003'
    dates = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-07-30']
    daily = pd.DataFrame({
        'date': dates,
        'open': [10, 10, 10, 10],
        'high': [11, 11, 11, 11],
        'low': [9, 9, 9, 9],
        # The invalid first bar is a hard error that an incremental tail
        # update cannot repair.
        'close': [12, 10, 10, 10],
        'volume': [100] * 4,
    })
    repo.save_kline(code, 'daily', daily)
    repo.record_stock_cache(code, '测试股')
    monkeypatch.setattr(dum, '_LEDGER_DB', str(db))
    monkeypatch.setattr(dum, 'is_qmt_daily_done', lambda: False)
    monkeypatch.setattr(dum, '_qmt_http_available', lambda: True)
    monkeypatch.setattr(dum, '_target_trade_day_str', lambda: '20260731')
    marked = []
    monkeypatch.setattr(dum, '_mark_qmt_daily_done', lambda: marked.append(True))

    class Client:
        def ohlc_batch(self, codes, **kwargs):
            if 'sh000300' in codes:
                return {'items': {
                    code: {'time': '20260731'}
                    for code in codes
                }}
            return {'items': {
                codes[0]: {
                    'time': '20260731',
                    'open': 10,
                    'high': 9,
                    'low': 11,
                    'close': 12,
                    'volume': 100,
                },
            }}

    monkeypatch.setattr(
        'data.qmt_http_client.get_qmt_http_client',
        lambda: Client(),
    )
    result = dum.qmt_update_all_stocks(
        force=True,
        rebuild_ledger=False,
        mark_done=True,
        batch_size=40,
    )
    assert result['failed'] == 1
    assert result['pending_repair'] == 0
    assert result['completion_ready'] is False
    assert marked == []


def test_update_all_today_does_not_mark_done_when_stage_failed(monkeypatch):
    marked = []
    monkeypatch.setenv('BOARD_HISTORY_REPAIR_BATCH', '0')
    monkeypatch.setattr(dum, 'is_today_updated', lambda: False)
    monkeypatch.setattr(dum, '_is_trading_day', lambda: False)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: True)
    monkeypatch.setattr(
        dum, 'update_all_indices_qmt',
        lambda max_retries=3: {'success': 0, 'failed': 1, 'total': 1},
    )
    monkeypatch.setattr(
        dum, 'qmt_update_all_stocks',
        lambda *args, **kwargs: {
            'success': 0, 'failed': 1, 'pending': 1,
            'completion_ready': False,
        },
    )
    monkeypatch.setattr(
        dum, 'materialize_higher_periods',
        lambda *args, **kwargs: {
            'success': 1, 'failed': 0, 'total': 1, 'completion_ready': True,
        },
    )
    monkeypatch.setattr(dum, '_mark_today_done', lambda: marked.append(True))
    monkeypatch.setattr(dum, '_update_status', lambda mutator: None)
    result = dum.update_all_today()
    assert result['completion_ready'] is False
    assert result['pending_stages']
    assert marked == []


def test_update_all_today_materializes_only_changed_and_repaired_codes(monkeypatch):
    monkeypatch.setenv('BOARD_HISTORY_REPAIR_BATCH', '2')
    monkeypatch.setattr(dum, 'is_today_updated', lambda: False)
    monkeypatch.setattr(dum, '_is_trading_day', lambda: False)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: True)
    monkeypatch.setattr(
        dum, 'update_all_indices_qmt',
        lambda max_retries=3: {
            'success': 1, 'failed': 0, 'total': 1,
            'completion_ready': True, 'updated_codes': ['sh000300'],
        },
    )
    monkeypatch.setattr(
        dum, 'qmt_update_all_stocks',
        lambda *args, **kwargs: {
            'success': 1, 'failed': 0, 'total': 1,
            'completion_ready': True, 'updated_codes': ['603259'],
        },
    )
    monkeypatch.setattr(
        'services.history_repair_service.repair_history_batch',
        lambda **kwargs: {
            'processed': 2, 'repaired': 1, 'failed': 0,
            'repaired_codes': ['000670'], 'completion_ready': True,
        },
    )
    materialized = []

    def materialize(*, codes, periods=None):
        materialized.extend(codes)
        return {
            'success': len(codes) * 4, 'failed': 0,
            'total': len(codes) * 4, 'completion_ready': True,
        }

    monkeypatch.setattr(dum, 'materialize_higher_periods', materialize)
    monkeypatch.setattr(dum, '_mark_today_done', lambda: None)
    result = dum.update_all_today()
    assert result['completion_ready'] is True
    assert materialized == ['000670', '603259', 'sh000300']


def test_formula_channel_failure_does_not_skip_http_stock_settlement(monkeypatch):
    calls = []
    monkeypatch.setattr(dum, 'is_today_updated', lambda: False)
    monkeypatch.setattr(dum, '_is_trading_day', lambda: False)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: False)
    monkeypatch.setattr(
        dum,
        'qmt_update_all_stocks',
        lambda *args, **kwargs: calls.append(True) or {
            'success': 0,
            'failed': 0,
            'canceled': True,
            'completion_ready': False,
        },
    )
    monkeypatch.setattr(dum, '_update_status', lambda mutator: None)

    result = dum.update_all_today()
    assert calls == [True]
    assert result['stocks']['canceled'] is True
