"""Deep probe: why local DAT exists but get_local_data returns empty."""
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_DATA_DIR, QMT_MINI_DATA_DIR

OUT = ROOT / "data" / "_qmt_probe_deep_read.json"
MAIN = str(QMT_DATA_DIR)
MINI = str(QMT_MINI_DATA_DIR)
DAT = Path(MAIN) / "SH" / "86400" / "000001.DAT"
DAT_MINI = Path(MINI) / "SH" / "86400" / "000001.DAT"

# --- host-side DAT header peek (no xtquant) ---
def peek_dat(path: Path) -> dict:
    info = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return info
    st = path.stat()
    info["size"] = st.st_size
    info["mtime"] = st.st_mtime
    try:
        raw = path.read_bytes()[:128]
        info["head_hex"] = raw[:64].hex()
        # common QMT kline: fixed record; try float count
        # guess record size by factors of size
        for rec in (32, 36, 40, 48, 52, 56, 64, 72, 80):
            if st.st_size % rec == 0 and st.st_size // rec > 10:
                info.setdefault("size_divisors", []).append(
                    {"rec": rec, "n": st.st_size // rec}
                )
        # try first few uint32/double
        if len(raw) >= 40:
            u32 = struct.unpack_from("<8I", raw, 0)
            info["u32_head"] = list(u32)
    except Exception as e:
        info["peek_err"] = str(e)[:120]
    return info


host = {
    "dat_main": peek_dat(DAT),
    "dat_mini": peek_dat(DAT_MINI),
}

probe = f'''
from xtquant import xtdata
import json, inspect, traceback
from pathlib import Path

MAIN = r"{MAIN}"
MINI = r"{MINI}"
out = {{"apis": {{}}, "tries": []}}

# API inventory
names = [
    "reconnect","rpc_init","get_client","init_data_dir","data_dir","default_data_dir",
    "get_local_data","get_market_data","get_market_data_ex","get_full_tick",
    "download_history_data","download_history_data2","subscribe_quote","unsubscribe_quote",
    "get_instrument_detail","get_stock_list_in_sector","get_trading_dates",
]
for n in names:
    a = getattr(xtdata, n, None)
    if a is None:
        out["apis"][n] = None
    elif callable(a):
        try:
            out["apis"][n] = str(inspect.signature(a))[:200]
        except Exception:
            out["apis"][n] = "callable"
    else:
        out["apis"][n] = repr(a)[:120]

# connect
try:
    xtdata.reconnect("127.0.0.1", 58600)
    c = xtdata.get_client()
    out["connected"] = bool(c.is_connected()) if c else False
except Exception as e:
    out["connected"] = False
    out["connect_err"] = str(e)[:160]

def describe(d, code="000001.SH"):
    entry = {{"type": type(d).__name__}}
    if d is None:
        entry["none"] = True
        return entry
    if isinstance(d, dict):
        entry["keys"] = list(d.keys())[:8]
        v = d.get(code)
        if v is None:
            entry["value"] = None
        else:
            entry["value_type"] = type(v).__name__
            if hasattr(v, "empty"):
                entry["empty"] = bool(v.empty)
                entry["rows"] = int(len(v)) if not v.empty else 0
                if not v.empty:
                    entry["index0"] = str(v.index[0])
                    entry["index_last"] = str(v.index[-1])
                    entry["cols"] = list(v.columns)[:12]
                    try:
                        entry["close_last"] = float(v.iloc[-1].get("close", v.iloc[-1][0] if len(v.columns) else 0))
                    except Exception:
                        pass
            elif isinstance(v, (list, tuple)):
                entry["rows"] = len(v)
            else:
                entry["value_repr"] = repr(v)[:120]
    else:
        entry["repr"] = repr(d)[:160]
    return entry

def try_call(label, fn):
    e = {{"label": label}}
    try:
        d = fn()
        e.update(describe(d))
    except Exception as ex:
        e["err"] = str(ex)[:200]
        e["tb"] = traceback.format_exc()[-300:]
    out["tries"].append(e)

# set data_dir variants
for label, path in [("assign_main", MAIN), ("assign_mini", MINI)]:
    try:
        xtdata.data_dir = path
        out[label] = str(getattr(xtdata, "data_dir", ""))
    except Exception as e:
        out[label] = str(e)[:80]

# init_data_dir
for path in [MAIN, MINI, None]:
    lab = f"init_{{Path(path).name if path else 'default'}}"
    try:
        if path:
            r = xtdata.init_data_dir(path)
        else:
            r = xtdata.init_data_dir()
        out.setdefault("init_results", {{}})[lab] = {{"ok": True, "ret": repr(r)[:80], "data_dir": str(getattr(xtdata, "data_dir", ""))}}
    except TypeError:
        try:
            r = xtdata.init_data_dir()
            out.setdefault("init_results", {{}})[lab] = {{"ok": True, "ret": repr(r)[:80], "note": "no-arg"}}
        except Exception as e:
            out.setdefault("init_results", {{}})[lab] = {{"ok": False, "err": str(e)[:120]}}
    except Exception as e:
        out.setdefault("init_results", {{}})[lab] = {{"ok": False, "err": str(e)[:120]}}

# field / period / count / data_dir matrix for get_local_data
periods = ["1d", "86400", "day", "1day"]
counts = [0, 5, -1, 10]
dirs = [MAIN, MINI, None]
# positional style used by board-app
try_call("local_pos_main_1d_c5", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "1d", "20260101", "20260717", 5, MAIN
))
try_call("local_kw_main_1d_c5", lambda: xtdata.get_local_data(
    field_list=["time","open","high","low","close","volume"],
    stock_list=["000001.SH"], period="1d",
    start_time="20260101", end_time="20260717", count=5, data_dir=MAIN
))
try_call("local_kw_mini_1d_c5", lambda: xtdata.get_local_data(
    field_list=["time","open","high","low","close","volume"],
    stock_list=["000001.SH"], period="1d",
    start_time="20260101", end_time="20260717", count=5, data_dir=MINI
))
try_call("local_kw_none_1d_c5", lambda: xtdata.get_local_data(
    field_list=["time","open","high","low","close","volume"],
    stock_list=["000001.SH"], period="1d",
    start_time="20260101", end_time="20260717", count=5
))
try_call("local_period_86400", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "86400", "20260101", "20260717", 5, MAIN
))
try_call("local_count0_wide", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "1d", "20200101", "20260717", 0, MAIN
))
try_call("local_count_neg1", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "1d", "20200101", "20260717", -1, MAIN
))
try_call("local_empty_fields", lambda: xtdata.get_local_data(
    [], ["000001.SH"], "1d", "20200101", "20260717", 5, MAIN
))
try_call("local_no_time_range", lambda: xtdata.get_local_data(
    stock_list=["000001.SH"], period="1d", count=5, data_dir=MAIN
))

# market data APIs
if hasattr(xtdata, "get_market_data"):
    try_call("market_data_1d", lambda: xtdata.get_market_data(
        ["open","high","low","close","volume"], ["000001.SH"], "1d", "20260601", "20260717", count=5
    ))
if hasattr(xtdata, "get_market_data_ex"):
    try_call("market_data_ex_1d", lambda: xtdata.get_market_data_ex(
        ["open","high","low","close","volume"], ["000001.SH"], "1d", "20260601", "20260717", count=5
    ))
if hasattr(xtdata, "get_full_tick"):
    try_call("full_tick", lambda: xtdata.get_full_tick(["000001.SH"]))

# trading dates
if hasattr(xtdata, "get_trading_dates"):
    try:
        td = xtdata.get_trading_dates("SH", "20260701", "20260717")
        out["trading_dates"] = {{"type": type(td).__name__, "n": len(td) if td is not None else 0, "tail": list(td)[-5:] if td else []}}
    except Exception as e:
        out["trading_dates_err"] = str(e)[:160]

# download then re-read with short sleep
try:
    xtdata.download_history_data("000001.SH", "1d", "20260701", "20260717")
    out["download_ok"] = True
except Exception as e:
    out["download_ok"] = False
    out["download_err"] = str(e)[:200]

try_call("after_download_local", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "1d", "20260701", "20260717", 10, MAIN
))

# stock 600519 as alternate
try_call("local_600519", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["600519.SH"], "1d", "20260601", "20260717", 5, MAIN
))

# relative data_dir like xtquant default
rel = str(Path(MAIN).parent / "userdata_mini" / "datadir")
try_call("local_rel_mini_path", lambda: xtdata.get_local_data(
    ["time","open","high","low","close","volume"], ["000001.SH"], "1d", "20260601", "20260717", 5, rel
))

print(json.dumps(out, ensure_ascii=False, default=str))
'''

def main():
    result = {"host": host, "meta": {
        "qmt_python": QMT_PYTHON_PATH,
        "main": MAIN,
        "mini": MINI,
    }}
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True, timeout=90, cwd=QMT_DIR,
    )
    result["returncode"] = proc.returncode
    stdout = (proc.stdout or b"").decode("utf-8", errors="ignore")
    stderr = (proc.stderr or b"").decode("utf-8", errors="ignore")
    result["stderr"] = stderr[-800:]
    result["stdout_tail"] = stdout[-500:]
    payload = {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except Exception:
                continue
    result["probe"] = payload
    # summary
    rows = []
    for t in payload.get("tries") or []:
        if t.get("rows"):
            rows.append({"label": t.get("label"), "rows": t.get("rows"), "close": t.get("close_last")})
    result["summary"] = {
        "connected": payload.get("connected"),
        "nonzero_tries": rows,
        "any_rows": bool(rows),
        "download_ok": payload.get("download_ok"),
        "download_err": payload.get("download_err"),
        "n_tries": len(payload.get("tries") or []),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("wrote", OUT)
    # print interesting tries
    for t in (payload.get("tries") or [])[:20]:
        brief = {k: t[k] for k in ("label", "rows", "empty", "err", "type", "keys", "value_type", "close_last") if k in t}
        print(json.dumps(brief, ensure_ascii=False))


if __name__ == "__main__":
    main()
