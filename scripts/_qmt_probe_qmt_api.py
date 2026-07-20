# -*- coding: utf-8 -*-
"""Probe official qmt_api (formula-server RPC, not xtquant xtdata).

Uses QMT python + site-packages qmt_api against 58600 server_formula.
Legal path only. Writes data/_qmt_probe_qmt_api.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_qmt_api.json"

probe = r'''
import json
import os
import sys
import traceback

out = {"ok": False}
try:
    # QMT python site-packages should have qmt_api
    import qmt_api.api as api
    out["api_file"] = getattr(api, "__file__", "")
    out["addr"] = ""
    try:
        out["addr"] = api.get_address()
    except Exception as e:
        out["addr_err"] = str(e)[:200]
    # sector via formula API
    try:
        sec = api.get_stock_list_in_sector("沪深A股")
        out["sector_n"] = len(sec) if sec else 0
        out["sector_head"] = list(sec)[:3] if sec else []
    except Exception as e:
        out["sector_err"] = str(e)[:200]
    try:
        det = api.get_instrumentdetail("000001.SH")
        out["detail_type"] = type(det).__name__
        if isinstance(det, dict):
            out["detail_keys"] = list(det.keys())[:15]
            out["detail_ok"] = bool(det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice"))
        else:
            out["detail_repr"] = repr(det)[:200]
            out["detail_ok"] = bool(det)
    except Exception as e:
        out["detail_err"] = str(e)[:200]
    try:
        md = api.get_market_data(
            ["close", "open", "high", "low", "volume"],
            ["000001.SH"],
            start_time="20260701",
            end_time="20260717",
            period="1d",
            count=5,
        )
        out["md_type"] = type(md).__name__
        if md is None:
            out["md_empty"] = True
        elif isinstance(md, dict):
            out["md_keys"] = list(md.keys())[:10]
            # try shapes
            sample = {}
            for k, v in list(md.items())[:3]:
                if hasattr(v, "shape"):
                    sample[str(k)] = {"shape": list(v.shape), "empty": bool(getattr(v, "empty", False))}
                elif isinstance(v, dict):
                    sample[str(k)] = {"n": len(v), "head": list(v.keys())[:3]}
                else:
                    sample[str(k)] = repr(v)[:120]
            out["md_sample"] = sample
            out["md_empty"] = len(md) == 0
        else:
            out["md_repr"] = repr(md)[:300]
    except Exception as e:
        out["md_err"] = str(e)[:300]
        out["md_tb"] = traceback.format_exc()[-400:]
    out["ok"] = True
except Exception as e:
    out["fatal"] = str(e)[:300]
    out["tb"] = traceback.format_exc()[-500:]
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
        "rc": proc.returncode,
        "stderr_tail": stderr[-400:],
        "stdout_tail": stdout[-400:],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
