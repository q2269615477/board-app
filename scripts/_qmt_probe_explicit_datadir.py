# -*- coding: utf-8 -*-
"""get_local_data with explicit data_dir kwarg (critical)."""
from xtquant import xtdata
import json
from pathlib import Path

OUT = Path(r"D:\.workbuddy\2026-06-27-21-35-52\board-app\data\_qmt_probe5.json")
MAIN = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\datadir"
MINI = r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini\datadir"
out = {}

xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c and c.is_connected())

for label, path in (("main", MAIN), ("mini", MINI), ("default", None)):
    entry = {"path": path}
    try:
        kwargs = dict(
            field_list=["time", "open", "high", "low", "close", "volume"],
            stock_list=["000001.SH", "600519.SH", "000001.SZ"],
            period="1d",
            start_time="20260601",
            end_time="20260717",
            count=10,
        )
        if path is not None:
            kwargs["data_dir"] = path
        d = xtdata.get_local_data(**kwargs)
        entry["type"] = type(d).__name__
        entry["keys"] = list(d.keys()) if isinstance(d, dict) else None
        codes = {}
        if isinstance(d, dict):
            for code, df in d.items():
                if df is None:
                    codes[code] = None
                    continue
                codes[code] = {
                    "len": int(len(df)),
                    "empty": bool(getattr(df, "empty", True)),
                    "cols": list(df.columns) if hasattr(df, "columns") else [],
                    "tail": (
                        df.tail(3).astype(str).to_dict("records")
                        if hasattr(df, "tail") and not df.empty else []
                    ),
                }
        entry["codes"] = codes
    except Exception as e:
        entry["err"] = str(e)[:300]
    out[label] = entry

# try relative path from bin.x64
for rel in ("../datadir", "../userdata_mini/datadir", "../userdata/datadir"):
    entry = {"path": rel}
    try:
        d = xtdata.get_local_data(
            ["time", "close", "volume"], ["000001.SH"], "1d",
            "20260601", "20260717", count=10, data_dir=rel
        )
        df = d.get("000001.SH") if isinstance(d, dict) else None
        entry["len"] = 0 if df is None else int(len(df))
        entry["empty"] = True if df is None else bool(df.empty)
        if df is not None and hasattr(df, "tail") and not df.empty:
            entry["tail"] = df.tail(2).astype(str).to_dict("records")
    except Exception as e:
        entry["err"] = str(e)[:200]
    out["rel_" + rel.replace("/", "_").replace(".", "")] = entry

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print("WROTE", str(OUT))
