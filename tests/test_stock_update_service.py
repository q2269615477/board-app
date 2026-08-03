"""Focused contract tests for the extracted stock-update helper service."""

import sqlite3
from datetime import datetime

import pandas as pd

import data_update_manager as dum
from services.stock_update_service import (
    StockUpdateDependencies,
    StockUpdateService,
    build_stock_pending_from_ledger,
    refresh_daily_meta_cursor,
    scan_daily_quality_cursor,
    spot_trade_date,
    valid_settlement_row,
    verify_no_bar_candidates,
)


def test_target_trade_day_is_injected_without_manager_runtime():
    service = StockUpdateService(
        StockUpdateDependencies(now=lambda: datetime(2026, 7, 31, 9, 0))
    )
    assert service.target_trade_day_str() == "20260731"


def test_build_pending_keeps_classifier_injection():
    calls = []

    def classify(*args):
        calls.append(args)
        return "repair_pending"

    result = build_stock_pending_from_ledger(
        [("600001", "A")],
        {"600001": "2026-07-31"},
        {"600001": 10},
        "20260731",
        classify=classify,
    )
    assert result["pending"] == [("600001", "A")]
    assert result["pending_repair"] == 1
    assert calls and calls[0][0] == "2026-07-31"


def test_manager_facade_uses_live_classifier_patch(monkeypatch):
    monkeypatch.setattr(dum, "classify_stock_daily_status", lambda *args: "up_to_date")
    result = dum.build_stock_pending_from_ledger(
        [("600001", "A")], {"600001": None}, {"600001": 0}, "20260731"
    )
    assert result["pending"] == []
    assert result["skipped_up_to_date"] == 1


def test_quality_cursor_injects_scanner_and_reads_selected_codes():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL)"
    )
    conn.execute(
        "INSERT INTO kline VALUES ('600001','daily','2026-07-31',1,2,0.5,1.5,10)"
    )
    seen = []

    def scanner(frame, code=None):
        seen.append((code, list(frame["date"])))
        return {"code": code, "rows": len(frame)}

    reports = scan_daily_quality_cursor(conn.cursor(), ["600001"], scanner=scanner)
    conn.close()
    assert reports == {"600001": {"code": "600001", "rows": 1}}
    assert seen == [("600001", ["2026-07-31"])]


def test_spot_date_and_settlement_validation_contract():
    assert spot_trade_date({"time": "2026-07-31 15:00:00"}) == "20260731"
    assert spot_trade_date({"time": "202607"}) == ""
    assert valid_settlement_row(
        {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}
    )
    assert not valid_settlement_row(
        {"open": 1, "high": 0.5, "low": 0.1, "close": 1, "volume": 10}
    )


def test_verify_no_bar_candidates_uses_injected_token_and_provider():
    class FakePro:
        def daily(self, **kwargs):
            assert kwargs["trade_date"] == "20260731"
            return pd.DataFrame({"ts_code": ["600001.SH", "000002.SZ"]})

    result = verify_no_bar_candidates(
        ["600001", "600003"],
        "2026-07-31",
        ensure_tushare_token=lambda: True,
        get_tushare_pro=lambda: FakePro(),
    )
    assert result == {"600001": True, "600003": False}


def test_refresh_daily_meta_reads_db_truth():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE kline (code TEXT, period TEXT, date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL)"
    )
    conn.executemany(
        "INSERT INTO kline VALUES (?,?,?,?,?,?,?,?)",
        [
            ("600001", "daily", "2026-07-30", 1, 1, 1, 1, 1),
            ("600001", "daily", "2026-07-31", 1, 1, 1, 1, 1),
        ],
    )
    refresh_daily_meta_cursor(conn.cursor(), ["600001"], "20260731 160000")
    row = conn.execute(
        "SELECT rows, first_date, last_date, updated_at FROM kline_meta "
        "WHERE code='600001' AND period='daily'"
    ).fetchone()
    conn.close()
    assert row == (2, "2026-07-30", "2026-07-31", "20260731 160000")
