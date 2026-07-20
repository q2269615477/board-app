#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
校验 K 线周期不变量（机械门禁）。

完成标准：
- timestamps 严格递增
- 无重复 timestamp
- 若同时有 1m 与 5m：5m 根数应明显少于 1m（约 1/5 量级，容差宽）

用法:
  python scripts/verify_kline_periods.py --type index --code sh000001 --periods 1m,5m,15m,60m,daily,weekly
  无数据时 exit 2（不伪装 PASS）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 允许无 QMT 时用本地库
os.environ.setdefault("BOARD_APP_AUTO_BOOTSTRAP", "0")


def _check_series(name: str, data: list) -> list[str]:
    errs = []
    if not data:
        errs.append(f"{name}: empty")
        return errs
    ts = [d.get("timestamp") for d in data]
    if any(t is None for t in ts):
        errs.append(f"{name}: null timestamp")
        return errs
    for i in range(1, len(ts)):
        if ts[i] <= ts[i - 1]:
            errs.append(f"{name}: non-increasing at i={i} ({ts[i-1]} -> {ts[i]})")
            break
    if len(ts) != len(set(ts)):
        errs.append(f"{name}: duplicate timestamps count={len(ts) - len(set(ts))}")
    return errs


def main() -> int:
    p = argparse.ArgumentParser(description="Verify kline period invariants")
    p.add_argument("--type", default="index")
    p.add_argument("--code", default="sh000001")
    p.add_argument(
        "--periods",
        default="1m,5m,15m,60m,daily,weekly",
        help="comma-separated periods",
    )
    p.add_argument("--name", default="")
    args = p.parse_args()
    periods = [x.strip() for x in args.periods.split(",") if x.strip()]

    from services.kline_service import get_kline_service

    ks = get_kline_service()
    results = {}
    all_errs = []
    empty_count = 0

    for period in periods:
        result, code = ks.get_kline(
            args.type, args.code, period, board_name=args.name, force=True, timeout=30
        )
        data = result.get("data") or []
        results[period] = len(data)
        if not data:
            empty_count += 1
            all_errs.append(f"{period}: no data (status={code}, err={result.get('error')})")
            continue
        all_errs.extend(_check_series(period, data))

    if "1m" in results and "5m" in results and results["1m"] > 50 and results["5m"] > 0:
        # 5m 应显著少于 1m（允许宽裕）
        if results["5m"] >= results["1m"] * 0.9:
            all_errs.append(
                f"5m vs 1m: 5m count {results['5m']} too close to 1m {results['1m']}"
            )

    print("counts:", results)
    if empty_count == len(periods):
        print("SKIP/FAIL: no kline data for any period (exit 2)")
        return 2
    if all_errs:
        print("FAIL:")
        for e in all_errs:
            print(" -", e)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
