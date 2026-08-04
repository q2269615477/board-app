"""Regression tests for the force-update debt scanner."""
import sqlite3
from datetime import date

import data_update_manager as dum


def _db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        'CREATE TABLE kline_meta ('
        'code TEXT, period TEXT, rows INTEGER, first_date TEXT, '
        'last_date TEXT, updated_at TEXT, PRIMARY KEY(code, period))'
    )
    conn.executemany(
        'INSERT INTO kline_meta VALUES (?,?,?,?,?,?)',
        [
            ('600001', 'daily', 100, '2025-01-01', '2026-08-03', ''),
            ('sh000001', 'daily', 100, '2025-01-01', '2026-08-02', ''),
        ],
    )
    conn.commit()
    conn.close()


def test_scan_update_debt_reads_real_daily_metadata(monkeypatch, tmp_path):
    db_path = tmp_path / 'market.db'
    _db(db_path)
    monkeypatch.setattr(dum, '_LEDGER_DB', db_path)
    monkeypatch.setattr(dum, 'get_all_cached_stocks', lambda: [('600001', 'A')])
    monkeypatch.setattr(
        dum, 'PREWARM_TARGETS', [('sh000001', '上证指数', 'index')]
    )
    monkeypatch.setattr(dum, 'QMT_INDEX_MAP', {'sh000001': '000001.SH'})
    monkeypatch.setattr(dum, 'EASTMONEY_INDEX_CODES', frozenset())
    monkeypatch.setattr(dum, 'BOARD_ONLY_PREWARM', frozenset())
    monkeypatch.setattr(dum, 'PERMANENT_SKIP_INDICES', {})
    monkeypatch.setattr(
        dum, '_index_session_target', lambda *a, **k: ('20260803', False)
    )
    monkeypatch.setattr(
        dum, '_load_classified_boards',
        lambda *_a, **_k: [('industry', '测试板块', 'BK0001')],
    )
    monkeypatch.setattr(
        'services.exchange_calendar_service.latest_expected_session_date',
        lambda *_a, **_k: date(2026, 8, 3),
    )

    debt = dum.scan_update_debt(sample_limit=3)

    assert debt['needs_catchup'] is True
    assert debt['stocks']['lagging'] == 0
    assert debt['indices']['lagging'] == 1
    assert debt['boards']['lagging'] == 1
    assert '欠更扫描' in debt['summary']


def test_force_bypasses_completed_marker_but_respects_settlement_window(monkeypatch):
    monkeypatch.setattr(dum, 'is_today_updated', lambda: True)
    monkeypatch.setattr(dum, '_is_trading_day', lambda: True)
    monkeypatch.setattr(dum, '_is_at_or_after', lambda *_a: False)

    normal = dum.update_all_today()
    forced = dum.update_all_today(force=True)

    assert normal['skipped'] is True
    assert forced['deferred'] is True
    assert forced['completion_ready'] is False


def test_scan_universe_failure_is_unavailable_not_fresh(monkeypatch):
    monkeypatch.setattr(
        dum, 'get_all_cached_stocks',
        lambda: (_ for _ in ()).throw(RuntimeError('ledger busy')),
    )
    monkeypatch.setattr(
        dum, '_load_classified_boards',
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('taxonomy busy')),
    )
    monkeypatch.setattr(dum, '_managed_index_targets', lambda: [])

    debt = dum.scan_update_debt()

    assert debt['needs_catchup'] is True
    assert debt['stocks']['available'] is False
    assert debt['boards']['available'] is False
    assert '个股 状态不可用' in debt['summary']
    assert '板块 状态不可用' in debt['summary']


def test_scan_metadata_db_failure_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(dum, '_LEDGER_DB', tmp_path / 'missing' / 'market.db')
    monkeypatch.setattr(dum, 'get_all_cached_stocks', lambda: [('600001', 'A')])
    monkeypatch.setattr(dum, '_managed_index_targets', lambda: [])
    monkeypatch.setattr(
        dum, '_load_classified_boards',
        lambda *_a, **_k: [('industry', '测试板块', 'BK0001')],
    )

    debt = dum.scan_update_debt()

    assert debt['stocks']['available'] is False
    assert debt['boards']['available'] is False
    assert debt['needs_catchup'] is True


def test_index_universe_failure_does_not_hide_stock_and_board_debt(monkeypatch, tmp_path):
    db_path = tmp_path / 'market.db'
    _db(db_path)
    monkeypatch.setattr(dum, '_LEDGER_DB', db_path)
    monkeypatch.setattr(dum, 'get_all_cached_stocks', lambda: [('600001', 'A')])
    monkeypatch.setattr(
        dum, '_managed_index_targets',
        lambda: (_ for _ in ()).throw(RuntimeError('index config broken')),
    )
    monkeypatch.setattr(
        dum, '_load_classified_boards',
        lambda *_a, **_k: [('industry', '测试板块', 'BK0001')],
    )

    debt = dum.scan_update_debt()

    assert debt['indices']['available'] is False
    assert debt['stocks']['available'] is True
    assert debt['boards']['available'] is True
    assert debt['boards']['lagging'] == 1
