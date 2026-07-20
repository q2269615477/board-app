# -*- coding: utf-8 -*-
"""继续 Phase2：台账已重建后 → 批量个股 + 高周期物化 + 失败板块(直连)。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.env_bootstrap import force_direct_network, load_env_files, ensure_tushare_token  # noqa: E402


def main():
    load_env_files()
    force_direct_network()
    ensure_tushare_token()

    from data_update_manager import (
        rebuild_stock_ledger_from_kline,
        qmt_update_all_stocks,
        update_failed_boards,
        materialize_higher_periods,
        _qmt_connect,
        get_all_cached_stocks,
    )

    report = {}
    print("qmt_connect", _qmt_connect())
    if len(get_all_cached_stocks()) < 100:
        report["ledger"] = rebuild_stock_ledger_from_kline(min_rows=1)
    else:
        report["ledger"] = {"codes": len(get_all_cached_stocks()), "skipped": True}
    print("ledger", report["ledger"])

    # 本批 300 只（最陈旧优先）；全量可再跑 -- 或 limit=0
    stocks = qmt_update_all_stocks(
        force=True,
        limit=300,
        batch_size=25,
        rebuild_ledger=False,
        mark_done=False,
    )
    report["stocks"] = stocks
    print("stocks", stocks)

    periods = materialize_higher_periods()
    report["periods"] = periods
    print("periods", periods)

    boards = update_failed_boards(max_retries=2, limit=60)
    report["boards_retry"] = boards
    print("boards_retry", boards)

    out = ROOT / "data" / "_phase2_continue.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
