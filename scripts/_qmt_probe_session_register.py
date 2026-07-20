"""Probe official session registration on full-QMT 58600 (no Mini, no DAT crack).

Key config facts:
- full QMT xtclient.lua: 58600 tag=server_formula (formula/iPython notebook)
- MiniQMT xtminiquote.lua: 58610 tag=server_ipythonapi
- get_data_dir/get_app_dir empty => data service not registered on this RPC session
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_DATA_DIR

OUT = ROOT / "data" / "_qmt_probe_session_register.json"

APP_ROOT = str(Path(QMT_DIR).resolve().parent)
DATA_DIR = str(Path(QMT_DATA_DIR).resolve())

probe = rf'''
from xtquant import xtdata
import json, traceback, os, time

out = {{
    "cwd": os.getcwd(),
    "APP_ROOT": r"{APP_ROOT}",
    "DATA_DIR": r"{DATA_DIR}",
    "xtdata_file": getattr(xtdata, "__file__", ""),
}}

xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c and c.is_connected())
out["client_type"] = type(c).__name__ if c else None

def safe_call(name, *args):
    e = {{"name": name, "args": [str(a)[:80] for a in args]}}
    try:
        fn = getattr(c, name)
        r = fn(*args) if args else fn()
        e["type"] = type(r).__name__
        if isinstance(r, (bytes, bytearray)):
            e["repr"] = r[:200].hex() if len(r) else "empty_bytes"
            try:
                e["text"] = r.decode("utf-8", errors="ignore")[:200]
            except Exception:
                pass
        elif isinstance(r, (list, tuple)):
            e["len"] = len(r)
            e["sample"] = [repr(x)[:80] for x in list(r)[:5]]
        elif isinstance(r, dict):
            e["keys"] = list(r.keys())[:20]
            e["repr"] = repr(r)[:300]
        else:
            e["repr"] = repr(r)[:300]
    except Exception as ex:
        e["err"] = str(ex)[:200]
        e["tb"] = traceback.format_exc()[-200:]
    return e

# baseline meta
for m in ("get_data_dir", "get_app_dir", "get_server_tag", "get_sector_list",
          "get_holidays", "get_all_subscribe_quote"):
    if hasattr(c, m):
        out[m + "_before"] = safe_call(m)

# try set_app_dir to full QMT root / bin / datadir parents
set_results = []
for path in [
    r"{APP_ROOT}",
    r"{APP_ROOT}\\bin.x64",
    r"{APP_ROOT}\\datadir",
    r"{DATA_DIR}",
    os.path.dirname(r"{DATA_DIR}"),
]:
    if hasattr(c, "set_app_dir"):
        set_results.append(safe_call("set_app_dir", path))
        set_results.append({{"after_set": path,
                            "get_app_dir": safe_call("get_app_dir"),
                            "get_data_dir": safe_call("get_data_dir")}})
out["set_app_dir_tries"] = set_results

# load_config / reset / re-init paths
if hasattr(c, "load_config"):
    cfg = os.path.join(os.path.dirname(xtdata.__file__), "xtdata.ini")
    out["load_config"] = safe_call("load_config", cfg)

# commonControl probes (official generic control channel)
cc_cmds = []
if hasattr(c, "commonControl"):
    for cmd in [
        "get_data_dir", "getDataDir", "GetDataDir",
        "get_app_dir", "getAppDir",
        "status", "info", "ping", "version",
        "init", "init_data", "start_quote", "login",
        "server_tag", "get_server_tag",
    ]:
        for payload in ("", "{{}}", "null", "1"):
            try:
                r = c.commonControl(cmd, payload)
                entry = {{"cmd": cmd, "payload": payload, "type": type(r).__name__,
                         "repr": repr(r)[:200]}}
                if r not in (None, "", 0, -1, b"", {{}}):
                    entry["interesting"] = True
                cc_cmds.append(entry)
            except Exception as ex:
                cc_cmds.append({{"cmd": cmd, "payload": payload, "err": str(ex)[:120]}})
out["commonControl"] = [x for x in cc_cmds if x.get("interesting") or x.get("err")][:40]
out["commonControl_n"] = len(cc_cmds)
out["commonControl_nonempty"] = [x for x in cc_cmds if x.get("interesting")][:20]

# after set_app_dir: try bars again
meta_after = {{}}
for m in ("get_data_dir", "get_app_dir", "get_server_tag"):
    if hasattr(c, m):
        meta_after[m] = safe_call(m)
out["meta_after"] = meta_after

# market data after registration attempts
bars = {{}}
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    bars["dl"] = "ok"
except Exception as e:
    bars["dl"] = str(e)[:160]

try:
    d = xtdata.get_market_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    if isinstance(d, dict) and "close" in d:
        df = d["close"]
        bars["market_data_close_shape"] = list(getattr(df, "shape", []))
        bars["market_data_empty"] = bool(getattr(df, "empty", True))
        if hasattr(df, "shape") and df.shape[1] > 0:
            bars["market_data_vals"] = [float(x) for x in list(df.iloc[0].dropna().values[-3:])]
    else:
        bars["market_data_type"] = type(d).__name__
        bars["market_data_repr"] = repr(d)[:200]
except Exception as e:
    bars["market_data_err"] = str(e)[:160]

try:
    d2 = xtdata.get_local_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=5,
                               data_dir=r"{DATA_DIR}")
    df2 = d2.get("000001.SH") if isinstance(d2, dict) else None
    if df2 is not None and hasattr(df2, "empty"):
        bars["local_rows"] = 0 if df2.empty else int(len(df2))
        bars["local_shape"] = list(df2.shape)
    else:
        bars["local_repr"] = repr(d2)[:160]
except Exception as e:
    bars["local_err"] = str(e)[:160]

try:
    det = xtdata.get_instrument_detail("000001.SH") or {{}}
    bars["detail_keys"] = list(det.keys())[:15] if isinstance(det, dict) else type(det).__name__
    bars["detail_ok"] = bool(isinstance(det, dict) and (det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice")))
except Exception as e:
    bars["detail_err"] = str(e)[:120]

try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=3)
    bars["subscribe_id"] = int(sid) if sid is not None else None
except Exception as e:
    bars["subscribe_err"] = str(e)[:120]

try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    bars["sector_n"] = len(lst)
except Exception as e:
    bars["sector_err"] = str(e)[:80]

out["bars"] = bars
out["verdict"] = {{
    "connected": out.get("connected"),
    "get_data_dir": (meta_after.get("get_data_dir") or {{}}).get("repr"),
    "get_app_dir": (meta_after.get("get_app_dir") or {{}}).get("repr"),
    "get_server_tag": (meta_after.get("get_server_tag") or {{}}).get("repr"),
    "local_rows": bars.get("local_rows", 0),
    "detail_ok": bars.get("detail_ok", False),
    "subscribe_id": bars.get("subscribe_id"),
    "sector_n": bars.get("sector_n", 0),
    "pure_qmt_ready": bool(bars.get("local_rows", 0) or bars.get("detail_ok") or bars.get("market_data_vals")),
}}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def main():
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True,
        timeout=90,
        cwd=QMT_DIR,
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
        "stderr": stderr[-1000:],
        "rc": proc.returncode,
        "notes": {
            "full_qmt_58600_tag": "server_formula (xtclient.lua)",
            "mini_58610_tag": "server_ipythonapi (xtminiquote.lua)",
            "install_xtdata_ini": "config/xtdata.ini address=127.0.0.1:58670",
            "package_xtdata_ini": "site-packages/xtquant/xtdata.ini address=127.0.0.1:58610",
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    v = payload.get("verdict") or {}
    print("connected", v.get("connected"))
    print("get_data_dir", v.get("get_data_dir"))
    print("get_app_dir", v.get("get_app_dir"))
    print("get_server_tag", v.get("get_server_tag"))
    print("local_rows", v.get("local_rows"))
    print("detail_ok", v.get("detail_ok"))
    print("subscribe_id", v.get("subscribe_id"))
    print("sector_n", v.get("sector_n"))
    print("pure_qmt_ready", v.get("pure_qmt_ready"))
    print("commonControl_nonempty", json.dumps(payload.get("commonControl_nonempty"), ensure_ascii=False)[:800])
    print("bars", json.dumps(payload.get("bars"), ensure_ascii=False)[:600])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
