"""Inspect IPythonApiClient methods + raw get_market_data3 / debug."""
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_rpc_methods.json"

probe = r'''
from xtquant import xtdata
import json, traceback

out = {}
xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c.is_connected()) if c else False
out["client_type"] = type(c).__name__

# list public methods
methods = []
for name in dir(c):
    if name.startswith("_"):
        continue
    try:
        attr = getattr(c, name)
        methods.append(name if callable(attr) else f"{name}={type(attr).__name__}")
    except Exception:
        methods.append(name)
out["methods"] = methods

# call meta getters
for name in ("get_data_dir", "get_app_dir", "get_sector_list"):
    if hasattr(c, name):
        try:
            r = getattr(c, name)()
            if isinstance(r, (list, tuple)):
                out[name] = {"n": len(r), "sample": list(r)[:5]}
            else:
                out[name] = repr(r)[:200]
        except Exception as e:
            out[name] = f"ERR:{e}"[:160]

# raw get_market_data3 variants
def call_gmd3(label, *args):
    e = {"label": label}
    try:
        r = c.get_market_data3(*args)
        e["type"] = type(r).__name__
        e["repr"] = repr(r)[:300]
        if isinstance(r, (list, tuple)):
            e["len"] = len(r)
            if r:
                e["item0_type"] = type(r[0]).__name__
                e["item0_repr"] = repr(r[0])[:200]
        if isinstance(r, dict):
            e["keys"] = list(r.keys())[:10]
    except Exception as ex:
        e["err"] = str(ex)[:200]
        e["tb"] = traceback.format_exc()[-250:]
    return e

# signature from xtdata.py:
# get_market_data3(field_list, stock_list, period, start_time, end_time, count,
#   dividend_type, fill_data, version, enable_read_from_local, enable_read_from_server, debug_mode)

fields = ["time", "open", "high", "low", "close", "volume"]
stocks = ["000001.SH"]
tries = []
for version in ("v2", "v3", "v4", ""):
    for local, server, dbg in [
        (True, True, 0),
        (True, False, 0),
        (False, True, 0),
        (True, True, 1),
        (False, True, 1),
    ]:
        tries.append(call_gmd3(
            f"{version}|L{int(local)}S{int(server)}D{dbg}",
            fields, stocks, "1d", "20260601", "20260717", 5,
            "none", True, version, local, server, dbg
        ))

# period variants
for period in ("1d", "1m", "5m", "tick", "86400"):
    tries.append(call_gmd3(
        f"period={period}",
        fields, stocks, period, "20260701", "20260717", 5,
        "none", True, "v4", True, True, 0
    ))

# stock code format variants
for code in ("000001.SH", "SH000001", "000001", "000001.SS"):
    tries.append(call_gmd3(
        f"code={code}",
        ["close"], [code], "1d", "20260701", "20260717", 3,
        "none", True, "v4", True, True, 0
    ))

out["gmd3_tries"] = tries

# download then gmd3
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    out["dl"] = "ok"
except Exception as e:
    out["dl"] = str(e)[:160]

out["after_dl"] = call_gmd3(
    "after_dl_v4",
    fields, stocks, "1d", "20260601", "20260717", 10,
    "none", True, "v4", True, True, 1
)

# subscribe raw
try:
    from xtquant import xtbson as bson
    meta = bson.BSON.encode({"stockCode": "000001.SH", "period": "1d"})
    region = bson.BSON.encode({"startTime": "", "endTime": "", "count": 5})
    sid = c.subscribe_quote(meta, region, None)
    out["raw_subscribe"] = sid
except Exception as e:
    out["raw_subscribe_err"] = str(e)[:200]

# instrument detail raw
try:
    inst = c.get_instrument_detail("000001.SH")
    out["raw_detail_type"] = type(inst).__name__
    out["raw_detail"] = repr(inst)[:400]
except Exception as e:
    out["raw_detail_err"] = str(e)[:160]

print(json.dumps(out, ensure_ascii=False, default=str))
'''

def main():
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True, timeout=90, cwd=QMT_DIR,
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
    result = {"payload": payload, "stderr": stderr[-800:], "rc": proc.returncode}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("connected", payload.get("connected"))
    print("methods_n", len(payload.get("methods") or []))
    print("methods_sample", (payload.get("methods") or [])[:40])
    print("get_data_dir", payload.get("get_data_dir"))
    print("get_app_dir", payload.get("get_app_dir"))
    print("raw_subscribe", payload.get("raw_subscribe") or payload.get("raw_subscribe_err"))
    print("raw_detail", str(payload.get("raw_detail") or payload.get("raw_detail_err"))[:200])
    # show non-empty gmd3
    nonempty = []
    for t in payload.get("gmd3_tries") or []:
        rep = t.get("repr", "")
        if t.get("err") or (rep and rep not in ("None", "[]", "{}", "()")):
            if t.get("err") or (t.get("len", 0) not in (0, None) and rep != "[]"):
                nonempty.append({k: t[k] for k in ("label", "type", "len", "err", "repr") if k in t})
    print("interesting tries", json.dumps(nonempty[:15], ensure_ascii=False, indent=2)[:2000])
    print("after_dl", json.dumps(payload.get("after_dl"), ensure_ascii=False)[:500])
    print("wrote", OUT)

if __name__ == "__main__":
    main()
