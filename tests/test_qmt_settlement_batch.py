import sqlite3

from data.sqlite_repo import SqliteRepo
import data_update_manager as dum


def _prepare(monkeypatch, tmp_path, codes, items):
    db = tmp_path / "kline.db"
    repo = SqliteRepo(db_path=db)
    for code in codes:
        repo.record_stock_cache(code, code)
    monkeypatch.setattr(dum, "_LEDGER_DB", str(db))
    monkeypatch.setattr(dum, "is_qmt_daily_done", lambda: False)
    monkeypatch.setattr(dum, "_qmt_http_available", lambda: True)
    monkeypatch.setattr(dum, "_target_trade_day_str", lambda: "20260731")

    class Client:
        def ohlc_batch(self, requested, **kwargs):
            if "sh000300" in requested:
                return {"items": {
                    code: _row("20260731")
                    for code in requested
                }}
            return {"items": {code: items[code] for code in requested if code in items}}

    monkeypatch.setattr(
        "data.qmt_http_client.get_qmt_http_client",
        lambda: Client(),
    )
    return db


def _row(trade_date="20260731", close=10):
    return {
        "time": trade_date,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 100,
    }


def test_settlement_batch_writes_only_target_trade_date(monkeypatch, tmp_path):
    db = _prepare(monkeypatch, tmp_path, ["600001"], {"600001": _row()})
    result = dum.qmt_update_all_stocks(
        force=True, rebuild_ledger=False, mark_done=False,
    )
    conn = sqlite3.connect(db)
    stored = conn.execute(
        "SELECT date, close FROM kline WHERE code='600001' AND period='daily'"
    ).fetchone()
    conn.close()
    assert stored == ("2026-07-31", 10.0)
    assert result["completion_ready"] is True
    assert result["channel"] == "qmt18080"


def test_settlement_batch_rejects_stale_probe(monkeypatch, tmp_path):
    codes = ["600001", "600002", "600003"]
    items = {code: _row("20260730") for code in codes}
    db = _prepare(monkeypatch, tmp_path, codes, items)
    class StaleClient:
        def ohlc_batch(self, requested, **kwargs):
            return {"items": {code: _row("20260730") for code in requested}}
    monkeypatch.setattr(
        "data.qmt_http_client.get_qmt_http_client",
        lambda: StaleClient(),
    )
    result = dum.qmt_update_all_stocks(
        force=True, rebuild_ledger=False, mark_done=False,
    )
    conn = sqlite3.connect(db)
    written = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
    conn.close()
    assert written == 0
    assert result["failed"] == 3
    assert result["completion_ready"] is False


def test_settlement_batch_treats_single_old_symbol_as_no_bar(monkeypatch, tmp_path):
    codes = ["600001", "600002", "600003"]
    items = {
        "600001": _row(),
        "600002": _row(),
        "600003": _row("20260730"),
    }
    _prepare(monkeypatch, tmp_path, codes, items)
    monkeypatch.setattr(
        dum,
        "_verify_no_bar_candidates",
        lambda requested, target: {code: False for code in requested},
    )
    result = dum.qmt_update_all_stocks(
        force=True, rebuild_ledger=False, mark_done=False,
    )
    assert result["success"] == 3
    assert result["no_bar"] == 1
    assert result["no_bar_codes"] == ["600003"]
    assert result["completion_ready"] is True


def test_settlement_batch_rejects_active_symbol_with_stale_bar(
    monkeypatch, tmp_path
):
    codes = ["600001", "600002", "600003"]
    items = {
        "600001": _row(),
        "600002": _row(),
        "600003": _row("20260730"),
    }
    _prepare(monkeypatch, tmp_path, codes, items)
    monkeypatch.setattr(
        dum,
        "_verify_no_bar_candidates",
        lambda requested, target: {code: True for code in requested},
    )
    result = dum.qmt_update_all_stocks(
        force=True, rebuild_ledger=False, mark_done=False,
    )
    assert result["failed"] == 1
    assert result["active_stale_codes"] == ["600003"]
    assert result["completion_ready"] is False
