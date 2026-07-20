"""Probe RPC client metadata: data_dir, app_dir, ports, connect variants."""
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_client_meta.json"

probe = r'''
from xtquant import xtdata
import json, os, traceback

out = {"home_xtquant": os.path.join(os.environ.get("USERPROFILE",""), ".xtquant")}

# list ~/.xtquant
base = out["home_xtquant"]
out["xtquant_dirs"] = []
if os.path.isdir(base):
    for name in os.listdir(base):
        p = os.path.join(base, name)
        entry = {"name": name, "is_dir": os.path.isdir(p)}
        cfg = os.path.join(p, "xtdata.cfg")
        rs = os.path.join(p, "running_status")
        entry["has_cfg"] = os.path.isfile(cfg)
        entry["has_running_status"] = os.path.isfile(rs)
        if entry["has_cfg"]:
            try:
                entry["cfg"] = json.load(open(cfg, "r", encoding="utf-8"))
            except Exception as e:
                entry["cfg_err"] = str(e)[:80]
        out["xtquant_dirs"].append(entry)

def probe_port(port):
    e = {"port": port}
    try:
        xtdata.reconnect("127.0.0.1", port)
        c = xtdata.get_client()
        e["connected"] = bool(c.is_connected()) if c else False
        for meth in ("get_data_dir", "get_app_dir"):
            if hasattr(c, meth):
                try:
                    e[meth] = str(getattr(c, meth)())[:200]
                except Exception as ex:
                    e[meth + "_err"] = str(ex)[:120]
        # sector
        try:
            lst = xtdata.get_stock_list_in_sector("沪深A股") or []
            e["sector_n"] = len(lst)
        except Exception as ex:
            e["sector_err"] = str(ex)[:120]
        # instrument
        try:
            det = xtdata.get_instrument_detail("000001.SH") or {}
            e["detail_keys"] = list(det.keys())[:15] if isinstance(det, dict) else []
            e["detail_ok"] = bool(det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice"))
        except Exception as ex:
            e["detail_err"] = str(ex)[:120]
        # local data using client data_dir
        try:
            dd = e.get("get_data_dir") or ""
            d = xtdata.get_local_data(
                ["time","open","high","low","close","volume"],
                ["000001.SH"], "1d", "20260601", "20260717", count=5,
                data_dir=dd if dd else None
            )
            df = d.get("000001.SH") if isinstance(d, dict) else None
            e["local_rows"] = int(len(df)) if df is not None and hasattr(df, "empty") and not df.empty else 0
        except Exception as ex:
            e["local_err"] = str(ex)[:160]
        # subscribe
        try:
            sid = xtdata.subscribe_quote("000001.SH", period="1d", count=1)
            e["subscribe_id"] = int(sid) if sid is not None else None
        except Exception as ex:
            e["subscribe_err"] = str(ex)[:120]
        # init_data_dir after connect
        try:
            e["init_data_dir"] = str(xtdata.init_data_dir())[:200]
            e["xtdata_data_dir"] = str(getattr(xtdata, "data_dir", ""))[:200]
        except Exception as ex:
            e["init_err"] = str(ex)[:120]
    except Exception as ex:
        e["err"] = str(ex)[:200]
        e["tb"] = traceback.format_exc()[-300:]
    return e

# auto get_client (no forced port)
auto = {}
try:
    # clear force
    xtdata.CLIENT = None
    c = xtdata.get_client()
    auto["connected"] = bool(c.is_connected()) if c else False
    try:
        auto["data_dir"] = str(c.get_data_dir())[:200]
    except Exception as e:
        auto["data_dir_err"] = str(e)[:100]
    try:
        auto["app_dir"] = str(c.get_app_dir())[:200]
    except Exception as e:
        auto["app_dir_err"] = str(e)[:100]
except Exception as e:
    auto["err"] = str(e)[:200]
out["auto_get_client"] = auto

out["ports"] = []
for port in [58600, 58610, 58341, 59000, 56000, 55300]:
    out["ports"].append(probe_port(port))

print(json.dumps(out, ensure_ascii=False))
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
    result = {"payload": payload, "stderr": stderr[-600:], "rc": proc.returncode, "stdout_tail": stdout[-400:]}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    print("wrote", OUT)

if __name__ == "__main__":
    main()
