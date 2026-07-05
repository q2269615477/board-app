#!/usr/bin/env python
"""
从 Tushare dc_daily 批量更新板块K线CSV
用法:
  python scripts/tushare_board_update.py
"""
import csv
import json
import re
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger('tushare_board_update')

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
CACHE_DIR = DATA_DIR / 'tushare_cache'
BOARD_DIRS = [
    (DATA_DIR / '行业板块K线数据', 'industry'),
    (DATA_DIR / '概念板块K线数据', 'concept'),
]

CSV_FIELDS = ['日期', '开盘', '收盘', '最高', '最低', '涨跌幅', '涨跌额', '成交量', '成交额', '振幅', '换手率']


def get_last_date(csv_path: Path) -> str | None:
    """获取CSV最后一行的日期"""
    if not csv_path.exists():
        return None
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        f.seek(0, 2)
        size = f.tell()
        if size < 100:
            return None
        f.seek(max(0, size - 500))
        lines = f.read().strip().split('\n')
        for line in reversed(lines):
            parts = line.split(',')
            if parts and len(parts[0]) >= 8:
                return parts[0].strip()
    return None


def load_tushare_data() -> dict:
    """加载本地缓存的 Tushare 数据 → {code: {'20260703': {close, ...}, ...}}"""
    result = {}
    if not CACHE_DIR.exists():
        logger.warning(f"缓存目录不存在: {CACHE_DIR}")
        return result

    for f in sorted(CACHE_DIR.glob('*.json')):
        trade_date = f.stem  # e.g., '20260703'
        try:
            with open(f, 'r', encoding='utf-8') as fh:
                rows = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"  跳过损坏缓存 {f.name}: {e}")
            continue
        for row in rows:
            ts_code = row.get('ts_code', '')
            if not ts_code.startswith('BK'):
                continue
            # Strip .DC suffix: BK1326.DC → BK1326
            code = ts_code.split('.')[0]
            if code not in result:
                result[code] = {}
            result[code][trade_date] = row
        logger.info(f"  加载 {f.name}: {len(rows)} 行")

    logger.info(f"共 {len(result)} 个板块有数据")
    return result


def main():
    tushare_data = load_tushare_data()
    if not tushare_data:
        logger.error("无Tushare数据，退出")
        return

    total_files = 0
    total_new_rows = 0

    for board_dir, dtype in BOARD_DIRS:
        if not board_dir.exists():
            continue
        csv_files = sorted(board_dir.iterdir())
        logger.info(f"[{dtype}] 处理 {len(csv_files)} 个板块...")

        for csv_path in csv_files:
            if not csv_path.name.endswith('.csv'):
                continue
            # 从文件名提取BK代码
            m = re.search(r'(BK\d+)', csv_path.name)
            if not m:
                continue
            code = m.group(1)

            if code not in tushare_data:
                continue

            last_date = get_last_date(csv_path)
            if not last_date:
                continue

            # 筛选 > last_date 的行（标准化日期格式后再比较）
            new_rows = []
            last_date_norm = last_date.replace('-', '')  # YYYY-MM-DD → YYYYMMDD
            for trade_date, row in sorted(tushare_data[code].items()):
                if trade_date > last_date_norm:
                    # Tushare dc_daily 字段:
                    # trade_date, close, open, high, low, pct_change, vol, amount
                    new_rows.append({
                        '日期': trade_date[:4] + '-' + trade_date[4:6] + '-' + trade_date[6:8],
                        '开盘': f"{row.get('open', 0):.2f}",
                        '收盘': f"{row.get('close', 0):.2f}",
                        '最高': f"{row.get('high', 0):.2f}",
                        '最低': f"{row.get('low', 0):.2f}",
                        '涨跌幅': f"{row.get('pct_change', 0):.2f}",
                        '涨跌额': '0',
                        '成交量': f"{row.get('vol', 0)}",
                        '成交额': f"{row.get('amount', 0)}",
                        '振幅': '0',
                        '换手率': '0',
                    })

            if new_rows:
                total_files += 1
                total_new_rows += len(new_rows)
                with open(csv_path, 'a', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                    for row in new_rows:
                        writer.writerow(row)
                logger.info(f"  {code}: +{len(new_rows)} 行 ({new_rows[0]['日期']}~{new_rows[-1]['日期']})")

    logger.info(f"完成: {total_files} 板块, +{total_new_rows} 行")


if __name__ == '__main__':
    main()
