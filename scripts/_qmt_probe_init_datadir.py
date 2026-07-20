# -*- coding: utf-8 -*-
"""Probe init_data_dir + local read. QMT pythonw only."""
from xtquant import xtdata
import json
import inspect
import os
from pathlib import Path

OUT = Path(r"D:\.workbuddy\2026-06-27-21-35-52\board-app\data\_qmt_probe4.json")
MAIN = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\datadir"
MINI = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini\datadir"
out = {}

# signatures
for n in ("init_data_dir", "reconnect", "get_local_data", "download_history_data"):
    fn = getattr(xtdata, n, None)
    if fn:
        try:
            out["sig_" + n] = str(inspect.signature(fn))
        except Exception as e:
            out["sig_" + n] = repr(e)[:120]
            # fallback: docstring first line
            out["doc_" + n] = (inspect.getdoc(fn) or "")[:300]

# reconnect first
try:
    xtdata.reconnect("127.0.0.1", 58600)
    c = xtdata.get_client()
    out["connected"] = bool(c and c.is_connected())
except Exception as e:
    out["reconnect_err"] = str(e)[:200]

out["data_dir_before"] = str(getattr(xtdata, "data_dir", ""))
out["default_data_dir"] = str(getattr(xtdata, "default_data_dir", ""))

results = {}
for label, path in (("main", MAIN), ("mini", MINI)):
    entry = {"path": path}
    # try init_data_dir
    try:
        r = xtdata.init_data_dir(path)
        entry["init_ret"] = str(r)[:100]
    except TypeError as e:
        entry["init_type_err"] = str(e)[:120]
        try:
            r = xtdata.init_data_dir()
            entry["init_noarg"] = str(r)[:100]
        except Exception as e2:
            entry["init_noarg_err"] = str(e2)[:120]
    except Exception as e:
        entry["init_err"] = str(e)[:200]

    entry["data_dir_after"] = str(getattr(xtdata, "data_dir", ""))

    # also absolute path assignment
    try:
        xtdata.data_dir = path
        entry["assign_ok"] = True
        entry["data_dir_assigned"] = str(xtdata.data_dir)
    except Exception as e:
        entry["assign_err"] = str(e)[:120]

    # local read several period/count combos
    combos = [
        ("1d", "20200101", "20260717", 5),
        ("1d", "20260701", "20260717", -1),
        ("1d", "20260701", "20260717", 0),
        ("1d", "", "", 5),
        ("1d", "20260701", "20260717", 100),
    ]
    for period, s, e, count in combos:
        key = f"{period}_{s}_{e}_c{count}"
        try:
            d = xtdata.get_local_data(
                ["time", "open", "high", "low", "close", "volume"],
                ["000001.SH"], period, s, e, count=count
            )
            df = d.get("000001.SH") if isinstance(d, dict) else None
            entry[key] = {
                "type": type(d).__name__,
                "keys": list(d.keys())[:5] if isinstance(d, dict) else None,
                "len": 0 if df is None else int(len(df)),
                "empty": True if df is None else bool(getattr(df, "empty", True)),
            }
            if df is not None and hasattr(df, "tail") and not df.empty:
                entry[key]["tail"] = df.tail(2).astype(str).to_dict("records")
        except Exception as ex:
            entry[key] = {"err": str(ex)[:150]}

    # download_history_data2 if any
    try:
        if hasattr(xtdata, "download_history_data2"):
            xtdata.download_history_data2(
                ["000001.SH"], period="1d",
                start_time="20260701", end_time="20260717"
            )
            entry["dl2"] = "ok"
    except Exception as e:
        entry["dl2_err"] = str(e)[:150]

    try:
        d = xtdata.get_local_data(
            ["time", "close", "volume"], ["000001.SH"], "1d",
            "20260701", "20260717", count=10
        )
        df = d.get("000001.SH") if isinstance(d, dict) else None
        entry["after_dl2"] = {
            "len": 0 if df is None else int(len(df)),
            "empty": True if df is None else bool(getattr(df, "empty", True)),
            "tail": (
                df.tail(2).astype(str).to_dict("records")
                if df is not None and hasattr(df, "tail") and not df.empty else []
            ),
        }
    except Exception as e:
        entry["after_dl2_err"] = str(e)[:150]

    results[label] = entry

out["results"] = results
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("WROTE", str(OUT))
