# -*- coding: utf-8 -*-
"""清除并重建主要指数/个股的周月季年线。"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_update_manager import materialize_higher_periods
from services.kline_service import get_kline_service

CODES = [
    "sh000001", "sh000300", "sh000688", "sh000016", "sz399006",
    "sh000852", "sh000853", "sh000985", "600519",
]


def main():
    conn = sqlite3.connect(str(ROOT / "data" / "kline.db"))
    for c in CODES:
        conn.execute(
            "DELETE FROM kline WHERE code=? AND period IN "
            "('weekly','monthly','quarterly','yearly')",
            (c,),
        )
    conn.commit()
    print("deleted old higher periods for", len(CODES), "codes")
    print(materialize_higher_periods(codes=CODES))
    for p in ("weekly", "monthly", "quarterly", "yearly"):
        r = conn.execute(
            "SELECT max(date), count(*) FROM kline WHERE code='sh000001' AND period=?",
            (p,),
        ).fetchone()
        print("db sh000001", p, r)
    conn.close()

    ks = get_kline_service()
    for p in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        r, st = ks.get_kline("index", "sh000001", p, timeout=15)
        data = r.get("data") or []
        last = data[-1] if data else {}
        ts = last.get("timestamp")
        import datetime
        dt = (
            datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if ts
            else None
        )
        print("api", p, "n", len(data), "last", dt)


if __name__ == "__main__":
    main()
