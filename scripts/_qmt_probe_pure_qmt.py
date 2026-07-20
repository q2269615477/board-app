"""Pure full-QMT channel probe (no MiniQMT). Official xtquant APIs only."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_pure_qmt.json"

# Critical discovery from xtdata.py:
# - get_local_data(..., period in 1m/5m/1d) IGNORES data_dir kwarg
#   and calls get_market_data3 with enable_read_from_server=False
# - get_market_data uses enable_read_from_server=True (default)
# - Real data path is always client RPC, never Python-side DAT parse

probe = r'''
from xtquant import xtdata
import json, time, traceback, os

out = {
    "cwd": os.getcwd(),
    "xtdata_file": getattr(xtdata, "__file__", ""),
}

def brief_df(df):
    if df is None:
        return {"none": True}
    if hasattr(df, "empty"):
        if df.empty:
            return {"empty": True, "shape": list(df.shape), "cols": list(getattr(df, "columns", []))}
        return {
            "rows": int(len(df)),
            "shape": list(df.shape),
            "cols": list(df.columns)[:12],
            "index0": str(df.index[0]),
            "index_last": str(df.index[-1]),
            "tail": df.tail(2).astype(object).where(df.notna(), None).to_dict(orient="list") if len(df) else {},
        }
    return {"type": type(df).__name__, "repr": repr(df)[:160]}

def brief_md(d):
    """get_market_data returns {field: DataFrame(index=stocks, columns=times)}"""
    if not isinstance(d, dict):
        return {"type": type(d).__name__, "repr": repr(d)[:120]}
    res = {}
    for k, v in list(d.items())[:8]:
        if hasattr(v, "shape"):
            res[k] = {
                "shape": list(v.shape),
                "empty": bool(getattr(v, "empty", False)),
                "index": [str(x) for x in list(v.index)[:3]],
                "cols_n": int(v.shape[1]) if hasattr(v, "shape") else 0,
                "cols_tail": [str(x) for x in list(v.columns)[-3:]] if hasattr(v, "columns") and len(v.columns) else [],
                "values_tail": None,
            }
            try:
                if not v.empty and v.shape[1] > 0:
                    res[k]["values_tail"] = [float(x) for x in list(v.iloc[0].dropna().values[-3:])]
            except Exception:
                pass
        else:
            res[k] = repr(v)[:80]
    return res

# 1) connect to full QMT 58600 only
try:
    xtdata.reconnect("127.0.0.1", 58600)
    c = xtdata.get_client()
    out["connected"] = bool(c.is_connected()) if c else False
except Exception as e:
    out["connected"] = False
    out["connect_err"] = str(e)[:200]
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

# 2) client meta
for meth in ("get_data_dir", "get_app_dir"):
    try:
        out[meth] = str(getattr(c, meth)() or "")
    except Exception as e:
        out[meth + "_err"] = str(e)[:120]

try:
    out["init_data_dir"] = str(xtdata.init_data_dir())
    out["xtdata_data_dir"] = str(getattr(xtdata, "data_dir", ""))
except Exception as e:
    out["init_err"] = str(e)[:120]

# 3) metadata APIs (always useful)
try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"] = len(lst)
    out["sector_sample"] = lst[:5]
except Exception as e:
    out["sector_err"] = str(e)[:120]

try:
    det = xtdata.get_instrument_detail("000001.SH") or {}
    out["detail"] = {k: det.get(k) for k in list(det.keys())[:20]} if isinstance(det, dict) else {}
    out["detail_ok"] = bool(det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice"))
except Exception as e:
    out["detail_err"] = str(e)[:120]
    out["detail_ok"] = False

try:
    td = xtdata.get_trading_dates("SH", "20260701", "20260718")
    out["trading_dates_n"] = len(td) if td else 0
    out["trading_dates_tail"] = list(td)[-5:] if td else []
except Exception as e:
    out["trading_dates_err"] = str(e)[:120]

# 4) Official path A: download then get_market_data (server+local)
path_a = {}
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    path_a["download"] = "ok"
except Exception as e:
    path_a["download"] = str(e)[:160]

try:
    md = xtdata.get_market_data(
        ["time", "open", "high", "low", "close", "volume"],
        ["000001.SH"], "1d", "20260601", "20260717", count=-1
    )
    path_a["market_data"] = brief_md(md)
    # extract bars if any
    if isinstance(md, dict) and "close" in md and hasattr(md["close"], "shape") and md["close"].shape[1] > 0:
        close = md["close"]
        path_a["bars_n"] = int(close.shape[1])
        path_a["last_col"] = str(close.columns[-1])
        path_a["last_close"] = float(close.iloc[0, -1])
except Exception as e:
    path_a["market_data_err"] = str(e)[:200]
    path_a["tb"] = traceback.format_exc()[-300:]

# 5) Official path B: get_market_data_ex
path_b = {}
try:
    mdex = xtdata.get_market_data_ex(
        ["time", "open", "high", "low", "close", "volume"],
        ["000001.SH", "600519.SH"],
        period="1d", start_time="20260601", end_time="20260717", count=10
    )
    path_b["type"] = type(mdex).__name__
    if isinstance(mdex, dict):
        path_b["codes"] = {}
        for code, df in mdex.items():
            path_b["codes"][code] = brief_df(df)
except Exception as e:
    path_b["err"] = str(e)[:200]

# 6) Official path C: get_local_data (server=False, local only)
# NOTE: data_dir param is IGNORED by xtdata for 1d (source confirmed)
path_c = {}
try:
    ld = xtdata.get_local_data(
        ["time", "open", "high", "low", "close", "volume"],
        ["000001.SH"], "1d", "20260601", "20260717", count=10
    )
    if isinstance(ld, dict):
        path_c["000001.SH"] = brief_df(ld.get("000001.SH"))
    else:
        path_c["raw"] = brief_df(ld)
except Exception as e:
    path_c["err"] = str(e)[:200]
    path_c["tb"] = traceback.format_exc()[-300:]

# 7) Official path D: subscribe_quote + short wait + re-read
path_d = {}
try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=5)
    path_d["subscribe_id"] = int(sid) if sid is not None else None
    time.sleep(2)
    md2 = xtdata.get_market_data(
        ["close", "open", "high", "low", "volume"],
        ["000001.SH"], "1d", "20260701", "20260717", count=5
    )
    path_d["after_sub"] = brief_md(md2)
    if isinstance(md2, dict) and "close" in md2 and hasattr(md2["close"], "shape"):
        path_d["bars_n"] = int(md2["close"].shape[1])
except Exception as e:
    path_d["err"] = str(e)[:200]

# 8) Official path E: download_history_data2 batch
path_e = {}
try:
    if hasattr(xtdata, "download_history_data2"):
        xtdata.download_history_data2(["000001.SH", "600519.SH"], "1d", "20260701", "20260717")
        path_e["download2"] = "ok"
        mdex2 = xtdata.get_market_data_ex(
            ["open", "high", "low", "close", "volume"],
            ["000001.SH", "600519.SH"], "1d", "20260701", "20260717", count=5
        )
        if isinstance(mdex2, dict):
            path_e["codes"] = {k: brief_df(v) for k, v in mdex2.items()}
    else:
        path_e["skip"] = "no download_history_data2"
except Exception as e:
    path_e["err"] = str(e)[:200]

# 9) full_tick
path_f = {}
try:
    tick = xtdata.get_full_tick(["000001.SH", "600519.SH"])
    path_f["type"] = type(tick).__name__
    if isinstance(tick, dict):
        path_f["keys"] = list(tick.keys())
        for k, v in list(tick.items())[:2]:
            path_f[k] = {kk: v.get(kk) for kk in list(v.keys())[:12]} if isinstance(v, dict) else repr(v)[:80]
except Exception as e:
    path_f["err"] = str(e)[:200]

out["path_A_market_data"] = path_a
out["path_B_market_data_ex"] = path_b
out["path_C_local_data"] = path_c
out["path_D_subscribe"] = path_d
out["path_E_download2"] = path_e
out["path_F_full_tick"] = path_f

# verdict
def any_bars(paths):
    for p in paths:
        if not isinstance(p, dict):
            continue
        if p.get("bars_n"):
            return True
        for v in p.values():
            if isinstance(v, dict) and v.get("rows"):
                return True
            if isinstance(v, dict) and "codes" in v:
                for c in v["codes"].values():
                    if isinstance(c, dict) and c.get("rows"):
                        return True
    return False

out["verdict"] = {
    "connected": out.get("connected"),
    "client_data_dir": out.get("get_data_dir"),
    "detail_ok": out.get("detail_ok"),
    "sector_n": out.get("sector_n"),
    "has_bars": any_bars([path_a, path_b, path_c, path_d, path_e]),
    "subscribe_id": path_d.get("subscribe_id"),
    "pure_qmt_ready": bool(out.get("connected")) and (
        any_bars([path_a, path_b, path_c, path_d, path_e]) or bool(out.get("detail_ok"))
    ),
}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def main():
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True, timeout=120, cwd=QMT_DIR,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="ignore")
    stderr = (proc.stderr or b"").decode("utf-8", errors="ignore")
    payload = {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    result = {
        "payload": payload,
        "rc": proc.returncode,
        "stderr": stderr[-500:],
        "stdout_tail": stdout[-300:],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload.get("verdict", payload), ensure_ascii=False, indent=2))
    # compact path summary
    for key in ("path_A_market_data", "path_B_market_data_ex", "path_C_local_data",
                "path_D_subscribe", "path_E_download2", "path_F_full_tick"):
        print(key, "=>", json.dumps(payload.get(key), ensure_ascii=False, default=str)[:400])
    print("meta:", json.dumps({
        "connected": payload.get("connected"),
        "get_data_dir": payload.get("get_data_dir"),
        "get_app_dir": payload.get("get_app_dir"),
        "detail_ok": payload.get("detail_ok"),
        "sector_n": payload.get("sector_n"),
    }, ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
