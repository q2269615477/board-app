# -*- coding: utf-8 -*-
"""One-shot QMT probe after user login. Run with QMT pythonw."""
from xtquant import xtdata
import json
import os
from pathlib import Path

OUT = Path(r"D:\.workbuddy\2026-06-27-21-35-52\board-app\data\_qmt_probe3.json")
MAIN = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\datadir"
MINI = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini\datadir"
out = {"cwd": os.getcwd(), "apis": []}

try:
    names = [n for n in dir(xtdata) if not n.startswith("_")]
    out["apis"] = [
        n for n in names
        if any(k in n.lower() for k in [
            "connect", "reconnect", "rpc", "download", "local",
            "market", "client", "data_dir", "tick", "quote", "init"
        ])
    ]
except Exception as e:
    out["apis_err"] = str(e)[:200]

# Prefer main QMT datadir (full client cache)
for path in (MAIN, MINI):
    key = "main" if path == MAIN else "mini"
    entry = {"path": path}
    try:
        os.environ["XTDATA_DATA_DIR"] = path
        if hasattr(xtdata, "data_dir"):
            try:
                xtdata.data_dir = path
                entry["data_dir_set"] = True
            except Exception as e:
                entry["data_dir_set_err"] = str(e)[:120]
        # connect variants
        try:
            xtdata.reconnect("127.0.0.1", 58600)
            c = xtdata.get_client()
            entry["reconnect"] = bool(c and c.is_connected())
        except Exception as e:
            entry["reconnect_err"] = str(e)[:120]
        entry["data_dir_now"] = str(getattr(xtdata, "data_dir", ""))

        # local without download
        d = xtdata.get_local_data(
            ["time", "open", "high", "low", "close", "volume"],
            ["000001.SH"], "1d", "20200101", "20260717", count=5
        )
        df = d.get("000001.SH") if isinstance(d, dict) else None
        entry["local_no_dl"] = {
            "len": 0 if df is None else int(len(df)),
            "empty": True if df is None else bool(df.empty),
            "tail": (
                df.tail(2).astype(str).to_dict("records")
                if df is not None and hasattr(df, "tail") and not df.empty else []
            ),
        }

        # download then local
        try:
            xtdata.download_history_data("000001.SH", "1d", "20260701", "20260717")
            entry["download"] = "ok"
        except Exception as e:
            entry["download_err"] = str(e)[:120]
        d2 = xtdata.get_local_data(
            ["time", "close", "volume"],
            ["000001.SH"], "1d", "20260701", "20260717", count=5
        )
        df2 = d2.get("000001.SH") if isinstance(d2, dict) else None
        entry["local_after_dl"] = {
            "len": 0 if df2 is None else int(len(df2)),
            "empty": True if df2 is None else bool(df2.empty),
            "tail": (
                df2.tail(2).astype(str).to_dict("records")
                if df2 is not None and hasattr(df2, "tail") and not df2.empty else []
            ),
        }

        # market_data_ex
        try:
            mdx = xtdata.get_market_data_ex(
                ["time", "open", "high", "low", "close", "volume"],
                ["000001.SH"], period="1d",
                start_time="20260701", end_time="20260717", count=5
            )
            mdf = mdx.get("000001.SH") if isinstance(mdx, dict) else None
            entry["mdx"] = {
                "len": 0 if mdf is None else int(len(mdf)),
                "empty": True if mdf is None else bool(mdf.empty),
            }
        except Exception as e:
            entry["mdx_err"] = str(e)[:120]

        # sector list
        try:
            lst = xtdata.get_stock_list_in_sector("沪深A股")
            entry["sector_a"] = len(lst) if lst else 0
            entry["sector_head"] = list(lst)[:3] if lst else []
        except Exception as e:
            entry["sector_err"] = str(e)[:120]

        # instrument detail
        try:
            det = xtdata.get_instrument_detail("000001.SH") or {}
            entry["detail_keys"] = len(det)
            entry["detail"] = {k: det.get(k) for k in list(det.keys())[:8]}
        except Exception as e:
            entry["detail_err"] = str(e)[:120]

    except Exception as e:
        entry["fatal"] = str(e)[:200]
    out[key] = entry

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("WROTE", str(OUT))
