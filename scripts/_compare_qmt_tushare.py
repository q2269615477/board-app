# -*- coding: utf-8 -*-
"""对比 QMT 公式口与 Tushare 同日收盘价，评估写库安全策略。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.env_bootstrap import ensure_tushare_token


def main():
    ensure_tushare_token()
    import tushare as ts
    from data.qmt_client import get_qmt_client

    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    client = get_qmt_client()

    pairs = [
        ("000001.SH", "000001.SH", "上证"),
        ("000300.SH", "000300.SH", "沪深300"),
        ("600519.SH", "600519.SH", "茅台"),
    ]
    for qmt_code, ts_code, name in pairs:
        print(f"\n=== {name} {qmt_code} ===")
        qdf = client.get_daily(qmt_code, start="20260615", end="20260719", count=-1)
        tdf = pro.index_daily(ts_code=ts_code, start_date="20260615", end_date="20260719") if not ts_code.startswith("6") else pro.daily(ts_code=ts_code, start_date="20260615", end_date="20260719")
        if qdf is None or qdf.empty:
            print("  QMT empty")
            continue
        print(f"  QMT rows={len(qdf)} last={qdf.iloc[-1]['date']} close={qdf.iloc[-1]['close']}")
        print("  QMT last 5:")
        print(qdf.tail(5).to_string(index=False))
        if tdf is not None and not tdf.empty:
            tdf = tdf.sort_values("trade_date")
            print(f"  TS rows={len(tdf)} last={tdf.iloc[-1]['trade_date']} close={tdf.iloc[-1]['close']}")
            print("  TS last 5:")
            cols = ["trade_date", "close"]
            print(tdf[cols].tail(5).to_string(index=False))
            # overlap compare
            qmap = {}
            for _, r in qdf.iterrows():
                d = str(r["date"]).replace("-", "")[:8]
                qmap[d] = float(r["close"])
            mismatches = 0
            checked = 0
            for _, r in tdf.iterrows():
                d = str(r["trade_date"])
                if d in qmap:
                    checked += 1
                    tc = float(r["close"])
                    qc = qmap[d]
                    if abs(tc - qc) / max(abs(tc), 1e-9) > 0.002:
                        mismatches += 1
                        if mismatches <= 5:
                            print(f"  MISMATCH {d}: qmt={qc} ts={tc}")
            print(f"  overlap checked={checked} mismatches(>0.2%)={mismatches}")


if __name__ == "__main__":
    main()
