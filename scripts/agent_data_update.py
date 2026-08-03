#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
board-app 数据更新 — Agent / 多模型唯一入口

契约文档: 技术文档/AGENT_DATA_UPDATE_CONTRACT.md

用法（必须在项目根，用 venv python）:
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py probe
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py debt
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py indices
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py stocks --limit 200
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py stocks --all
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py boards --only-lagging
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py catchup          # 推荐：探针→指数→个股→板块→debt
  .\\venv\\Scripts\\python.exe scripts\\agent_data_update.py catchup --stocks-limit 300

退出码:
  0 = 成功（或跳过但无硬错误）
  2 = QMT 不可用 / 探针失败
  3 = 更新过程硬失败
  4 = 参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("BOARD_APP_AUTO_BOOTSTRAP", "0")
    try:
        from core.env_bootstrap import load_env_files

        load_env_files()
    except Exception:
        pass


def _jprint(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _debt_summary(debt: dict) -> dict:
    out = {
        "target_trade_date": debt.get("target_trade_date") or debt.get("target"),
        "needs_catchup": debt.get("needs_catchup"),
        "summary": debt.get("summary"),
    }
    for key in ("indices", "boards", "stocks"):
        b = debt.get(key) or {}
        if isinstance(b, dict):
            out[key] = {
                "total": b.get("total"),
                "lagging": b.get("lagging"),
                "up_to_date": b.get("up_to_date"),
                "max_lag_calendar_days": b.get("max_lag_calendar_days") or b.get("max_lag"),
                "samples": (b.get("samples") or [])[:3],
            }
    return out


def cmd_probe(_: argparse.Namespace) -> int:
    """验证 QMT 是否真连通，并标明通道（formula / xtdata / none）。"""
    from data.qmt_client import get_qmt_client

    try:
        from data.qmt_client import is_mini_data_ready
    except Exception:
        def is_mini_data_ready():  # type: ignore
            return False

    import data_update_manager as dum

    client = get_qmt_client()
    connected = bool(dum._qmt_connect())
    formula = {}
    freshness = {}
    sample = {}
    err = None
    try:
        formula = client.probe_formula_ready() or {}
    except Exception as e:
        err = f"probe_formula_ready: {e}"
    try:
        target = dum._target_trade_day_str()
        freshness = client.probe_history_freshness(target_date=target) or {}
    except Exception as e:
        err = (err + "; " if err else "") + f"freshness: {e}"

    # 真取一根 600519 证明公式口有 OHLCV（不是空壳）
    try:
        batch = client.get_daily_batch(
            ["600519.SH"], start="20260701", end="20260727", count=10
        ) or {}
        df = batch.get("600519.SH")
        if df is not None and getattr(df, "empty", True) is False:
            last = df.iloc[-1]
            sample = {
                "code": "600519.SH",
                "rows": int(len(df)),
                "last_date": str(last.get("date", ""))[:10],
                "last_close": float(last.get("close", 0) or 0),
                "channel": getattr(client, "_channel", None)
                or getattr(client, "active_channel", None)
                or freshness.get("channel"),
            }
        else:
            sample = {"code": "600519.SH", "rows": 0, "error": "empty_batch"}
    except Exception as e:
        sample = {"code": "600519.SH", "error": str(e)}

    report = {
        "ok": bool(connected and sample.get("rows")),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "qmt_connected": connected,
        "mini_ready": bool(is_mini_data_ready()),
        "qmt_python": getattr(client, "_python", None),
        "qmt_cwd": getattr(client, "_cwd", None),
        "formula_ready": formula,
        "freshness": freshness,
        "sample_bar": sample,
        "truth": {
            "indices_primary": "QMT formula/xtdata → Tushare → HTTP",
            "stocks_primary": "QMT get_daily_batch(formula) → HTTP batch gap-fill",
            "boards_primary": "Tushare dc_daily（不是 QMT）",
            "note": (
                "channel=formula 且 sample_bar.rows>0 才算真 QMT。"
                "若 freshness.oldest_sample < target，QMT 本地缓存偏旧，"
                "个股/指数尾部会走 HTTP/Tushare 灾备，debt 可能仍显示 lagging。"
            ),
        },
        "error": err,
    }
    _jprint(report)
    return 0 if report["ok"] else 2


def cmd_debt(_: argparse.Namespace) -> int:
    import data_update_manager as dum

    debt = dum.scan_update_debt(sample_limit=5)
    _jprint({"ok": True, "debt": _debt_summary(debt), "raw_keys": list(debt.keys())})
    return 0


def cmd_indices(_: argparse.Namespace) -> int:
    import data_update_manager as dum

    t0 = time.time()
    if not dum._qmt_connect():
        _jprint({"ok": False, "error": "QMT未连接", "hint": "先登录本机 QMT 客户端"})
        return 2
    result = dum.update_all_indices_qmt(max_retries=2)
    result = dict(result or {})
    result["elapsed_sec"] = round(time.time() - t0, 2)
    result["ok"] = not result.get("error")
    # 通道说明：written=0 且 success>0 通常表示本地已达目标或 QMT 无新 bar
    result["agent_note"] = (
        "channel 含 qmt 表示走了 QMT 路径；含 tushare/http 表示尾部灾备。"
        "800000 等 permanent_skip 不计失败。"
    )
    _jprint(result)
    return 0 if result.get("ok", True) else 3


def cmd_stocks(args: argparse.Namespace) -> int:
    import data_update_manager as dum

    limit = None if args.all else args.limit
    if limit is None and not args.all:
        limit = 200  # agent 默认安全批次，避免误跑全市场无观察

    t0 = time.time()
    if not dum._qmt_connect():
        _jprint({"ok": False, "error": "QMT未连接"})
        return 2

    result = dum.qmt_update_all_stocks(
        force=True,
        limit=limit,
        batch_size=int(args.batch_size or 50),
        rebuild_ledger=bool(args.rebuild_ledger),
        mark_done=bool(args.mark_done),
    )
    result = dict(result or {})
    result["wall_sec"] = round(time.time() - t0, 2)
    result["ok"] = not result.get("error") and not result.get("skipped")
    if result.get("skipped") and result.get("error"):
        result["ok"] = False
    result["agent_note"] = (
        "channel=formula|xtdata 为真 QMT 批取。"
        "缺数会 HTTP 批补；QMT 缓存末日 < 目标交易日时，debt.lagging 可能仍高。"
        "全量请加 --all；默认 limit=200。"
    )
    _jprint(result)
    if result.get("error") == "QMT未连接" or result.get("error") == "QMT历史缓存偏旧":
        return 2
    return 0 if result.get("success", 0) >= 0 and not (
        result.get("error") and not result.get("success")
    ) else 3


def cmd_boards(args: argparse.Namespace) -> int:
    import data_update_manager as dum

    t0 = time.time()
    # 板块不是 QMT
    result = dum.update_all_boards(only_lagging=bool(args.only_lagging))
    result = dict(result or {})
    result["wall_sec"] = round(time.time() - t0, 2)
    result["ok"] = not result.get("error")
    result["source"] = "tushare_dc_daily_not_qmt"
    _jprint(result)
    return 0 if result.get("ok", True) else 3


def cmd_stocks_http_tail(args: argparse.Namespace) -> int:
    """QMT 缓存偏旧时的显式 HTTP 尾补（仍写同一 kline 表）。"""
    import data_update_manager as dum

    t0 = time.time()
    result = dum.http_fallback_update_stocks(target_date=args.target_date)
    result = dict(result or {})
    result["wall_sec"] = round(time.time() - t0, 2)
    result["ok"] = not result.get("error")
    result["source"] = "http_fallback_not_qmt"
    _jprint(result)
    return 0 if result.get("ok", True) else 3


def cmd_catchup(args: argparse.Namespace) -> int:
    """标准补齐流水线：probe → indices → stocks → boards → debt。"""
    report: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "steps": {},
        "ok": True,
    }
    code = 0

    # probe
    class _P:
        pass

    rc = cmd_probe(_P())  # type: ignore
    # cmd_probe 已打印；这里只记状态（避免双份大 JSON，agent 看最后 debt）
    report["steps"]["probe_exit"] = rc
    if rc != 0 and not args.allow_no_qmt:
        report["ok"] = False
        report["error"] = "QMT probe failed; refuse catchup (pass --allow-no-qmt to continue HTTP/boards)"
        _jprint(report)
        return 2

    import data_update_manager as dum

    t_all = time.time()

    # indices
    t0 = time.time()
    try:
        idx = dum.update_all_indices_qmt(max_retries=2) if rc == 0 else dum.http_fallback_update_indices()
        report["steps"]["indices"] = {
            "elapsed_sec": round(time.time() - t0, 2),
            **{k: idx.get(k) for k in ("success", "failed", "written", "channel", "error", "skipped") if isinstance(idx, dict)},
        }
    except Exception as e:
        report["steps"]["indices"] = {"error": str(e), "trace": traceback.format_exc()[-500:]}
        report["ok"] = False
        code = 3

    # stocks
    t0 = time.time()
    try:
        limit = None if args.all else (args.stocks_limit if args.stocks_limit is not None else 200)
        if rc == 0:
            st = dum.qmt_update_all_stocks(
                force=True,
                limit=limit,
                batch_size=int(args.batch_size or 50),
                rebuild_ledger=False,
                mark_done=bool(args.mark_done and args.all),
            )
        else:
            st = dum.http_fallback_update_stocks()
        report["steps"]["stocks"] = {
            "elapsed_sec": round(time.time() - t0, 2),
            "limit": limit,
            **{
                k: st.get(k)
                for k in (
                    "success", "failed", "new_rows", "pending", "channel",
                    "error", "elapsed_sec", "skipped_up_to_date", "pending_date_lag",
                )
                if isinstance(st, dict)
            },
        }
        if isinstance(st, dict) and st.get("error") and not st.get("success"):
            report["ok"] = False
            code = 3
    except Exception as e:
        report["steps"]["stocks"] = {"error": str(e)}
        report["ok"] = False
        code = 3

    # boards
    t0 = time.time()
    try:
        bd = dum.update_all_boards(only_lagging=True)
        report["steps"]["boards"] = {
            "elapsed_sec": round(time.time() - t0, 2),
            "source": "tushare_dc_daily_not_qmt",
            **{k: bd.get(k) for k in ("success", "failed", "updated", "skipped", "error", "total") if isinstance(bd, dict)},
        }
    except Exception as e:
        report["steps"]["boards"] = {"error": str(e)}
        # 板块失败不阻断指数/个股结论
        report["boards_ok"] = False

    # debt
    try:
        debt = dum.scan_update_debt(sample_limit=3)
        report["debt_after"] = _debt_summary(debt)
    except Exception as e:
        report["debt_after"] = {"error": str(e)}

    report["wall_sec"] = round(time.time() - t_all, 2)
    report["contract"] = "技术文档/AGENT_DATA_UPDATE_CONTRACT.md"
    _jprint(report)
    return code if code else (0 if report.get("ok") else 3)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_data_update",
        description="board-app 数据更新 Agent 唯一入口（见 技术文档/AGENT_DATA_UPDATE_CONTRACT.md）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="验证 QMT 真连通 + 抽样 600519")
    sub.add_parser("debt", help="扫描欠更 scan_update_debt")

    sub.add_parser("indices", help="指数日K：QMT→Tushare→HTTP")

    ps = sub.add_parser("stocks", help="个股日K：QMT batch→HTTP 缺数补")
    ps.add_argument("--limit", type=int, default=200, help="最多处理只数（默认200）")
    ps.add_argument("--all", action="store_true", help="处理全部 pending")
    ps.add_argument("--batch-size", type=int, default=50)
    ps.add_argument("--mark-done", action="store_true", help="结束后写 qmt_daily_done")
    ps.add_argument("--rebuild-ledger", action="store_true")

    pb = sub.add_parser("boards", help="板块日K：Tushare（非QMT）")
    pb.add_argument("--only-lagging", action="store_true", default=True)
    pb.add_argument("--all-boards", action="store_true", help="不只 lagging")

    ph = sub.add_parser("stocks-http-tail", help="显式 HTTP 尾补个股（QMT 缓存旧时）")
    ph.add_argument("--target-date", default=None, help="YYYYMMDD 或 YYYY-MM-DD")

    pc = sub.add_parser("catchup", help="推荐：probe→indices→stocks→boards→debt")
    pc.add_argument("--stocks-limit", type=int, default=200)
    pc.add_argument("--all", action="store_true", help="个股全量 pending")
    pc.add_argument("--batch-size", type=int, default=50)
    pc.add_argument("--mark-done", action="store_true")
    pc.add_argument("--allow-no-qmt", action="store_true", help="QMT 挂了仍继续 HTTP/板块")

    return p


def main(argv=None) -> int:
    _bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)

    # boards --all-boards 覆盖 only_lagging
    if args.cmd == "boards" and getattr(args, "all_boards", False):
        args.only_lagging = False

    handlers = {
        "probe": cmd_probe,
        "debt": cmd_debt,
        "indices": cmd_indices,
        "stocks": cmd_stocks,
        "boards": cmd_boards,
        "stocks-http-tail": cmd_stocks_http_tail,
        "catchup": cmd_catchup,
    }
    try:
        return int(handlers[args.cmd](args))
    except SystemExit:
        raise
    except Exception as e:
        _jprint({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[-1200:],
            "cmd": args.cmd,
        })
        return 3


if __name__ == "__main__":
    sys.exit(main())
