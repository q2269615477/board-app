"""
东财板块增量更新脚本
从SQLite读取所有板块列表，逐板块通过 Tushare dc_daily 增量更新到今日
在终端直接运行：python update_boards_today.py
"""
import os, sys, time, random, logging, sqlite3
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('board_update')

DB_PATH = 'data/kline.db'
today = datetime.now().strftime('%Y-%m-%d')

# 加载板块列表
conn = sqlite3.connect(DB_PATH)
rows = conn.execute("SELECT code, name, type FROM meta WHERE period='daily' AND code LIKE 'BK%' ORDER BY code").fetchall()
conn.close()
boards = [{'code': r[0], 'name': r[1], 'type': r[2]} for r in rows]

logger.info(f"共 {len(boards)} 个板块")

# 统计需要更新的
conn = sqlite3.connect(DB_PATH)
cur = conn.execute("SELECT code, MAX(date) FROM kline WHERE code LIKE 'BK%' AND period='daily' GROUP BY code")
need_codes = set()
has_today = 0
for r in cur.fetchall():
    if r[1] >= today:
        has_today += 1
    else:
        need_codes.add(r[0])
conn.close()

logger.info(f"已有今日数据: {has_today}, 需要更新: {len(need_codes)}, 跳过(无meta匹配): {len(boards) - len(need_codes) - has_today}")

if not need_codes:
    logger.info("所有板块已是最新，无需更新")
    sys.exit(0)

from data.board_kline import load_board_kline

success = 0
failed = 0
skipped = 0
total = len(boards)
batch_size = 50

for i, b in enumerate(boards):
    code, name, btype = b['code'], b['name'], b['type']
    if code not in need_codes:
        skipped += 1
        continue

    try:
        df = load_board_kline(btype, name, code, 'daily')
        success += 1
    except Exception as e:
        failed += 1
        logger.warning(f"❌ [{i+1}/{total}] {code} {name}: {str(e)[:80]}")

    # 限流
    time.sleep(random.uniform(1.0, 2.5))

    if (i+1) % batch_size == 0:
        logger.info(f"[进度] {i+1}/{total}: ✅{success} ❌{failed} ⏭{skipped}")

logger.info(f"\n===== 更新完成 =====")
logger.info(f"总数: {total}, 成功: {success}, 失败: {failed}, 跳过(已有): {skipped}")

# 验证
conn = sqlite3.connect(DB_PATH)
cur = conn.execute("SELECT COUNT(*) FROM kline WHERE period='daily' AND code LIKE 'BK%' AND date=?", (today,))
cnt = cur.fetchone()[0]
conn.close()
logger.info(f"今日({today})板块K线数据条数: {cnt}")
