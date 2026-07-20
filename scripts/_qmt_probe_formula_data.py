"""Try official formula/view APIs on full-QMT 58600 for market data.

58600 tag=server_formula. If market-data3 is empty, formula layer may still
expose bars (UI formula cache was observed earlier). Official APIs only.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_formula_data.json"

probe = r'''
from xtquant import xtdata
from xtquant import xtbson as bson
import json, traceback, inspect

out = {}
xtdata.reconnect("127.0.0.1", 58600)
c = xtdata.get_client()
out["connected"] = bool(c and c.is_connected())

# inspect signatures if available
for name in ("createFormula", "callFormula", "createView", "bindViewFormula",
             "get_market_data3", "subscribe_quote", "commonControl"):
    if hasattr(c, name):
        try:
            out[f"sig_{name}"] = str(inspect.signature(getattr(c, name)))
        except Exception as e:
            out[f"sig_{name}"] = f"nosig:{e}"[:80]
            try:
                out[f"doc_{name}"] = (getattr(c, name).__doc__ or "")[:200]
            except Exception:
                pass

# try createFormula / callFormula with simple CLOSE formula
formula_tries = []
for expr in ("CLOSE", "C", "MA(CLOSE,5)", "DYNAINFO(7)"):
    entry = {"expr": expr}
    try:
        # common patterns: createFormula(name, formula_text) or similar
        fid = None
        for args in [
            (expr,),
            ("probe", expr),
            ("probe", expr, ""),
            (expr, "000001.SH"),
            ("probe", expr, "000001.SH"),
        ]:
            try:
                fid = c.createFormula(*args)
                entry["create_args"] = [str(a) for a in args]
                entry["create_ret"] = repr(fid)[:120]
                break
            except TypeError as te:
                entry.setdefault("create_typeerrs", []).append(str(te)[:80])
            except Exception as e:
                entry.setdefault("create_errs", []).append(str(e)[:100])
        if fid is not None and fid not in (None, -1, 0, False):
            for call_args in [
                (fid,),
                (fid, "000001.SH"),
                (fid, "000001.SH", "1d"),
                (fid, "SH000001"),
            ]:
                try:
                    r = c.callFormula(*call_args)
                    entry["call_args"] = [str(a) for a in call_args]
                    entry["call_type"] = type(r).__name__
                    entry["call_repr"] = repr(r)[:300]
                    break
                except TypeError:
                    continue
                except Exception as e:
                    entry.setdefault("call_errs", []).append(str(e)[:100])
            try:
                c.destoryFormula(fid)
            except Exception:
                pass
    except Exception as e:
        entry["err"] = str(e)[:160]
    formula_tries.append(entry)
out["formula_tries"] = formula_tries

# createView attempts
view_tries = []
for args in [
    (),
    ("000001.SH",),
    ("000001.SH", "1d"),
    ("SH", "000001", "1d"),
]:
    entry = {"args": [str(a) for a in args]}
    try:
        vid = c.createView(*args)
        entry["ret"] = repr(vid)[:120]
        entry["type"] = type(vid).__name__
        if vid not in (None, -1, 0, False, ""):
            try:
                c.destoryView(vid)
            except Exception:
                pass
    except TypeError as te:
        entry["typeerr"] = str(te)[:120]
    except Exception as e:
        entry["err"] = str(e)[:120]
    view_tries.append(entry)
out["view_tries"] = view_tries

# subscribe with different meta shapes (raw client)
sub_tries = []
for meta in [
    {"stockCode": "000001.SH", "period": "1d"},
    {"stockcode": "000001.SH", "period": "1d"},
    {"code": "000001.SH", "period": "1d"},
    {"stockCode": "SH000001", "period": "1d"},
    {"stockCode": "000001.SH", "period": "86400"},
]:
    for region in [
        {"startTime": "20260601", "endTime": "20260717", "count": 5},
        {"startTime": "", "endTime": "", "count": 5},
        {"startTime": 20260601, "endTime": 20260717, "count": 5},
    ]:
        entry = {"meta": meta, "region": region}
        try:
            m = bson.BSON.encode(meta)
            r = bson.BSON.encode(region)
            sid = c.subscribe_quote(m, r, None)
            entry["sid"] = sid
        except Exception as e:
            entry["err"] = str(e)[:120]
        sub_tries.append(entry)
out["sub_tries"] = sub_tries[:12]
out["sub_nonzero"] = [s for s in sub_tries if s.get("sid") not in (None, -1, -2, 0)]

# financial / other data that might work on formula server
for name, call in [
    ("get_divid_factors", lambda: c.get_divid_factors("000001.SH", "20200101", "20260717")),
    ("get_holidays", lambda: c.get_holidays()),
    ("get_sector_list", lambda: c.get_sector_list()),
    ("get_stock_type", lambda: c.get_stock_type("000001.SH")),
    ("is_stock_type", lambda: c.is_stock_type("000001.SH", "STOCK")),
]:
    try:
        r = call()
        out[name] = {
            "type": type(r).__name__,
            "len": len(r) if hasattr(r, "__len__") else None,
            "repr": repr(r)[:200],
        }
    except Exception as e:
        out[name] = {"err": str(e)[:120]}

# market data with enable server True and empty start/end count
try:
    r = c.get_market_data3(
        ["close"], ["000001.SH"], "1d", "", "", 5,
        "none", True, "v4", False, True, 1
    )
    out["gmd3_count5"] = {"type": type(r).__name__, "repr": repr(r)[:400]}
except Exception as e:
    out["gmd3_count5"] = {"err": str(e)[:160]}

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
        "stderr": stderr[-800:],
        "rc": proc.returncode,
        "crashed": proc.returncode in (3221225477, -1073741819) or (proc.returncode or 0) > 255,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("rc", proc.returncode, "crashed", result["crashed"])
    print("connected", payload.get("connected"))
    for k in ("sig_createFormula", "sig_callFormula", "sig_createView", "sig_get_market_data3"):
        if k in payload:
            print(k, payload[k])
    print("formula_tries", json.dumps(payload.get("formula_tries"), ensure_ascii=False)[:1200])
    print("view_tries", json.dumps(payload.get("view_tries"), ensure_ascii=False)[:600])
    print("sub_nonzero", payload.get("sub_nonzero"))
    print("gmd3", json.dumps(payload.get("gmd3_count5"), ensure_ascii=False)[:400])
    for name in ("get_divid_factors", "get_sector_list", "get_stock_type", "is_stock_type"):
        if name in payload:
            print(name, json.dumps(payload[name], ensure_ascii=False)[:200])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
