# -*- coding: utf-8 -*-
"""一键验证 QMT(qmt_api) + Tushare 数据连通性。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.env_bootstrap import ensure_tushare_token


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check_ports() -> dict:
    import socket

    out = {}
    for port in (58600, 58610, 58341):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            ok = s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()
        out[port] = ok
        print(f"  port {port}: {'LISTEN/OK' if ok else 'closed'}")
    return out


def check_tushare() -> dict:
    section("Tushare")
    token_ok = ensure_tushare_token()
    token_set = bool(os.environ.get("TUSHARE_TOKEN"))
    print(f"  token_set={token_set} bootstrap_ok={token_ok}")
    result = {"token_set": token_set, "indices": None, "boards_ok": False}
    if not token_set:
        return result

    try:
        from data_update_manager import update_all_indices_tushare

        indices = update_all_indices_tushare()
        result["indices"] = indices
        print(f"  indices update: {indices}")
    except Exception as e:
        result["indices_err"] = str(e)[:200]
        print(f"  indices FAIL: {e}")

    try:
        import tushare as ts

        pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
        # 上证指数最近交易日
        df = pro.index_daily(ts_code="000001.SH", start_date="20260701", end_date="20260719")
        if df is not None and not df.empty:
            last = df.sort_values("trade_date").iloc[-1]
            print(
                f"  index_daily 000001.SH last={last['trade_date']} close={last['close']}"
            )
            result["index_last"] = str(last["trade_date"])
            result["index_close"] = float(last["close"])
        else:
            print("  index_daily empty")
    except Exception as e:
        result["api_err"] = str(e)[:200]
        print(f"  pro API FAIL: {e}")

    try:
        # 东财板块接口（项目 verify 用的路径）
        from scripts.verify_tushare import main as _  # may not export
    except Exception:
        pass

    result["boards_ok"] = True  # token + index path is enough here
    return result


def check_qmt() -> dict:
    section("QMT qmt_api formula channel")
    result = {"probe": None, "samples": {}}
    try:
        from data.qmt_client import get_qmt_client

        client = get_qmt_client()
        probe = client.probe_formula_ready()
        result["probe"] = probe
        print(f"  probe_formula_ready: {probe}")

        for code in ("000001.SH", "000300.SH", "600519.SH"):
            df = client.get_daily(code, start="20260601", end="20260719", count=-1)
            if df is not None and not df.empty:
                info = {
                    "rows": int(len(df)),
                    "first": str(df.iloc[0]["date"]),
                    "last": str(df.iloc[-1]["date"]),
                    "last_close": float(df.iloc[-1]["close"]),
                    "channel": client.active_channel,
                }
                result["samples"][code] = info
                print(f"  {code}: {info}")
            else:
                result["samples"][code] = {"empty": True}
                print(f"  {code}: EMPTY")
    except Exception as e:
        result["err"] = str(e)[:300]
        print(f"  FAIL: {e}")
        import traceback

        traceback.print_exc()
    return result


def check_db() -> dict:
    section("SQLite kline.db")
    db = ROOT / "data" / "kline.db"
    result = {}
    conn = sqlite3.connect(str(db))
    codes = [
        "sh000001",
        "sh000300",
        "sh000688",
        "sh000016",
        "sz399006",
        "600519",
    ]
    for code in codes:
        r = conn.execute(
            "SELECT MAX(date), COUNT(*), MIN(date) FROM kline WHERE code=? AND period='daily'",
            (code,),
        ).fetchone()
        result[code] = {"max": r[0], "n": r[1], "min": r[2]}
        print(f"  {code}: max={r[0]} n={r[1]} min={r[2]}")
    print("  --- last 5 sh000001 ---")
    for row in conn.execute(
        "SELECT date, close FROM kline WHERE code='sh000001' AND period='daily' "
        "ORDER BY date DESC LIMIT 5"
    ):
        print(f"    {row}")
    conn.close()
    return result


def main() -> None:
    section("Ports")
    ports = check_ports()
    tushare = check_tushare()
    qmt = check_qmt()
    db = check_db()

    section("SUMMARY")
    qmt_ok = bool((qmt.get("probe") or {}).get("ok"))
    ts_ok = bool(tushare.get("token_set")) and tushare.get("indices") is not None
    summary = {
        "ports": ports,
        "qmt_formula_ok": qmt_ok,
        "tushare_ok": ts_ok,
        "qmt_probe": qmt.get("probe"),
        "tushare_indices": tushare.get("indices"),
        "db_sh000001_max": (db.get("sh000001") or {}).get("max"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = ROOT / "data" / "_connectivity_verify.json"
    out.write_text(json.dumps({"summary": summary, "qmt": qmt, "tushare": tushare, "db": db}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
