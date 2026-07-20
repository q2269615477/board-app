# -*- coding: utf-8 -*-
"""Snapshot DB / QMT / update status for unfinished phases."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DATA_DIR, SQLITE_PATH  # noqa: E402
from data.qmt_client import get_qmt_client  # noqa: E402


def main():
    c = get_qmt_client()
    print("formula", c.probe_formula_ready())

    conn = sqlite3.connect(str(SQLITE_PATH))
    print(
        "daily_symbols",
        conn.execute(
            "SELECT COUNT(1) FROM (SELECT DISTINCT code FROM kline WHERE period='daily')"
        ).fetchone()[0],
    )
    print(
        "periods",
        conn.execute(
            "SELECT period, COUNT(1), COUNT(DISTINCT code) FROM kline GROUP BY period"
        ).fetchall(),
    )
    print(
        "stock_tail",
        conn.execute(
            "SELECT code, MAX(date), COUNT(1) FROM kline "
            "WHERE period='daily' AND length(code)=6 "
            "GROUP BY code ORDER BY MAX(date) DESC LIMIT 10"
        ).fetchall(),
    )
    print(
        "index_tail",
        conn.execute(
            "SELECT code, MAX(date), COUNT(1) FROM kline "
            "WHERE period='daily' AND (code LIKE 'sh%' OR code LIKE 'sz%' OR code IN ('HSI','HSTECH')) "
            "GROUP BY code ORDER BY code"
        ).fetchall(),
    )
    has_ledger = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_ledger'"
    ).fetchone()
    if has_ledger:
        print(
            "ledger_n",
            conn.execute("SELECT COUNT(1) FROM stock_ledger").fetchone()[0],
        )
    else:
        print("ledger_n", "missing")
    conn.close()

    p = DATA_DIR / "update_status.json"
    if p.exists():
        st = json.loads(p.read_text(encoding="utf-8"))
        boards = st.get("boards") or {}
        failed = sum(1 for v in boards.values() if isinstance(v, dict) and v.get("status") == "failed")
        ok = sum(1 for v in boards.values() if isinstance(v, dict) and v.get("status") == "success")
        print("boards_ok_failed", ok, failed, "total_keys", len(boards))
        print(
            "flags",
            {
                k: st.get(k)
                for k in (
                    "today_done",
                    "qmt_daily_done",
                    "last_run",
                    "next_run",
                )
            },
        )
    else:
        print("update_status", None)


if __name__ == "__main__":
    main()
