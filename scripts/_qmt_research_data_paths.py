"""Research: legitimate QMT data paths (no DAT reverse-engineering).

Paths tested:
  A) Full QMT formula port 58600 (current login)
  B) MiniQMT / miniquote ports if listening (official xtdata design)
  C) Module inventory (xtdatacenter present?)
  D) In-client ContextInfo export recipe (for user to run inside QMT)

Does NOT: parse proprietary DAT structs, patch binaries, bypass license.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_MINI_PATH, QMT_DATA_DIR

OUT = ROOT / "data" / "_qmt_research_data_paths.json"
EXPORT_DIR = ROOT / "data" / "qmt_export"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# In-client strategy template (run from QMT formula/strategy UI, not external python)
IN_CLIENT_EXPORT = r'''#coding:gbk
# 在完整 QMT 内「Python 策略 / 模型」中运行（非外部 python）
# 官方 ContextInfo 可在客户端内读行情并写出 CSV，供面板导入。
import os
from datetime import datetime

OUT_DIR = r"''' + str(EXPORT_DIR).replace("\\", "\\\\") + r'''"
CODES = ["000001.SH", "000300.SH", "600519.SH"]

def init(ContextInfo):
    pass

def handlebar(ContextInfo):
    if not ContextInfo.is_last_bar():
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for code in CODES:
        try:
            # 客户端内官方接口
            df = ContextInfo.get_market_data_ex(
                ["open", "high", "low", "close", "volume"],
                [code],
                period="1d",
                count=60,
            )
            path = os.path.join(OUT_DIR, f"{code.replace('.', '_')}_{ts}.csv")
            if isinstance(df, dict) and code in df:
                df[code].to_csv(path, encoding="utf-8-sig")
            else:
                with open(path + ".err.txt", "w", encoding="utf-8") as f:
                    f.write(repr(type(df)) + "\\n" + repr(df)[:500])
        except Exception as e:
            with open(os.path.join(OUT_DIR, f"err_{code}_{ts}.txt"), "w", encoding="utf-8") as f:
                f.write(str(e))
'''


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.8) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        r = s.connect_ex((host, port))
        s.close()
        return r == 0
    except Exception:
        return False


def probe_port(port: int) -> dict:
    """Official xtquant probe on one port."""
    code = f'''
from xtquant import xtdata
import json
out = {{"port": {port}}}
try:
    xtdata.reconnect("127.0.0.1", {port})
    c = xtdata.get_client()
    out["connected"] = bool(c and c.is_connected())
except Exception as e:
    out["connected"] = False
    out["err"] = str(e)[:200]
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)
try:
    out["get_data_dir"] = str(c.get_data_dir() or "")
    out["get_app_dir"] = str(c.get_app_dir() or "")
except Exception as e:
    out["dir_err"] = str(e)[:120]
try:
    lst = xtdata.get_stock_list_in_sector("沪深A股") or []
    out["sector_n"] = len(lst)
except Exception as e:
    out["sector_err"] = str(e)[:100]
rows = 0
try:
    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")
    out["dl"] = "ok"
except Exception as e:
    out["dl"] = str(e)[:120]
try:
    d = xtdata.get_market_data(["close"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    df = d.get("close") if isinstance(d, dict) else None
    if df is not None and hasattr(df, "shape"):
        out["md_shape"] = list(df.shape)
        out["md_empty"] = bool(df.empty)
        if not df.empty and df.shape[1] > 0:
            out["md_vals"] = [float(x) for x in list(df.iloc[0].dropna().values[-3:])]
            rows = int(df.shape[1])
except Exception as e:
    out["md_err"] = str(e)[:120]
try:
    d2 = xtdata.get_local_data(["close","time"], ["000001.SH"], "1d", "20260601", "20260717", count=5)
    df2 = d2.get("000001.SH") if isinstance(d2, dict) else None
    if df2 is not None and hasattr(df2, "empty") and not df2.empty:
        out["local_rows"] = int(len(df2))
        rows = max(rows, int(len(df2)))
        out["local_tail"] = str(df2.tail(2).to_dict())[:200]
    else:
        out["local_rows"] = 0
except Exception as e:
    out["local_err"] = str(e)[:120]
try:
    det = xtdata.get_instrument_detail("000001.SH") or {{}}
    out["detail_ok"] = bool(isinstance(det, dict) and (det.get("InstrumentID") or det.get("InstrumentName") or det.get("LastPrice")))
    if isinstance(det, dict) and det:
        out["detail_keys"] = list(det.keys())[:12]
except Exception as e:
    out["detail_err"] = str(e)[:100]
try:
    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=3)
    out["subscribe_id"] = int(sid) if sid is not None else None
except Exception as e:
    out["subscribe_err"] = str(e)[:100]
out["has_bars"] = rows > 0 or bool(out.get("detail_ok")) or bool(out.get("md_vals"))
print(json.dumps(out, ensure_ascii=False, default=str))
'''
    try:
        proc = subprocess.run(
            [QMT_PYTHON_PATH, "-c", code],
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
        return {
            "port": port,
            "rc": proc.returncode,
            "payload": payload,
            "stderr_tail": stderr[-300:],
            "crashed": proc.returncode in (3221225477, -1073741819) or (proc.returncode or 0) > 255,
        }
    except Exception as e:
        return {"port": port, "err": str(e)[:200]}


def try_start_mini() -> dict:
    """Official MiniQMT process start (documented data plane for xtdata)."""
    info = {
        "mini_path": QMT_MINI_PATH,
        "exists": Path(QMT_MINI_PATH).exists(),
        "started": False,
    }
    if not info["exists"]:
        return info
    # already running?
    if port_open(58610) or port_open(58600):
        info["already_ports"] = {
            58600: port_open(58600),
            58610: port_open(58610),
            58670: port_open(58670),
        }
    try:
        # Official exe; user must already be licensed/logged for data
        proc = subprocess.Popen(
            [QMT_MINI_PATH],
            cwd=QMT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        info["started"] = True
        info["pid"] = proc.pid
        # wait for ports
        for i in range(20):
            time.sleep(1)
            info["wait_s"] = i + 1
            info["ports"] = {
                58600: port_open(58600),
                58610: port_open(58610),
                58670: port_open(58670),
            }
            if info["ports"].get(58610) or info["ports"].get(58600):
                break
    except Exception as e:
        info["start_err"] = str(e)[:200]
    return info


def module_inventory() -> dict:
    inv = {}
    try:
        proc = subprocess.run(
            [
                QMT_PYTHON_PATH,
                "-c",
                "import pkgutil, xtquant, json; "
                "print(json.dumps({"
                "'xtquant_file': xtquant.__file__, "
                "'mods': [m.name for m in pkgutil.iter_modules(xtquant.__path__)]"
                "}, ensure_ascii=False))",
            ],
            capture_output=True,
            timeout=20,
            cwd=QMT_DIR,
        )
        stdout = (proc.stdout or b"").decode("utf-8", errors="ignore")
        for line in reversed(stdout.strip().splitlines()):
            if line.startswith("{"):
                inv = json.loads(line)
                break
    except Exception as e:
        inv["err"] = str(e)[:160]
    inv["has_xtdatacenter"] = "xtdatacenter" in (inv.get("mods") or [])
    return inv


def main():
    report = {
        "boundary": "official APIs + optional Mini data plane + in-client ContextInfo export; no DAT reverse",
        "qmt_dir": QMT_DIR,
        "data_dir_cfg": QMT_DATA_DIR,
        "modules": module_inventory(),
        "ports_before": {
            p: port_open(p) for p in (58600, 58610, 58620, 58670, 64000)
        },
    }

    # Path A: current full QMT 58600
    report["path_A_full_qmt_58600"] = probe_port(58600)

    # Path B: start Mini officially if 58610 down
    if not port_open(58610):
        report["path_B_start_mini"] = try_start_mini()
        time.sleep(2)
    else:
        report["path_B_start_mini"] = {"skipped": "58610 already open"}

    report["ports_after_mini"] = {
        p: port_open(p) for p in (58600, 58610, 58620, 58670, 64000)
    }

    # Probe all open-ish ports
    probes = {}
    for p in (58600, 58610, 58670, 64000):
        if port_open(p):
            probes[str(p)] = probe_port(p)
        else:
            probes[str(p)] = {"port": p, "skipped": "not listening"}
    report["probes"] = probes

    # In-client export recipe
    recipe_path = EXPORT_DIR / "in_client_export_strategy.py"
    recipe_path.write_text(IN_CLIENT_EXPORT, encoding="utf-8")
    report["path_C_in_client_export"] = {
        "strategy_file": str(recipe_path),
        "export_dir": str(EXPORT_DIR),
        "howto": "在完整 QMT 中打开 Python 策略，加载此文件并运行至最后一根K；CSV 写入 export_dir",
    }

    # Verdict
    ready_ports = []
    for k, v in probes.items():
        pl = (v or {}).get("payload") or {}
        if pl.get("has_bars"):
            ready_ports.append(int(k) if k.isdigit() else k)
    report["verdict"] = {
        "any_qmt_bars": bool(ready_ports),
        "ready_ports": ready_ports,
        "has_xtdatacenter": report["modules"].get("has_xtdatacenter"),
        "recommendation": (
            "use_ready_port_for_xtdata"
            if ready_ports
            else (
                "mini_started_but_no_bars_need_login"
                if (report.get("path_B_start_mini") or {}).get("started")
                else "use_in_client_export_or_tushare"
            )
        ),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))
    print("ports_before", report["ports_before"])
    print("ports_after", report["ports_after_mini"])
    print("mini", json.dumps(report.get("path_B_start_mini"), ensure_ascii=False)[:400])
    for k, v in probes.items():
        pl = (v or {}).get("payload") or {}
        print(
            f"port {k}: connected={pl.get('connected')} data_dir={pl.get('get_data_dir')!r} "
            f"local_rows={pl.get('local_rows')} detail_ok={pl.get('detail_ok')} has_bars={pl.get('has_bars')}"
        )
    print("wrote", OUT)
    print("in_client_strategy", recipe_path)


if __name__ == "__main__":
    main()
