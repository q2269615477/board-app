import json
import sqlite3

import pandas as pd

from data.sqlite_repo import SqliteRepo
from services.kline_service import KLineService, df_to_kline


def test_df_to_kline_drops_nan_rows_for_strict_json():
    df = pd.DataFrame([
        {
            "date": "2026-07-28",
            "open": 1.0,
            "high": 2.0,
            "low": 0.8,
            "close": 1.5,
            "volume": 100,
        },
        {
            "date": "2026-07-29",
            "open": float("nan"),
            "high": float("nan"),
            "low": float("nan"),
            "close": float("nan"),
            "volume": 0,
        },
    ])

    records = df_to_kline(df)

    assert len(records) == 1
    assert records[0]["close"] == 1.5
    json.dumps({"data": records}, allow_nan=False)


def test_stock_daily_no_db_uses_qmt_http_first(monkeypatch):
    svc = KLineService.__new__(KLineService)
    svc._db = type("DB", (), {})()
    svc._qmt = type("QMT", (), {"to_qmt_code": lambda self, code, data_type: "600519.SH"})()

    qmt_http_df = pd.DataFrame([
        {
            "date": "2026-07-29",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "volume": 12345,
        }
    ])
    saved = []

    def fake_read(code, period):
        if not saved:
            return None
        return qmt_http_df

    svc._db.read_kline = fake_read
    svc._db.save_kline = lambda code, period, df: saved.append((code, period, df))

    monkeypatch.setattr("services.kline_service._qmt_http_daily", lambda code, count=-1: qmt_http_df)
    monkeypatch.setattr("services.kline_service.is_qmt_available", lambda: False)

    out = svc._do_load("stock", "600519", "daily", "", "stock:600519:daily")

    assert not out.empty
    assert out.iloc[-1]["close"] == 10.5
    assert saved and saved[0][0] == "600519"


def test_save_kline_rejects_nan_ohlc(tmp_path):
    repo = SqliteRepo(tmp_path / "kline.db")
    df = pd.DataFrame([
        {
            "date": "2026-07-28",
            "open": 1.0,
            "high": 2.0,
            "low": 0.8,
            "close": 1.5,
            "volume": 100,
        },
        {
            "date": "2026-07-29",
            "open": float("nan"),
            "high": float("nan"),
            "low": float("nan"),
            "close": float("nan"),
            "volume": 0,
        },
    ])

    repo.save_kline("600519", "daily", df)

    conn = sqlite3.connect(str(tmp_path / "kline.db"))
    try:
        rows = conn.execute(
            "SELECT date, close FROM kline WHERE code='600519' AND period='daily'"
        ).fetchall()
        meta = conn.execute(
            "SELECT rows, last_date FROM kline_meta WHERE code='600519' AND period='daily'"
        ).fetchone()
    finally:
        conn.close()

    assert rows == [("2026-07-28", 1.5)]
    assert meta == (1, "2026-07-28")
