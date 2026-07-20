# -*- coding: utf-8 -*-
"""One-shot verify: formula RPC daily path + DB + lifecycle flag."""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import SQLITE_PATH  # noqa: E402
from data.qmt_client import get_qmt_client  # noqa: E402
from data_update_manager import fetch_qmt_kline  # noqa: E402


def main():
    c = get_qmt_client()
    probe = c.probe_formula_ready()
    print("probe", probe)
    assert probe.get("ok"), f"formula not ready: {probe}"

    rows = fetch_qmt_kline("sh000001", "20260701")
    print("fetch_qmt_kline sh000001", len(rows), rows[-1] if rows else None)
    assert rows, "fetch_qmt_kline empty"

    df = c.get_daily("600519.SH", start="20200101", count=-1)
    print("600519 rows", None if df is None else len(df),
          None if df is None or df.empty else df.iloc[-1].to_dict())
    assert df is not None and not df.empty

    batch = c.get_daily_batch(
        ["000001.SH", "600519.SH", "000300.SH"], start="20200101", count=-1
    )
    print("batch", {k: len(v) for k, v in batch.items()})
    assert "000001.SH" in batch and len(batch["000001.SH"]) > 100

    conn = sqlite3.connect(str(SQLITE_PATH))
    for code in ("sh000001", "600519", "sh000300"):
        r = conn.execute(
            "SELECT MAX(date), COUNT(1) FROM kline WHERE code=? AND period=?",
            (code, "daily"),
        ).fetchone()
        print("db", code, r)
    conn.close()
    print("OK formula path verified")


if __name__ == "__main__":
    main()
