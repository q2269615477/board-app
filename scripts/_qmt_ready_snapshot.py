# -*- coding: utf-8 -*-
"""One-shot readiness snapshot for handoff / Grok Builder.

Captures: ports, processes, xtdata probe on live ports, Flask system status.
Writes data/_qmt_ready_snapshot.json
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_ready_snapshot.json"
PORTS = [5000, 58341, 58600, 58610, 58670, 59010, 59600]


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def probe_port(port: int) -> dict:
    probe = f'''
from xtquant import xtdata
import json
port={port}
out={{"port":port}}
try:
    xtdata.reconnect("127.0.0.1", port)
    c=xtdata.get_client()
    out["connected"]=bool(c and c.is_connected())
    out["get_data_dir"]=str(c.get_data_dir() or "")
    out["get_app_dir"]=str(c.get_app_dir() or "")
except Exception as e:
    out["err"]=str(e)[:160]
    print(json.dumps(out,ensure_ascii=False)); raise SystemExit(0)
try:
    lst=xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"]=len(lst)
except Exception as e:
    out["sector_err"]=str(e)[:100]
try:
    d=xtdata.get_market_data(["close"],["000001.SH"],"1d","20260601","20260717",count=5)
    df=d.get("close") if isinstance(d,dict) else None
    if df is not None and hasattr(df,"shape"):
        out["md_shape"]=list(df.shape)
        out["md_empty"]=bool(df.empty)
except Exception as e:
    out["md_err"]=str(e)[:100]
try:
    det=xtdata.get_instrument_detail("000001.SH") or {{}}
    out["detail_ok"]=bool(isinstance(det,dict) and det and (det.get("InstrumentID") or det.get("InstrumentName")))
except Exception as e:
    out["detail_err"]=str(e)[:100]
try:
    sid=xtdata.subscribe_quote("000001.SH",period="1d",count=3)
    out["subscribe_id"]=int(sid) if sid is not None else None
except Exception as e:
    out["subscribe_err"]=str(e)[:100]
out["has_bars"]=bool((not out.get("md_empty",True)) or out.get("detail_ok"))
print(json.dumps(out,ensure_ascii=False,default=str))
'''
    try:
        proc = subprocess.run(
            [QMT_PYTHON_PATH, "-c", probe],
            capture_output=True,
            timeout=45,
            cwd=QMT_DIR,
        )
        stdout = (proc.stdout or b"").decode("utf-8", errors="ignore")
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        return {"port": port, "parse_fail": True, "stdout": stdout[-200:], "rc": proc.returncode}
    except Exception as e:
        return {"port": port, "probe_err": str(e)[:200]}


def flask_status() -> dict:
    out = {}
    for path in ("/api/health", "/api/system/status", "/api/update/status"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:5000{path}", timeout=5) as r:
                out[path] = json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            out[path] = {"err": str(e)[:120]}
    return out


def main():
    ports = {p: port_open(p) for p in PORTS}
    probes = {}
    for p in (58600, 58341, 58610):
        if ports.get(p):
            probes[str(p)] = probe_port(p)
        else:
            probes[str(p)] = {"port": p, "skipped": "not listening"}
    any_bars = any(bool(v.get("has_bars")) for v in probes.values() if isinstance(v, dict))
    snap = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ports": ports,
        "probes": probes,
        "any_qmt_bars": any_bars,
        "flask": flask_status(),
        "verdict": (
            "READY" if any_bars else
            "EMPTY_SHELL — need Mini login→58610 or in-client ContextInfo export or Tushare"
        ),
    }
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(snap, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
