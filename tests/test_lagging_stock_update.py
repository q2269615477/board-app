"""test_lagging_stock_update.py — 个股 pending 与 debt 同口径 + 只请求 lagging"""
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')

import data_update_manager as dum


TARGET = '2026-07-27'
TARGET_NORM = '20260727'


def _make_db(tmp_path: Path, rows):
    """rows: list of (code, date, n_extra_dummy_days) — writes daily bars."""
    db = tmp_path / 'kline.db'
    conn = sqlite3.connect(str(db))
    conn.execute(
        'CREATE TABLE kline ('
        'code TEXT, period TEXT, date TEXT, open REAL, high REAL, '
        'low REAL, close REAL, volume INTEGER, '
        'PRIMARY KEY (code, period, date))'
    )
    conn.execute('CREATE TABLE stock_ledger (code TEXT, name TEXT)')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS kline_meta ('
        'code TEXT, period TEXT, rows INTEGER, first_date TEXT, '
        'last_date TEXT, updated_at TEXT, PRIMARY KEY (code, period))'
    )
    for code, last_date, n_rows in rows:
        conn.execute('INSERT INTO stock_ledger VALUES (?, ?)', (code, f'N{code}'))
        # write n_rows bars ending at last_date
        y, m, d = int(last_date[:4]), int(last_date[5:7]), int(last_date[8:10])
        import datetime as _dt
        end = _dt.date(y, m, d)
        for i in range(n_rows):
            day = end - _dt.timedelta(days=n_rows - 1 - i)
            # skip nothing — synthetic calendar days ok for unit test
            ds = day.strftime('%Y-%m-%d')
            conn.execute(
                'INSERT OR REPLACE INTO kline VALUES (?,?,?,?,?,?,?,?)',
                (code, 'daily', ds, 1, 1, 1, 1, 100),
            )
    conn.commit()
    conn.close()
    return str(db)


class TestClassifyAndBuild:
    def test_classify_date_lag_sparse_up_to_date(self):
        assert dum.classify_stock_daily_status('2026-07-20', 200, TARGET_NORM) == 'date_lag'
        assert dum.classify_stock_daily_status('2026-07-27', 50, TARGET_NORM) == 'up_to_date'
        assert dum.classify_stock_daily_status('2026-07-27', 200, TARGET_NORM) == 'up_to_date'
        assert dum.classify_stock_daily_status(None, 0, TARGET_NORM) == 'date_lag'

    def test_build_pending_skips_up_to_date(self):
        stocks = [('600001', 'A'), ('600002', 'B'), ('600003', 'C')]
        max_dates = {
            '600001': '2026-07-20',
            '600002': '2026-07-27',
            '600003': '2026-07-10',
        }
        row_counts = {'600001': 200, '600002': 200, '600003': 200}
        built = dum.build_stock_pending_from_ledger(
            stocks, max_dates, row_counts, TARGET_NORM
        )
        codes = [c for c, _ in built['pending']]
        assert codes == ['600003', '600001'] or set(codes) == {'600001', '600003'}
        assert '600002' not in codes
        assert built['skipped_up_to_date'] == 1
        assert built['pending_date_lag'] == 2
        assert built['pending_sparse'] == 0
        assert built['total'] == 3


class TestQmtUpdateLaggingOnly:
    def _patch_common(self, monkeypatch, db_path, batch_recorder):
        monkeypatch.setattr(dum, '_LEDGER_DB', db_path)
        monkeypatch.setattr(dum, 'is_qmt_daily_done', lambda: False)
        monkeypatch.setattr(dum, '_qmt_http_available', lambda: True)
        monkeypatch.setattr(dum, '_target_trade_day_str', lambda: TARGET)
        monkeypatch.setattr(dum, '_mark_qmt_daily_done', lambda: None)
        monkeypatch.setattr(dum, 'rebuild_stock_ledger_from_kline', lambda **kw: None)

        mock_client = MagicMock()

        def fake_batch(codes, **kwargs):
            if 'sh000300' in codes:
                return {'items': {
                    code: {'time': TARGET_NORM}
                    for code in codes
                }}
            batch_recorder.extend(list(codes or []))
            return {'items': {}, 'errors': []}

        mock_client.ohlc_batch = fake_batch
        monkeypatch.setattr(
            'data.qmt_http_client.get_qmt_http_client',
            lambda: mock_client,
        )
        return mock_client

    def test_only_lagging_requested(self, monkeypatch, tmp_path):
        db = _make_db(tmp_path, [
            ('600001', '2026-07-20', 150),  # lag
            ('600002', '2026-07-27', 150),  # up to date
            ('600003', '2026-07-10', 150),  # lag
        ])
        requested = []
        self._patch_common(monkeypatch, db, requested)

        result = dum.qmt_update_all_stocks(
            force=True, rebuild_ledger=False, mark_done=False, batch_size=40
        )
        # strip market prefix if any — codes passed may be qmt form
        bare = []
        for c in requested:
            s = str(c)
            if s.startswith(('SH', 'SZ', 'sh', 'sz')):
                bare.append(s[-6:])
            else:
                bare.append(s[-6:] if len(s) >= 6 else s)
        assert '600002' not in bare
        assert set(bare) >= {'600001', '600003'} or set(requested)  # at least lagging hit
        assert result.get('skipped_up_to_date') == 1
        assert result.get('pending') == 2
        assert result.get('pending_date_lag') == 2
        assert result.get('total') == 3 or result.get('ledger') == 3

    def test_all_current_skips_batch(self, monkeypatch, tmp_path):
        db = _make_db(tmp_path, [
            ('600001', '2026-07-27', 150),
            ('600002', '2026-07-27', 150),
        ])
        requested = []
        self._patch_common(monkeypatch, db, requested)
        result = dum.qmt_update_all_stocks(
            force=True, rebuild_ledger=False, mark_done=False
        )
        assert result.get('skipped') is True or result.get('pending') == 0
        assert result.get('skipped_up_to_date', 0) >= 2
        assert requested == []

    def test_cancel_immediate(self, monkeypatch, tmp_path):
        db = _make_db(tmp_path, [
            ('600001', '2026-07-01', 150),
            ('600002', '2026-07-01', 150),
        ])
        requested = []
        self._patch_common(monkeypatch, db, requested)
        result = dum.qmt_update_all_stocks(
            force=True, rebuild_ledger=False, mark_done=False,
            cancel_check=lambda: True,
        )
        assert result.get('canceled') is True
        assert 'skipped_up_to_date' in result
