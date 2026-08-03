import sqlite3
import json

import pandas as pd
import pytest

from services.history_repair_service import (
    list_repair_codes,
    repair_history_batch,
)


def _db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stock_ledger (
            code TEXT PRIMARY KEY, name TEXT, cached_at TEXT
        );
        CREATE TABLE kline (
            code TEXT, period TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL, updated_at TEXT,
            PRIMARY KEY (code, period, date)
        );
        CREATE TABLE kline_meta (
            code TEXT, period TEXT, rows INTEGER, first_date TEXT,
            last_date TEXT, updated_at TEXT,
            PRIMARY KEY (code, period)
        );
        INSERT INTO stock_ledger VALUES ('600001', '测试一', '');
        INSERT INTO stock_ledger VALUES ('600002', '测试二', '');
        INSERT INTO kline VALUES
            ('600001','daily','2026-01-02',10,11,9,10.5,100,'');
        INSERT INTO kline VALUES
            ('600002','daily','2026-01-02',20,21,19,20.5,100,'');
    """)
    conn.commit()
    conn.close()


def test_list_repair_codes_uses_ledger_and_domestic_index(tmp_path):
    db = tmp_path / "kline.db"
    _db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO kline VALUES "
        "('sh000300','daily','2026-01-02',1,2,0.5,1.5,100,'')"
    )
    conn.commit()
    conn.close()
    assert list_repair_codes(db) == ["600001", "600002", "sh000300"]


def test_batch_repairs_rows_advances_cursor_and_materializes(tmp_path):
    db = tmp_path / "kline.db"
    state = tmp_path / "history.json"
    _db(db)
    materialized = []

    def repair(code, data_type, local):
        older = pd.DataFrame([{
            "date": "2025-12-31", "open": 9, "high": 10,
            "low": 8, "close": 9.5, "volume": 100,
        }])
        return pd.concat([older, local], ignore_index=True)

    def materialize_func(codes):
        materialized.extend(codes)
        return {"completion_ready": True}

    first = repair_history_batch(
        db_path=db, state_path=state, limit=1,
        repair_func=repair, materialize_func=materialize_func,
    )
    second = repair_history_batch(
        db_path=db, state_path=state, limit=1,
        repair_func=repair, materialize_func=materialize_func,
    )
    assert first["processed"] == 1
    assert first["cursor"] == 1
    assert second["cursor"] == 0
    assert materialized == ["600001", "600002"]
    assert json.loads(state.read_text(encoding="utf-8"))["status"] == "complete"


def test_batch_can_defer_materialization_to_daily_orchestrator(tmp_path):
    db = tmp_path / "kline.db"
    state = tmp_path / "history.json"
    _db(db)

    def repair(code, data_type, local):
        older = pd.DataFrame([{
            "date": "2025-12-31", "open": 9, "high": 10,
            "low": 8, "close": 9.5, "volume": 100,
        }])
        return pd.concat([older, local], ignore_index=True)

    result = repair_history_batch(
        db_path=db,
        state_path=state,
        limit=1,
        materialize=False,
        repair_func=repair,
        materialize_func=lambda **kwargs: pytest.fail("must not materialize"),
    )
    assert result["repaired_codes"] == ["600001"]
    assert result["materialized"] is None


def test_same_date_ohlcv_correction_is_materialized(tmp_path):
    db = tmp_path / "kline.db"
    state = tmp_path / "history.json"
    _db(db)
    materialized = []

    def repair(code, data_type, local):
        corrected = local.copy()
        corrected.loc[corrected.index[-1], "volume"] = 200
        return corrected

    result = repair_history_batch(
        db_path=db,
        state_path=state,
        limit=1,
        repair_func=repair,
        materialize_func=lambda codes: materialized.extend(codes) or {},
    )

    assert result["repaired_codes"] == ["600001"]
    assert result["details"][0]["added"] == 0
    assert result["details"][0]["corrected"] is True
    assert materialized == ["600001"]
