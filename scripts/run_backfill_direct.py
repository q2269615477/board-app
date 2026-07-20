# -*- coding: utf-8 -*-
"""全程直连（不走 7688）回填：个股 QMT + 失败板块 + 高周期。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stock-limit", type=int, default=500)
    p.add_argument("--board-limit", type=int, default=80)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--skip-boards", action="store_true")
    p.add_argument("--skip-stocks", action="store_true")
    p.add_argument("--skip-periods", action="store_true")
    args = p.parse_args()

    from core.env_bootstrap import load_env_files, force_direct_network, ensure_tushare_token
    load_env_files()
    force_direct_network()  # 必须最后：清代理 + patch requests
    ensure_tushare_token()

    import os
    print("proxy_env", {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")})

    from data_update_manager import (
        qmt_update_all_stocks,
        update_failed_boards,
        materialize_higher_periods,
        _qmt_connect,
        get_all_cached_stocks,
        rebuild_stock_ledger_from_kline,
    )

    report = {"direct": True, "qmt": _qmt_connect()}
    print("qmt", report["qmt"])
    if len(get_all_cached_stocks()) < 100:
        report["ledger"] = rebuild_stock_ledger_from_kline(min_rows=1)
    else:
        report["ledger"] = {"codes": len(get_all_cached_stocks())}

    if not args.skip_stocks:
        lim = None if args.stock_limit == 0 else args.stock_limit
        report["stocks"] = qmt_update_all_stocks(
            force=True,
            limit=lim,
            batch_size=args.batch_size,
            rebuild_ledger=False,
            mark_done=(lim is None),
        )
        print("stocks", report["stocks"])

    if not args.skip_periods:
        report["periods"] = materialize_higher_periods()
        print("periods", report["periods"])

    if not args.skip_boards:
        report["boards"] = update_failed_boards(max_retries=2, limit=args.board_limit)
        print("boards", report["boards"])

    out = ROOT / "data" / "_backfill_direct.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
