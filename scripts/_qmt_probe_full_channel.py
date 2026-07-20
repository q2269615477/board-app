"""完整 QMT 通道复测：58600 + 显式 QMT_DATA_DIR，不依赖 MiniQMT。"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_DATA_DIR, QMT_MINI_DATA_DIR, QMT_AUTO_START

OUT = ROOT / "data" / "_qmt_probe_full_channel.json"

probe = f'''
from xtquant import xtdata
import json, os
from pathlib import Path

MAIN = r"{QMT_DATA_DIR}"
MINI = r"{QMT_MINI_DATA_DIR}"
out = {{
    "auto_start": {repr(bool(QMT_AUTO_START))},
    "main_dir": MAIN,
    "mini_dir": MINI,
    "main_exists": Path(MAIN).exists(),
    "mini_exists": Path(MINI).exists(),
}}
xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c.is_connected()) if c is not None else False
out["data_dir_attr"] = str(getattr(xtdata, "data_dir", ""))
out["default_data_dir"] = str(getattr(xtdata, "default_data_dir", ""))

# sector
try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"] = len(lst)
except Exception as e:
    out["sector_err"] = str(e)[:120]

# instrument detail
try:
    det = xtdata.get_instrument_detail("000001.SH") or {{}}
    out["detail_keys"] = list(det.keys())[:12] if isinstance(det, dict) else []
    out["detail_ok"] = bool(det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice"))
    out["detail_name"] = str(det.get("InstrumentName") or det.get("InstrumentID") or "")[:40]
except Exception as e:
    out["detail_err"] = str(e)[:120]
    out["detail_ok"] = False

# history download
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    out["download_ok"] = True
except Exception as e:
    out["download_ok"] = False
    out["download_err"] = str(e)[:160]

# get_local_data with explicit main datadir
for label, path in (("main", MAIN), ("mini", MINI), ("none", None)):
    entry = {{"path": path}}
    try:
        kwargs = dict(
            field_list=["time","open","high","low","close","volume"],
            stock_list=["000001.SH"],
            period="1d",
            start_time="20260601",
            end_time="20260717",
            count=5,
        )
        if path:
            kwargs["data_dir"] = path
        d = xtdata.get_local_data(**kwargs)
        df = d.get("000001.SH") if isinstance(d, dict) else None
        if df is not None and hasattr(df, "empty") and not df.empty:
            entry["rows"] = int(len(df))
            entry["last"] = str(df.index[-1])
            entry["close"] = float(df.iloc[-1]["close"])
        else:
            entry["rows"] = 0
            entry["empty"] = True
    except Exception as e:
        entry["err"] = str(e)[:160]
    out[f"local_{{label}}"] = entry

# subscribe
try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=1)
    out["subscribe_id"] = int(sid) if sid is not None else None
except Exception as e:
    out["subscribe_err"] = str(e)[:160]

print(json.dumps(out, ensure_ascii=False))
'''

def main():
    meta = {
        "qmt_python": QMT_PYTHON_PATH,
        "qmt_dir": QMT_DIR,
        "qmt_data_dir": QMT_DATA_DIR,
        "qmt_auto_start": QMT_AUTO_START,
        "python_exists": os.path.exists(QMT_PYTHON_PATH),
    }
    if not os.path.exists(QMT_PYTHON_PATH):
        meta["error"] = "QMT python missing"
        OUT.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return

    proc = subprocess.run(
        [QMT_PYTHON_PATH, "-c", probe],
        capture_output=True,
        timeout=45,
        text=True,
        cwd=QMT_DIR,
    )
    payload = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            payload = json.loads(line)
            break
    result = {
        "meta": meta,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "")[-500:],
        "stdout_tail": (proc.stdout or "")[-300:],
        "probe": payload,
    }
    # 判定：connected + (rows>0 or detail_ok)
    p = payload
    rows = 0
    for k in ("local_main", "local_mini", "local_none"):
        rows = max(rows, int((p.get(k) or {}).get("rows") or 0))
    data_ok = rows > 0 or bool(p.get("detail_ok"))
    result["verdict"] = {
        "connected": bool(p.get("connected")),
        "data_ok": data_ok,
        "qmt_available_would_be": bool(p.get("connected")) and data_ok,
        "rows_max": rows,
        "sector_n": p.get("sector_n"),
        "subscribe_id": p.get("subscribe_id"),
        "download_ok": p.get("download_ok"),
        "download_err": p.get("download_err"),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
