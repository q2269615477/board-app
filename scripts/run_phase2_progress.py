# -*- coding: utf-8 -*-
"""Phase2 推进：台账重建 → 指数 → 批量个股(可限流) → 失败板块重试 → 高周期物化."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.env_bootstrap import (  # noqa: E402
    ensure_tushare_token,
    force_direct_network,
    load_env_files,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-limit", type=int, default=200, help="本次个股日更只数，0=全部")
    parser.add_argument("--board-limit", type=int, default=40, help="失败板块重试只数，0=跳过")
    parser.add_argument("--skip-stocks", action="store_true")
    parser.add_argument("--skip-boards", action="store_true")
    parser.add_argument("--skip-indices", action="store_true")
    parser.add_argument("--skip-periods", action="store_true")
    args = parser.parse_args()

    load_env_files()
    force_direct_network()  # 必须在 Tushare/板块请求前清掉 7688 代理
    ensure_tushare_token()

    from data_update_manager import (
        rebuild_stock_ledger_from_kline,
        update_all_indices_qmt,
        update_all_indices_tushare,
        qmt_update_all_stocks,
        update_failed_boards,
        materialize_higher_periods,
        _qmt_connect,
    )

    report = {}
    print("=== Phase2 progress ===")
    print("qmt_connect", _qmt_connect())

    ledger = rebuild_stock_ledger_from_kline(min_rows=5)
    report["ledger"] = ledger
    print("ledger", ledger)

    if not args.skip_indices:
        # 策略：指数仅 QMT，不回退 Tushare
        if _qmt_connect():
            idx = update_all_indices_qmt(max_retries=2)
        else:
            idx = {'success': 0, 'failed': 0, 'skipped': True, 'error': 'QMT不可用'}
        report["indices_qmt"] = idx
        print("indices", idx)

    if not args.skip_stocks:
        lim = None if args.stock_limit == 0 else args.stock_limit
        stocks = qmt_update_all_stocks(
            force=True,
            limit=lim,
            batch_size=30,
            rebuild_ledger=False,
            mark_done=(lim is None),
        )
        report["stocks"] = stocks
        print("stocks", stocks)

    if not args.skip_boards and args.board_limit != 0:
        boards = update_failed_boards(max_retries=2, limit=args.board_limit)
        report["boards_retry"] = boards
        print("boards_retry", boards)

    if not args.skip_periods:
        periods = materialize_higher_periods()
        report["periods"] = periods
        print("periods", periods)

    out = ROOT / "data" / "_phase2_progress.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)
    print("DONE")


if __name__ == "__main__":
    main()
