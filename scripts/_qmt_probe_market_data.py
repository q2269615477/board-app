"""Focus on get_market_data / get_market_data_ex payload shape."""
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_DATA_DIR

OUT = ROOT / "data" / "_qmt_probe_market_data.json"

probe = f'''
from xtquant import xtdata
import json
import numpy as np
import pandas as pd

MAIN = r"{QMT_DATA_DIR}"
out = {{}}
xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c.is_connected()) if c else False

def shape(obj, depth=0):
    if depth > 3:
        return type(obj).__name__
    if obj is None:
        return None
    if isinstance(obj, dict):
        d = {{}}
        for k, v in list(obj.items())[:8]:
            if hasattr(v, "shape"):
                d[str(k)] = {{
                    "type": type(v).__name__,
                    "shape": list(v.shape) if hasattr(v, "shape") else None,
                    "empty": bool(getattr(v, "empty", False)) if hasattr(v, "empty") else None,
                    "sample": _sample(v),
                }}
            elif isinstance(v, dict):
                d[str(k)] = shape(v, depth+1)
            else:
                d[str(k)] = {{"type": type(v).__name__, "repr": repr(v)[:120]}}
        return d
    if hasattr(obj, "shape"):
        return {{"type": type(obj).__name__, "shape": list(obj.shape), "sample": _sample(obj)}}
    return {{"type": type(obj).__name__, "repr": repr(obj)[:160]}}

def _sample(v):
    try:
        if hasattr(v, "empty") and v.empty:
            return "EMPTY_DF"
        if hasattr(v, "iloc"):
            return {{
                "cols": list(v.columns)[:8],
                "index_tail": [str(x) for x in list(v.index[-3:])],
                "tail": v.tail(2).to_dict(orient="list") if len(v) else {{}},
                "rows": int(len(v)),
            }}
        if hasattr(v, "shape"):
            arr = np.asarray(v)
            flat = arr.flatten()
            return {{
                "shape": list(arr.shape),
                "tail": [float(x) if np.isfinite(x) else str(x) for x in flat[-6:]],
                "nan_ratio": float(np.isnan(arr.astype(float)).mean()) if arr.size else 1,
            }}
    except Exception as e:
        return str(e)[:80]
    return None

# get_market_data variants
for label, kwargs in [
    ("md_1d_range", dict(field_list=["open","high","low","close","volume"], stock_list=["000001.SH"], period="1d", start_time="20260601", end_time="20260717", count=-1)),
    ("md_1d_count5", dict(field_list=["open","high","low","close","volume"], stock_list=["000001.SH"], period="1d", start_time="20260601", end_time="20260717", count=5)),
    ("md_1d_pos", None),
]:
    try:
        if kwargs is None:
            d = xtdata.get_market_data(["open","high","low","close","volume"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
        else:
            d = xtdata.get_market_data(**kwargs)
        out[label] = shape(d)
        # try extract close series
        if isinstance(d, dict) and "close" in d:
            close = d["close"]
            if hasattr(close, "shape"):
                out[label + "_close_shape"] = list(close.shape)
                out[label + "_close_sample"] = _sample(close)
            if isinstance(close, dict):
                out[label + "_close_keys"] = list(close.keys())[:5]
    except Exception as e:
        out[label] = {{"err": str(e)[:200]}}

# get_market_data_ex
try:
    d = xtdata.get_market_data_ex(
        ["open","high","low","close","volume"], ["000001.SH","600519.SH"],
        period="1d", start_time="20260601", end_time="20260717", count=5
    )
    out["mdex"] = shape(d)
except Exception as e:
    out["mdex"] = {{"err": str(e)[:200]}}

# after download
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    out["dl"] = True
except Exception as e:
    out["dl"] = str(e)[:120]

try:
    d = xtdata.get_market_data(["open","high","low","close","volume"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    out["md_after_dl"] = shape(d)
    if isinstance(d, dict) and "close" in d:
        out["md_after_dl_close"] = _sample(d["close"])
except Exception as e:
    out["md_after_dl"] = {{"err": str(e)[:200]}}

# subscribe + wait + market data
try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=10)
    out["sub"] = int(sid) if sid is not None else None
except Exception as e:
    out["sub_err"] = str(e)[:120]

print(json.dumps(out, ensure_ascii=False, default=str))
'''

def main():
    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True, timeout=60, cwd=QMT_DIR,
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
    result = {"payload": payload, "stderr": stderr[-400:], "rc": proc.returncode}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:3000])
    print("wrote", OUT)

if __name__ == "__main__":
    main()
