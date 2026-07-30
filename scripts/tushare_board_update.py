#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 data/tushare_cache/ JSON 写入板块 CSV（替代逐个 API 调用）。

用法：
  python scripts/tushare_board_update.py              # 处理最新一个 JSON
  python scripts/tushare_board_update.py 20260730     # 指定日期
"""
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE / "data" / "tushare_cache"
DATA_DIR = BASE / "data"
BOARD_LIST = BASE / "scripts" / "_all_boards.txt"

# CSV 列顺序（与现有板块 CSV 一致）
CSV_HEADER = ["日期", "开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "换手率"]


def _safe_filename(name: str) -> str:
    """生成安全文件名（替换非法字符）"""
    return re.sub(r'[\\/:*?"<>|]', '_', str(name)).strip()


def load_name_map() -> dict:
    """从 _all_boards.txt 和现有 CSV 目录构建 code → (name, type) 映射"""
    code_map = {}
    # 1. 从 _all_boards.txt 加载
    if BOARD_LIST.exists():
        with open(BOARD_LIST, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    name, code, board_type = parts[0], parts[1], parts[2]
                    code_map[code] = (name, board_type)

    # 2. 从 CSV 文件名补充遗漏项（格式: {name}_{code}.csv）
    for dirname in ("行业板块K线数据", "概念板块K线数据"):
        d = DATA_DIR / dirname
        if not d.exists():
            continue
        for fn in d.iterdir():
            m = re.match(r".+_(BK\d+)\.csv$", fn.name)
            if m and m.group(1) not in code_map:
                code = m.group(1)
                name = fn.stem[:-(len(code) + 1)]  # 去掉 _{code} 后缀
                btype = "industry" if "行业" in dirname else "concept"
                code_map[code] = (name, btype)

    return code_map


def find_csv_path(code: str, name_map: dict) -> Path | None:
    """根据 code 查找对应 CSV 文件路径"""
    if code not in name_map:
        return None
    name, btype = name_map[code]
    subdir_name = "行业板块K线数据" if btype == "industry" else "概念板块K线数据"
    subdir = DATA_DIR / subdir_name
    safe = _safe_filename(name)

    # 优先: {safe_name}_{code}.csv
    path = subdir / f"{safe}_{code}.csv"
    if path.exists():
        return path

    # 兼容旧版: {name}_{code}.csv
    legacy = subdir / f"{name}_{code}.csv"
    if legacy.exists():
        return legacy

    # 新板块：返回标准路径（文件不存在时父目录必须存在）
    subdir.mkdir(parents=True, exist_ok=True)
    return path


def get_existing_dates(csv_path: Path) -> set:
    """读取 CSV 中已存在的日期集合"""
    dates = set()
    if not csv_path.exists() or csv_path.stat().st_size < 20:
        return dates
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if row and row[0]:
                    dates.add(row[0].strip())
    except Exception:
        pass
    return dates


def format_row(rec: dict) -> list:
    """将 dc_daily 记录转为 CSV 行"""
    trade_date = str(rec.get("trade_date", ""))
    # 20260729 → 2026-07-29
    if len(trade_date) == 8:
        trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

    return [
        trade_date,
        rec.get("open", 0) or 0,
        rec.get("close", 0) or 0,
        rec.get("high", 0) or 0,
        rec.get("low", 0) or 0,
        rec.get("pct_change", 0) or 0,
        0,  # 涨跌额（无此字段）
        rec.get("vol", 0) or 0,
        rec.get("amount", 0) or 0,
        0,  # 振幅（无此字段）
        0,  # 换手率（无此字段）
    ]


def write_header_if_needed(csv_path: Path):
    """如果 CSV 文件不存在或为空，写入表头"""
    if csv_path.exists() and csv_path.stat().st_size > 10:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)


def process_date(target_date: str) -> dict:
    """处理指定日期的缓存 JSON，写入板块 CSV。

    Returns: {"boards": N, "rows": M, "skipped": S, "missing": X}
    """
    json_path = CACHE_DIR / f"{target_date}.json"
    if not json_path.exists():
        print(f"[ERROR] 缓存文件不存在: {json_path}")
        return {"boards": 0, "rows": 0, "skipped": 0, "missing": 0}

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"[INFO] 加载 {len(records)} 条板块日线 (trade_date={target_date})")

    name_map = load_name_map()
    print(f"[INFO] 板块名称映射: {len(name_map)} 条")

    boards_updated = 0
    total_new_rows = 0
    skipped = 0
    missing = 0

    for rec in records:
        ts_code = rec.get("ts_code", "")
        # BK0448.DC → BK0448
        code = ts_code.replace(".DC", "").strip()
        if not code or not code.startswith("BK"):
            continue

        csv_path = find_csv_path(code, name_map)
        if csv_path is None:
            missing += 1
            if missing <= 5:
                print(f"  [WARN] 无 CSV 映射: {ts_code}")
            continue

        # 去重检查
        trade_date = str(rec.get("trade_date", ""))
        if len(trade_date) == 8:
            trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"

        existing = get_existing_dates(csv_path)
        if trade_date in existing:
            skipped += 1
            continue

        # 写入
        write_header_if_needed(csv_path)
        row = format_row(rec)
        with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        boards_updated += 1
        total_new_rows += 1

    if missing > 0:
        print(f"[INFO] 无 CSV 映射的板块: {missing} 只（ETP或未知类型）")

    print(f"完成: {boards_updated} 板块, +{total_new_rows} 行 (跳过 {skipped} 重复, 缺失 {missing})")
    return {
        "boards": boards_updated,
        "rows": total_new_rows,
        "skipped": skipped,
        "missing": missing,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        # 自动取 cache 目录中最新的 JSON
        jsons = sorted(CACHE_DIR.glob("*.json"))
        if not jsons:
            print("[ERROR] data/tushare_cache/ 下无 JSON 文件")
            sys.exit(1)
        date_str = jsons[-1].stem
        print(f"[INFO] 自动选择最新缓存: {date_str}")

    result = process_date(date_str)
    if result["boards"] == 0 and result["rows"] == 0:
        print("[WARN] 无新增数据写入（可能已存在或 CSV 映射缺失）")
        sys.exit(1)
