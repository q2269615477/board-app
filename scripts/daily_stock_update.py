#!/usr/bin/env python
"""
每日成分股数据增量更新 — 从 QMT 拉取 OHLCV + 市值写入本地 SQLite
支持断点续传、批处理、交易日检测

用法:
  python scripts/daily_stock_update.py              # 增量：仅补缺失数据
  python scripts/daily_stock_update.py --force      # 强制覆盖所有股票最新日
  python scripts/daily_stock_update.py --batch=50   # 每批股票数（默认50）
"""
import sys
import os
import json
import time
import sqlite3
import logging
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.sqlite_repo import normalize_date

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger('daily_stock_update')

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
DB_PATH = str(DATA_DIR / 'kline.db')


def load_all_stock_codes():
    """从成分股 JSON 缓存提取全部唯一股票代码"""
    codes = set()
    for fn in ['industry_constituents.json', 'concept_constituents.json']:
        fpath = DATA_DIR / fn
        if not fpath.exists():
            continue
        with open(fpath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for entry in data.values():
            for s in entry.get('cons', []):
                sc = s.get('code', '')
                if sc and len(sc) == 6 and sc.isdigit():
                    codes.add(sc)
    return sorted(codes)


def get_stocks_needing_update(codes, target_date: str):
    """返回尚未有 target_date 日线的股票代码"""
    conn = sqlite3.connect(DB_PATH)
    try:
        # 取已有该日数据的股票
        if not codes:
            return []
        placeholders = ','.join('?' * len(codes))
        cur = conn.execute(
            f"SELECT DISTINCT code FROM kline WHERE code IN ({placeholders}) AND period='daily' AND date=?",
            codes + [target_date]
        )
        existing = {row[0] for row in cur.fetchall()}
        return [c for c in codes if c not in existing]
    finally:
        conn.close()


def save_daily_batch(records):
    """批量写入 kline + mkt_cap 到 SQLite"""
    if not records:
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        # UPSERT kline
        kline_rows = []
        for r in records:
            kline_rows.append((
                r['code'], 'daily', r['date'],
                r['open'], r['high'], r['low'], r['close'], r['volume']
            ))
        conn.executemany(
            'INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume) '
            'VALUES (?,?,?,?,?,?,?,?)',
            kline_rows
        )

        # UPSERT mkt_cap
        now = datetime.now().isoformat()
        mkt_rows = []
        for r in records:
            if 'mkt_cap' in r and r['mkt_cap'] > 0:
                mkt_rows.append((r['code'], r['mkt_cap'], now))
        if mkt_rows:
            conn.executemany(
                'INSERT OR REPLACE INTO mkt_cap (code, mkt_cap, updated_at) VALUES (?,?,?)',
                mkt_rows
            )

        conn.commit()
        return len(records)
    except Exception as e:
        conn.rollback()
        logger.warning(f'[DB] 批量写入失败: {e}')
        return 0
    finally:
        conn.close()


def fetch_batch_from_qmt(codes, target_date):
    """单次 QMT 子进程调用，返回一批股票的 OHLCV + 市值数据
    target_date: YYYY-MM-DD 格式的目标交易日
    """
    python = os.environ.get(
        'QMT_PYTHON_PATH',
        r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\python.exe'
    )
    cwd = os.environ.get(
        'QMT_DIR',
        r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64'
    )

    if not os.path.exists(python):
        logger.warning('[QMT] Python 路径不存在，跳过')
        return []

    # 代码转换
    qmt_codes = []
    for cd in codes:
        if cd.startswith(('6', '9')) and not cd.startswith('920'):
            qmt_codes.append(f'{cd}.SH')
        elif cd.startswith(('4', '8')) or cd.startswith('920'):
            qmt_codes.append(f'{cd}.BJ')
        else:
            qmt_codes.append(f'{cd}.SZ')

    import subprocess
    code_list = json.dumps(qmt_codes)
    # 使用 target_date 计算下载范围：前7天覆盖周末缺口
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    start = (target_dt - timedelta(days=7)).strftime('%Y%m%d')
    end = target_dt.strftime('%Y%m%d')

    script = (
        "from xtquant import xtdata\n"
        "import json, math\n"
        "xtdata.rpc_init('127.0.0.1:58600')\n"
        f"codes = {code_list}\n"
        f"s, e = '{start}', '{end}'\n"
        # 下载 + 获取日线
        "for qc in codes:\n"
        "    try: xtdata.download_history_data(qc, '1d', s, e)\n"
        "    except: pass\n"
        "kl = xtdata.get_local_data(['time','open','high','low','close','preClose','volume'], codes, '1d', s, e, count=2)\n"
        "result = []\n"
        "for qc in codes:\n"
        "    try:\n"
        "        df = kl.get(qc) if isinstance(kl, dict) else None\n"
        "        if df is None or df.empty: continue\n"
        "        last = df.iloc[-1]\n"
        "        close = float(last['close'])\n"
        "        pre_close = float(last.get('preClose', close))\n"
        "        if math.isnan(close) or close <= 0: continue\n"
        "        detail = xtdata.get_instrument_detail(qc)\n"
        "        tv = detail.get('TotalVolume', 0) or 0 if detail else 0\n"
        "        cap = round(tv * close / 1e8, 2) if tv > 0 else 0\n"
        "        t = int(last['time']) if 'time' in last.index else 0\n"
        "        if t > 1e9:  # 毫秒时间戳\n"
        "            from datetime import datetime\n"
        "            dt = datetime.fromtimestamp(t/1000).strftime('%Y-%m-%d')\n"
        "        else:  # 已是日期数字\n"
        "            dt = str(int(t))[:8]\n"
        "            if len(dt) == 8:\n"
        "                dt = f'{dt[:4]}-{dt[4:6]}-{dt[6:]}'\n"
        "            else:\n"
        "                dt = ''\n"
        "        result.append({\n"
        "            'code': qc.split('.')[0],\n"
        "            'date': dt,\n"
        "            'open': round(float(last['open']), 2),\n"
        "            'high': round(float(last['high']), 2),\n"
        "            'low': round(float(last['low']), 2),\n"
        "            'close': round(close, 2),\n"
        "            'volume': int(float(last.get('volume', 0) or 0)),\n"
        "            'mkt_cap': cap\n"
        "        })\n"
        "    except: pass\n"
        "print(json.dumps(result))\n"
    )

    try:
        proc = subprocess.run(
            [python, '-c', script],
            capture_output=True, timeout=60, cwd=cwd
        )
        out = proc.stdout.decode('utf-8', errors='ignore').strip()
        if out:
            return json.loads(out)
    except subprocess.TimeoutExpired:
        logger.warning(f'[QMT] 子进程超时（{len(codes)}只）')
    except Exception as e:
        logger.warning(f'[QMT] 子进程失败: {e}')
    return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description='每日成分股数据增量更新')
    parser.add_argument('--force', action='store_true', help='强制覆盖今日数据')
    parser.add_argument('--batch', type=int, default=50, help='每批股票数 (默认50)')
    args = parser.parse_args()

    # 确定目标日期：取上个交易日（盘后运行则取当日，否则取前一交易日）
    today = date.today()
    now = datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        today = today - timedelta(days=1)
    if today.weekday() >= 5:  # 周末→周五
        today = today - timedelta(days=today.weekday() - 4)
    target = today.strftime('%Y-%m-%d')

    # 收集所有成分股
    all_codes = load_all_stock_codes()
    logger.info(f'共 {len(all_codes)} 只成分股，目标日期 {target}')

    # 确定需更新的股票
    if args.force:
        need_update = all_codes
        logger.info(f'强制模式：全部 {len(need_update)} 只')
    else:
        need_update = get_stocks_needing_update(all_codes, target)
        logger.info(f'增量模式：需更新 {len(need_update)} 只')

    if not need_update:
        logger.info('所有股票已是最新，无需更新')
        return

    # 分批处理
    batch_size = args.batch
    total_batches = (len(need_update) + batch_size - 1) // batch_size
    total_written = 0

    for i in range(0, len(need_update), batch_size):
        batch = need_update[i:i + batch_size]
        batch_no = i // batch_size + 1

        records = fetch_batch_from_qmt(batch, target)
        written = save_daily_batch(records)
        total_written += written

        pct = min(batch_no * batch_size, len(need_update)) / len(need_update) * 100
        logger.info(f'[{batch_no}/{total_batches}] {len(batch)}只 → 获取{len(records)}条 → 写入{written}条 ({pct:.0f}%)')

        if batch_no < total_batches:
            time.sleep(1)  # QMT 子进程间短暂休息

    logger.info(f'完成！共写入 {total_written} 条日线 + 市值数据')

    # 清理日内缓存（分钟/分时临时脚本）
    import tempfile, glob as _glob
    tmp_dir = tempfile.gettempdir()
    for f in _glob.glob(os.path.join(tmp_dir, '_qk_*.py')):
        try:
            os.remove(f)
        except Exception:
            pass
    logger.info(f'已清理日内缓存临时文件')


if __name__ == '__main__':
    main()
