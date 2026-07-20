"""Safe stepwise pure-QMT meta probe. Avoid crashing calls if possible."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_DATA_DIR

OUT = ROOT / "data" / "_qmt_probe_safe_meta.json"
APP_ROOT = str(Path(QMT_DIR).resolve().parent)
DATA_DIR = str(Path(QMT_DATA_DIR).resolve())


def run_snippet(label: str, code: str, timeout: int = 45) -> dict:
    full = (
        "from xtquant import xtdata\n"
        "import json, traceback, os\n"
        "out = {}\n"
        "try:\n"
        "    xtdata.reconnect('127.0.0.1', 58600)\n"
        "    c = xtdata.get_client()\n"
        "    out['connected'] = bool(c and c.is_connected())\n"
        "except Exception as e:\n"
        "    out['connected'] = False\n"
        "    out['connect_err'] = str(e)[:200]\n"
        "    print(json.dumps(out, ensure_ascii=False))\n"
        "    raise SystemExit(0)\n"
        + code
        + "\nprint(json.dumps(out, ensure_ascii=False, default=str))\n"
    )
    try:
        proc = subprocess.run(
            [QMT_PYTHON_PATH, "-c", full],
            capture_output=True,
            timeout=timeout,
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
        return {
            "label": label,
            "rc": proc.returncode,
            "payload": payload,
            "stderr": stderr[-400:],
            "stdout_tail": stdout[-400:],
            "crashed": proc.returncode in (3221225477, -1073741819) or proc.returncode > 255,
        }
    except subprocess.TimeoutExpired:
        return {"label": label, "err": "timeout", "crashed": False}
    except Exception as e:
        return {"label": label, "err": str(e)[:200], "crashed": False}


def main():
    results = []

    # A: baseline meta only
    results.append(run_snippet("A_meta", r'''
out["client_type"] = type(c).__name__
for m in ("get_data_dir", "get_app_dir", "get_server_tag"):
    try:
        r = getattr(c, m)()
        out[m] = repr(r)[:200]
    except Exception as e:
        out[m] = f"ERR:{e}"[:160]
try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"] = len(lst)
except Exception as e:
    out["sector_err"] = str(e)[:100]
'''))

    # B: get_server_tag only + holidays
    results.append(run_snippet("B_server_tag_holidays", r'''
try:
    out["get_server_tag"] = repr(c.get_server_tag())[:200]
except Exception as e:
    out["get_server_tag"] = f"ERR:{e}"[:120]
try:
    h = c.get_holidays()
    out["holidays_type"] = type(h).__name__
    out["holidays_len"] = len(h) if hasattr(h, "__len__") else None
    out["holidays_sample"] = repr(h)[:200]
except Exception as e:
    out["holidays_err"] = str(e)[:120]
'''))

    # C: set_app_dir to install root (official API)
    results.append(run_snippet("C_set_app_dir_root", rf'''
path = r"{APP_ROOT}"
try:
    r = c.set_app_dir(path)
    out["set_app_dir_ret"] = repr(r)[:120]
except Exception as e:
    out["set_app_dir_err"] = str(e)[:160]
try:
    out["get_app_dir"] = repr(c.get_app_dir())[:200]
except Exception as e:
    out["get_app_dir"] = f"ERR:{{e}}"[:120]
try:
    out["get_data_dir"] = repr(c.get_data_dir())[:200]
except Exception as e:
    out["get_data_dir"] = f"ERR:{{e}}"[:120]
'''))

    # D: set_app_dir to bin.x64
    results.append(run_snippet("D_set_app_dir_bin", rf'''
path = r"{APP_ROOT}\\bin.x64"
try:
    r = c.set_app_dir(path)
    out["set_app_dir_ret"] = repr(r)[:120]
except Exception as e:
    out["set_app_dir_err"] = str(e)[:160]
try:
    out["get_app_dir"] = repr(c.get_app_dir())[:200]
except Exception as e:
    out["get_app_dir"] = f"ERR:{{e}}"[:120]
try:
    out["get_data_dir"] = repr(c.get_data_dir())[:200]
except Exception as e:
    out["get_data_dir"] = f"ERR:{{e}}"[:120]
# bars after set
try:
    d = xtdata.get_market_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=3)
    df = d.get("close") if isinstance(d, dict) else None
    if df is not None and hasattr(df, "shape"):
        out["md_shape"] = list(df.shape)
        out["md_empty"] = bool(df.empty)
    else:
        out["md_repr"] = repr(d)[:120]
except Exception as e:
    out["md_err"] = str(e)[:120]
try:
    det = xtdata.get_instrument_detail("000001.SH")
    out["detail"] = (list(det.keys())[:10] if isinstance(det, dict) else repr(det)[:100])
    out["detail_ok"] = bool(isinstance(det, dict) and det)
except Exception as e:
    out["detail_err"] = str(e)[:100]
'''))

    # E: commonControl single safe cmds only
    results.append(run_snippet("E_commonControl_safe", r'''
hits = []
if hasattr(c, "commonControl"):
    for cmd in ("status", "info", "ping", "version", "get_server_tag"):
        for payload in ("", "{}"):
            try:
                r = c.commonControl(cmd, payload)
                if r not in (None, "", 0, -1, b"", {}):
                    hits.append({"cmd": cmd, "payload": payload, "type": type(r).__name__, "repr": repr(r)[:180]})
            except Exception as e:
                hits.append({"cmd": cmd, "payload": payload, "err": str(e)[:100]})
out["hits"] = hits[:20]
'''))

    # F: subscribe_whole_quote + full tick after
    results.append(run_snippet("F_whole_quote", r'''
try:
    sid = xtdata.subscribe_whole_quote(["SH", "SZ"])
    out["whole_sid"] = int(sid) if sid is not None else None
except Exception as e:
    out["whole_err"] = str(e)[:160]
import time
time.sleep(1)
try:
    t = xtdata.get_full_tick(["000001.SH"])
    out["tick_type"] = type(t).__name__
    out["tick_repr"] = repr(t)[:300]
except Exception as e:
    out["tick_err"] = str(e)[:160]
try:
    det = xtdata.get_instrument_detail("000001.SH")
    out["detail_ok"] = bool(isinstance(det, dict) and det)
    out["detail_sample"] = {k: det.get(k) for k in list(det.keys())[:8]} if isinstance(det, dict) else None
except Exception as e:
    out["detail_err"] = str(e)[:100]
'''))

    # G: callFormula / createView — may expose formula-layer cache (UI has cache)
    results.append(run_snippet("G_formula_layer", r'''
methods = [m for m in dir(c) if "formula" in m.lower() or "view" in m.lower() or "Formula" in m or "View" in m]
out["formula_methods"] = methods
# try get_instrument_detail raw
try:
    raw = c.get_instrument_detail("000001.SH")
    out["raw_detail_type"] = type(raw).__name__
    out["raw_detail"] = repr(raw)[:300]
except Exception as e:
    out["raw_detail_err"] = str(e)[:120]
# trading dates
try:
    td = c.get_trading_dates_by_market("SH")
    out["td_type"] = type(td).__name__
    out["td_len"] = len(td) if hasattr(td, "__len__") else None
    out["td_sample"] = repr(td)[:200]
except Exception as e:
    out["td_err"] = str(e)[:120]
'''))

    summary = {
        "app_root": APP_ROOT,
        "data_dir": DATA_DIR,
        "results": results,
        "verdict": {
            "any_crash": any(r.get("crashed") for r in results),
            "labels_ok": [r["label"] for r in results if r.get("rc") == 0 and r.get("payload")],
            "labels_crash": [r["label"] for r in results if r.get("crashed")],
        },
    }
    # extract readiness if any
    for r in results:
        p = r.get("payload") or {}
        if p.get("md_empty") is False or p.get("detail_ok") or p.get("local_rows"):
            summary["verdict"]["ready_hint"] = r["label"]
            summary["verdict"]["ready_payload"] = p
            break

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary["verdict"], ensure_ascii=False, indent=2))
    for r in results:
        p = r.get("payload") or {}
        print(
            r["label"],
            "rc=", r.get("rc"),
            "crash=", r.get("crashed"),
            "keys=", list(p.keys())[:12],
            "snippet=", json.dumps({k: p[k] for k in list(p)[:8]}, ensure_ascii=False)[:300],
        )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
