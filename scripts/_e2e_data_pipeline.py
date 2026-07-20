# -*- coding: utf-8 -*-
"""端到端：QMT 抽样个股 + Tushare 尾部补齐 + 库内日期抽查。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.env_bootstrap import ensure_tushare_token


def main():
    ensure_tushare_token()
    out = {}

    from data_update_manager import (
        _qmt_connect,
        qmt_update_all_stocks,
        tushare_fill_stocks_recent,
        update_all_indices_tushare,
    )

    print("=== QMT connect ===")
    qmt_ok = _qmt_connect()
    out["qmt_ok"] = qmt_ok
    print("qmt_ok=", qmt_ok)

    print("=== QMT stock sample (limit=15) ===")
    stocks = qmt_update_all_stocks(
        force=True, limit=15, batch_size=5, rebuild_ledger=True, mark_done=False
    )
    out["qmt_stocks"] = stocks
    print(stocks)

    print("=== Tushare stock fill (max 40) ===")
    fill = tushare_fill_stocks_recent(max_codes=40, lookback_days=25)
    out["tushare_fill"] = fill
    print(fill)

    print("=== Tushare indices (idempotent) ===")
    idx = update_all_indices_tushare()
    out["indices"] = idx
    print(idx)

    print("=== DB sample ===")
    conn = sqlite3.connect(str(ROOT / "data" / "kline.db"))
    samples = {}
    for code in ("sh000001", "600519", "000001", "000858", "300750"):
        r = conn.execute(
            "SELECT MAX(date), COUNT(*) FROM kline WHERE code=? AND period='daily'",
            (code,),
        ).fetchone()
        samples[code] = {"max": r[0], "n": r[1]}
        print(f"  {code}: max={r[0]} n={r[1]}")
    out["db"] = samples
    # lagging count
    lag = conn.execute(
        """
        SELECT COUNT(1) FROM (
          SELECT code FROM kline WHERE period='daily' AND LENGTH(code)=6
          GROUP BY code
          HAVING REPLACE(COALESCE(MAX(date),''),'-','') < '20260717'
        )
        """
    ).fetchone()[0]
    total6 = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM kline WHERE period='daily' AND LENGTH(code)=6"
    ).fetchone()[0]
    out["stock_lagging_lt_0717"] = lag
    out["stock_total_6digit"] = total6
    print(f"  stocks lagging <2026-07-17: {lag}/{total6}")
    conn.close()

    path = ROOT / "data" / "_e2e_data_pipeline.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
