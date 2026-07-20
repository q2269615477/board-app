# -*- coding: utf-8 -*-
"""Full qmt_api bar dump (formula RPC path). Legal official API only."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR

OUT = ROOT / "data" / "_qmt_probe_qmt_api_full.json"

probe = r'''
import json
import traceback

out = {}
try:
    import qmt_api.api as api
    out["addr"] = api.get_address()
    codes = ["000001.SH", "000300.SH", "600519.SH"]
    results = {}
    for code in codes:
        entry = {}
        try:
            det = api.get_instrumentdetail(code)
            if isinstance(det, dict):
                entry["name"] = det.get("InstrumentName")
                entry["preclose"] = det.get("PreClose")
                entry["detail_ok"] = True
            else:
                entry["detail_ok"] = False
        except Exception as e:
            entry["detail_err"] = str(e)[:120]
        try:
            md = api.get_market_data(
                ["open", "high", "low", "close", "volume"],
                [code],
                start_time="20260601",
                end_time="20260717",
                period="1d",
                count=10,
            )
            if md is None:
                entry["md_empty"] = True
            else:
                # DataFrame multiindex or columns
                try:
                    import pandas as pd
                    if isinstance(md, pd.DataFrame):
                        entry["md_type"] = "DataFrame"
                        entry["shape"] = list(md.shape)
                        entry["columns"] = [str(c) for c in md.columns]
                        entry["index_head"] = [str(i) for i in list(md.index[:3])]
                        entry["index_tail"] = [str(i) for i in list(md.index[-3:])]
                        # last rows as records
                        tail = md.tail(5)
                        # flatten
                        recs = []
                        for idx, row in tail.iterrows():
                            r = {"date": str(idx)}
                            for col in tail.columns:
                                try:
                                    r[str(col)] = float(row[col])
                                except Exception:
                                    r[str(col)] = str(row[col])
                            recs.append(r)
                        entry["tail"] = recs
                        entry["has_bars"] = len(md) > 0
                    elif isinstance(md, dict):
                        entry["md_type"] = "dict"
                        entry["keys"] = list(md.keys())[:10]
                        # field -> DataFrame
                        for k, v in md.items():
                            if hasattr(v, "shape"):
                                entry["field_%s_shape" % k] = list(v.shape)
                                if not getattr(v, "empty", True):
                                    entry["field_%s_tail" % k] = v.iloc[-3:].astype(str).to_dict()
                        entry["has_bars"] = any(
                            hasattr(v, "shape") and list(v.shape)[-1] > 0 for v in md.values()
                        )
                    else:
                        entry["md_type"] = type(md).__name__
                        entry["repr"] = repr(md)[:400]
                except Exception as e:
                    entry["parse_err"] = str(e)[:200]
                    entry["md_repr"] = repr(md)[:400]
        except Exception as e:
            entry["md_err"] = str(e)[:200]
            entry["tb"] = traceback.format_exc()[-300:]
        results[code] = entry
    out["results"] = results
    out["any_bars"] = any(r.get("has_bars") for r in results.values())
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
        timeout=120,
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
    result = {"payload": payload, "rc": proc.returncode, "stderr_tail": stderr[-300:]}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
