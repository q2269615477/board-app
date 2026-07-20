"""Probe a single xtdata RPC port (official). Usage: python _qmt_probe_port.py 58341"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

port = int(sys.argv[1]) if len(sys.argv) > 1 else 58341

probe = f'''
from xtquant import xtdata
import json
port = {port}
out = {{"port": port}}
try:
    xtdata.reconnect("127.0.0.1", port)
    c = xtdata.get_client()
    out["connected"] = bool(c and c.is_connected())
    out["get_data_dir"] = str(c.get_data_dir() or "")
    out["get_app_dir"] = str(c.get_app_dir() or "")
except Exception as e:
    out["err"] = str(e)[:200]
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)
try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"] = len(lst)
except Exception as e:
    out["sector_err"] = str(e)[:100]
try:
    d = xtdata.get_market_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    df = d.get("close") if isinstance(d, dict) else None
    if df is not None and hasattr(df, "shape"):
        out["md_shape"] = list(df.shape)
        out["md_empty"] = bool(df.empty)
        if not df.empty and df.shape[1] > 0:
            out["md_vals"] = [float(x) for x in list(df.iloc[0].dropna().values[-3:])]
except Exception as e:
    out["md_err"] = str(e)[:120]
try:
    d2 = xtdata.get_local_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    df2 = d2.get("000001.SH") if isinstance(d2, dict) else None
    out["local_rows"] = 0 if df2 is None or getattr(df2, "empty", True) else int(len(df2))
except Exception as e:
    out["local_err"] = str(e)[:100]
try:
    det = xtdata.get_instrument_detail("000001.SH") or {{}}
    out["detail_ok"] = bool(isinstance(det, dict) and det and (
        det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice")))
    out["detail_keys"] = list(det.keys())[:10] if isinstance(det, dict) else None
except Exception as e:
    out["detail_err"] = str(e)[:100]
try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=3)
    out["subscribe_id"] = int(sid) if sid is not None else None
except Exception as e:
    out["subscribe_err"] = str(e)[:100]
out["has_bars"] = bool(out.get("md_vals") or (out.get("local_rows") or 0) > 0 or out.get("detail_ok"))
print(json.dumps(out, ensure_ascii=False, default=str))
'''

proc = subprocess.run(
    [QMT_PYTHON_PATH, "-c", probe],
    capture_output=True,
    timeout=60,
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
print(json.dumps({"payload": payload, "rc": proc.returncode, "stderr": stderr[-300:]}, ensure_ascii=False, indent=2))
