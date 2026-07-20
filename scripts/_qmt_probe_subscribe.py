# -*- coding: utf-8 -*-
"""Probe subscribe + market data + period variants after login."""
from xtquant import xtdata
import json
import time
from pathlib import Path

OUT = Path(r"D:\.workbuddy\2026-06-27-21-35-52\board-app\data\_qmt_probe6.json")
MAIN = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\datadir"
out = {}

# reconnect (default port if any)
try:
    r = xtdata.reconnect("127.0.0.1", 58600)
    out["reconnect"] = str(r)[:80]
except Exception as e:
    out["reconnect_err"] = str(e)[:150]
c = xtdata.get_client()
out["connected"] = bool(c and c.is_connected())

# subscribe
for code in ("000001.SH", "600519.SH"):
    try:
        sid = xtdata.subscribe_quote(code, period="1d", start_time="20260701", end_time="20260717", count=20)
        out["sub_" + code] = str(sid)[:80]
    except Exception as e:
        out["sub_err_" + code] = str(e)[:150]

time.sleep(2)

# after subscribe: market data
try:
    md = xtdata.get_market_data(
        ["time", "open", "high", "low", "close", "volume"],
        ["000001.SH", "600519.SH"], "1d", "20260701", "20260717", count=10
    )
    entry = {"type": type(md).__name__}
    if isinstance(md, dict):
        entry["keys"] = list(md.keys())
        for k, v in md.items():
            if hasattr(v, "shape"):
                entry[str(k)] = {
                    "shape": list(v.shape),
                    "sample": v.iloc[:2, :3].astype(str).to_dict() if hasattr(v, "iloc") and v.shape[1] > 0 else str(v)[:120],
                }
            else:
                entry[str(k)] = str(v)[:120]
    out["md_after_sub"] = entry
except Exception as e:
    out["md_err"] = str(e)[:200]

try:
    mdx = xtdata.get_market_data_ex(
        ["time", "open", "high", "low", "close", "volume"],
        ["000001.SH"], period="1d",
        start_time="20260701", end_time="20260717", count=10
    )
    df = mdx.get("000001.SH") if isinstance(mdx, dict) else None
    out["mdx_after_sub"] = {
        "len": 0 if df is None else int(len(df)),
        "empty": True if df is None else bool(df.empty),
        "tail": (
            df.tail(3).astype(str).to_dict("records")
            if df is not None and hasattr(df, "tail") and not df.empty else []
        ),
    }
except Exception as e:
    out["mdx_err"] = str(e)[:200]

# period as 86400?
for period in ("1d", "day", "86400", "1D"):
    try:
        d = xtdata.get_local_data(
            ["time", "close", "volume"], ["000001.SH"], period,
            "20260601", "20260717", count=10, data_dir=MAIN
        )
        df = d.get("000001.SH") if isinstance(d, dict) else None
        out["period_" + str(period)] = {
            "len": 0 if df is None else int(len(df)),
            "empty": True if df is None else bool(getattr(df, "empty", True)),
            "tail": (
                df.tail(2).astype(str).to_dict("records")
                if df is not None and hasattr(df, "tail") and not df.empty else []
            ),
        }
    except Exception as e:
        out["period_" + str(period)] = {"err": str(e)[:150]}

# download_history_data2 again after sub
try:
    xtdata.download_history_data2(
        stock_list=["000001.SH"], period="1d",
        start_time="20260701", end_time="20260717"
    )
    out["dl2"] = "ok"
except Exception as e:
    out["dl2_err"] = str(e)[:200]

try:
    d = xtdata.get_local_data(
        ["time", "close", "volume"], ["000001.SH"], "1d",
        "20260701", "20260717", count=10, data_dir=MAIN
    )
    df = d.get("000001.SH") if isinstance(d, dict) else None
    out["after_dl2"] = {
        "len": 0 if df is None else int(len(df)),
        "empty": True if df is None else bool(getattr(df, "empty", True)),
        "tail": (
            df.tail(2).astype(str).to_dict("records")
            if df is not None and hasattr(df, "tail") and not df.empty else []
        ),
    }
except Exception as e:
    out["after_dl2_err"] = str(e)[:150]

# client attrs
try:
    c = xtdata.get_client()
    attrs = {}
    for n in dir(c):
        if n.startswith("_"):
            continue
        if any(k in n.lower() for k in ("connect", "login", "quote", "status", "server", "data")):
            try:
                v = getattr(c, n)
                attrs[n] = str(v() if callable(v) else v)[:100]
            except Exception as e:
                attrs[n] = "err:" + str(e)[:60]
    out["client_attrs"] = attrs
except Exception as e:
    out["client_attrs_err"] = str(e)[:150]

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("WROTE", str(OUT))
