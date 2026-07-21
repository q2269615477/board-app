#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全量数据更新脚本（桌面快捷方式入口）

每次点击：强制更新到最新日期/时刻，盘中也可执行。
绕过 is_today_updated 门控、_is_trading_day 门控。

更新内容：
  I   顶部指数K线   → QMT优先 + Tushare兜底
  II  行业板块K线   → Tushare dc_daily
  III 概念板块K线   → Tushare dc_daily
  IV  板块成分股     → Tushare dc_member (仅最新日期)
  V   多周期物化     → 日→周/月/季/年 resample
  VI  板块涨跌幅     → 东财 HTTP 即时快照
  VII 搜索索引重建   → 拼音+模糊搜索
  VIII 更新状态写回   → update_status.json
  IX  个股K线        → QMT公式口（条件项，需QMT在线）

用法：
  python run_full_update.py          # 全量（无个股）
  python run_full_update.py --stocks # 全量 + 个股（需QMT在线）
  python run_full_update.py --dry    # 干跑：只列出将更新的项
  python run_full_update.py --no-panel # 不自动打开面板
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

# 项目根目录（scripts/ 的父目录）
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

# ============================================================
# 日志配置
# ============================================================
log_dir = os.path.join(_BASE, "data", "update_logs")
os.makedirs(log_dir, exist_ok=True)
ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(log_dir, f"manual_update_{ts_tag}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("full-update")

HEADER = """
╔══════════════════════════════════════════╗
║     board-app 全量数据更新               ║
║     时间: {timestamp}           ║
╚══════════════════════════════════════════╝
""".strip()


# ============================================================
# 强制解锁陈旧门控
# ============================================================
def _force_reset_gates():
    """绕过 is_today_updated 和 _is_trading_day 门控。

    将 update_status.json 中 today 设为昨天，迫使 is_today_updated() 返回 False。
    不修改交易日历判断（该判断仍有效，但我们不依赖它）。
    """
    status_path = os.path.join(_BASE, "data", "update_status.json")
    import json

    if not os.path.exists(status_path):
        return  # 文件不存在则不需要 reset
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        return

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    changed = False
    if status.get("today") == datetime.now().strftime("%Y-%m-%d"):
        status["today"] = yesterday
        changed = True
    if status.get("qmt_daily_done") == datetime.now().strftime("%Y-%m-%d"):
        status["qmt_daily_done"] = yesterday
        changed = True
    if changed:
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        logger.info("[Gate] 已强制重置今日门控 → 昨天")


# ============================================================
# 更新时间打印助手
# ============================================================
def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _print_result(label: str, result: dict, elapsed_ms: float):
    if result.get("skipped"):
        logger.info(f"[{label}] 跳过 ({result.get('message', '')})")
        return
    ok = result.get("success", 0) or result.get("updated", 0)
    fail = result.get("failed", 0)
    lines = result.get("info", "")
    logger.info(f"[{label}] 成功 {ok} / 失败 {fail} ({elapsed_ms:.0f}ms)" +
                (f" — {lines}" if lines else ""))


# ============================================================
# 各步骤实现
# ============================================================
def _step_indices():
    """Step I: 指数更新（QMT 优先）"""
    from data_update_manager import _qmt_connect, update_all_indices_qmt

    if _qmt_connect():
        return update_all_indices_qmt(max_retries=2)

    # QMT 不可用 → 无法自动更新指数（项目策略：指数禁走 Tushare）
    logger.warning("[指数] QMT 不可用，跳过指数更新（项目策略: 指数仅 QMT）")
    logger.warning("[指数] 如需手动更新，请在 QMT 客户端执行数据下载后重跑")
    return {"success": 0, "failed": 0, "skipped": True, "message": "QMT 不可用（项目策略）"}


def _step_industry_boards():
    """Step II-III: 行业 + 概念板块 K 线"""
    from data_update_manager import update_all_boards
    return update_all_boards(max_retries=2)


def _step_constituents():
    """Step IV: 板块成分股（仅最新一天）"""
    from data.board_api import BoardApi
    import sqlite3
    import json

    db_path = os.path.join(_BASE, "data", "kline.db")
    db = sqlite3.connect(db_path)
    today = datetime.now().strftime("%Y-%m-%d")

    # 检查今天是否已有数据
    cnt = db.execute(
        "SELECT COUNT(*) FROM board_constituents WHERE date=?",
        (today,),
    ).fetchone()[0]
    if cnt > 0:
        logger.info("[成分股] 今日已有数据 ({cnt} 条)，跳过".format(cnt=cnt))
        db.close()
        return {"success": cnt, "failed": 0, "info": "今日已存在"}

    api = BoardApi()
    result = {"success": 0, "failed": 0}
    all_rows = []

    try:
        # 获取行业板块列表
        industries = api.get_industry_boards()
        logger.info(f"[成分股] 获取到 {len(industries)} 个行业板块")
    except Exception as e:
        logger.warning(f"[成分股] 行业板块列表获取失败: {e}")
        industries = []

    try:
        # 获取概念板块列表
        concepts = api.get_concept_boards()
        logger.info(f"[成分股] 获取到 {len(concepts)} 个概念板块")
    except Exception as e:
        logger.warning(f"[成分股] 概念板块列表获取失败: {e}")
        concepts = []

    all_boards = list(industries) + list(concepts)
    if not all_boards:
        db.close()
        return {"success": 0, "failed": 0, "error": "板块列表为空"}

    for i, b in enumerate(all_boards):
        code = b.get("code", "") or b.get("ts_code", "").replace(".DC", "")
        name = b.get("name", "")
        if not code:
            continue
        try:
            members = api._get_constituents(code)
            for m in members:
                m_code = m.get("con_code", "") or m.get("code", "")
                m_name = m.get("name", "")
                if not m_code:
                    continue
                all_rows.append((code, today, m_code, m_name))
            result["success"] += 1
            if (i + 1) % 100 == 0:
                logger.info(f"[成分股] 进度: {i + 1}/{len(all_boards)}")
        except Exception as e:
            result["failed"] += 1
            if result["failed"] <= 3:
                logger.warning(f"[成分股] 失败 {code} {name}: {e}")

    if all_rows:
        db.executemany(
            """INSERT OR REPLACE INTO board_constituents (code, date, constituent_code, constituent_name)
               VALUES (?, ?, ?, ?)""",
            all_rows,
        )
        db.commit()
        logger.info(f"[成分股] 写入 {len(all_rows)} 条 ({today})")

    db.close()
    result["info"] = f"共 {len(all_rows)} 条组成记录"
    return result


def _step_higher_periods():
    """Step V: 多周期物化"""
    from data_update_manager import materialize_higher_periods, PREWARM_TARGETS, PERMANENT_SKIP_INDICES
    codes = [c for c, _, _ in PREWARM_TARGETS if c not in PERMANENT_SKIP_INDICES]
    return materialize_higher_periods(codes=codes)


def _step_weekly_monthly():
    """板块周月线刷新"""
    from data_update_manager import refresh_all_boards_weekly_monthly
    return refresh_all_boards_weekly_monthly()


def _step_board_chg():
    """Step VI: 板块涨跌幅验证（从 CSV 读取最后一行数据）"""
    import csv
    import re
    from pathlib import Path

    base = Path(_BASE) / "data"
    found = 0
    errors = 0

    for dirname in ("行业板块K线数据", "概念板块K线数据"):
        d = base / dirname
        if not d.exists():
            continue
        for fn in d.iterdir():
            if not fn.name.endswith(".csv"):
                continue
            m = re.match(r".+_(BK\d+)\.csv$", fn.name)
            if not m:
                continue
            try:
                with open(fn, "r", encoding="utf-8-sig") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    if size < 50:
                        continue
                    f.seek(max(0, size - 500))
                    lines = f.read().strip().split("\n")
                    if len(lines) < 2:
                        continue
                    last = list(csv.reader([lines[-1]]))[0]
                    if len(last) >= 3 and last[2]:
                        found += 1
            except Exception:
                errors += 1

    logger.info(f"[板块涨跌] CSV 有效条目 {found} / 异常 {errors}")
    return {"success": found, "failed": errors, "info": f"CSV 扫描 {found} 条"}


def _step_search_index():
    """Step VII: 重建搜索索引"""
    try:
        from build_search_index import build_index_json
        build_index_json()
        logger.info("[搜索索引] 重建完成")
        return {"success": 1, "failed": 0}
    except ImportError:
        logger.warning("[搜索索引] build_search_index.py 不可导入")
        return {"success": 0, "failed": 0, "skipped": True, "message": "无法导入模块"}
    except Exception as e:
        logger.warning(f"[搜索索引] 重建失败: {e}")
        return {"success": 0, "failed": 1, "error": str(e)[:100]}


def _step_update_status():
    """Step VIII: 写回更新状态"""
    import json

    status_path = os.path.join(_BASE, "data", "update_status.json")
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()

    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
    else:
        status = {}

    status["today"] = today
    status["manual_update"] = now
    if "scheduler" not in status:
        status["scheduler"] = {}
    status["scheduler"]["last_manual_run"] = now
    status["scheduler"]["status"] = "manual"

    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    return {"success": 1, "failed": 0}


def _step_stocks():
    """Step IX: 个股K线（条件项，需 QMT 在线）"""
    from data_update_manager import _qmt_connect, qmt_update_all_stocks

    qmt_ok = _qmt_connect()
    if not qmt_ok:
        return {"success": 0, "failed": 0, "skipped": True, "message": "QMT 不可用"}
    return qmt_update_all_stocks(max_retries=2)


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="board-app 全量数据更新")
    parser.add_argument("--stocks", action="store_true", help="包含个股K线（需QMT在线）")
    parser.add_argument("--dry", action="store_true", help="干跑：只列出计划，不执行")
    parser.add_argument("--skip-constituents", action="store_true", help="跳过成分股更新")
    parser.add_argument("--no-panel", action="store_true", help="不自动启动 Flask 并打开浏览器面板")
    args = parser.parse_args()

    print(HEADER.format(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    print()

    STEPS = [
        ("I   指数K线(12只, 需QMT)", "indices", _step_indices),
        ("II  行业板块(497只)", "industry", _step_industry_boards),
        ("III 概念板块(495只)", "concept", None),  # 含在 update_all_boards 中
        ("IV  板块成分股", "constituents", _step_constituents if not args.skip_constituents else None),
        ("V   多周期物化(周/月/季/年)", "higher", _step_higher_periods),
        ("VI  板块涨跌幅", "board_chg", _step_board_chg),
        ("VII 搜索索引重建", "search", _step_search_index),
        ("VIII更新状态写回", "status", _step_update_status),
    ]

    if args.stocks:
        STEPS.append(
            ("IX  个股K线(5603只,需QMT)", "stocks", _step_stocks),
        )

    if args.dry:
        print("[干跑] 计划执行的步骤:")
        for i, (label, key, fn) in enumerate(STEPS, 1):
            if fn:
                print(f"  {i}. {label}")
        print(f"\n共 {sum(1 for _, _, f in STEPS if f)} 步（另有概念板块并入行业板块）")
        return

    _force_reset_gates()

    results = {}
    t_start = time.perf_counter()

    for label, key, fn in STEPS:
        if key == "concept":
            continue  # 概念板块合并到 industry 一起跑
        if fn is None:
            logger.info(f"[{label}] 已跳过")
            continue

        logger.info(f"--- [{label}] 开始 ---")
        t0 = time.perf_counter()
        try:
            r = fn()
            elapsed = _elapsed_ms(t0)
            _print_result(label, r, elapsed)
            results[key] = r
        except Exception as e:
            elapsed = _elapsed_ms(t0)
            logger.error(f"[{label}] 异常 ({elapsed:.0f}ms): {e}", exc_info=True)
            results[key] = {"success": 0, "failed": 1, "error": str(e)[:200]}

    total_ms = _elapsed_ms(t_start)
    logger.info(f"\n{'=' * 50}")
    logger.info(f"[全量更新] 完成 ({total_ms / 1000:.1f}s)")

    # 汇总
    total_success = sum(
        (r.get("success", 0) or 0) + (r.get("updated", 0) or 0)
        for r in results.values()
        if not r.get("skipped")
    )
    total_fail = sum(r.get("failed", 0) or 0 for r in results.values())
    logger.info(f"[汇总] 成功 {total_success} / 失败 {total_fail}")
    logger.info(f"[日志] {log_file}")
    print(f"\n日志文件: {log_file}")
    if not args.stocks:
        print("提示: 用 --stocks 可包含个股K线更新（需QMT在线）")

    # 自动打开面板
    if not args.no_panel:
        _launch_panel()
        print("\n面板将在浏览器中打开…")
    print(f"全部完成 ({total_ms / 1000:.1f}s)，窗口 5 秒后关闭")
    time.sleep(5)


def _launch_panel():
    """启动 Flask（如未运行）+ 打开浏览器面板"""
    import subprocess
    import urllib.request

    panel_url = "http://127.0.0.1:5000"
    flask_running = False

    # 检测 Flask 是否已在监听
    try:
        req = urllib.request.Request(panel_url)
        req.add_header("User-Agent", "board-app/launcher")
        urllib.request.urlopen(req, timeout=3)
        flask_running = True
    except Exception:
        flask_running = False

    if not flask_running:
        logger.info("[面板] Flask 未运行，启动中…")
        venv_python = os.path.join(_BASE, "venv", "Scripts", "python.exe")
        subprocess.Popen(
            [venv_python, "app.py"],
            cwd=_BASE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        # 等待启动
        for _ in range(10):
            time.sleep(1)
            try:
                urllib.request.urlopen(panel_url, timeout=2)
                break
            except Exception:
                continue
        logger.info("[面板] Flask 已启动")

    # 打开浏览器
    try:
        import webbrowser
        webbrowser.open(panel_url)
        logger.info(f"[面板] 浏览器已打开 → {panel_url}")
    except Exception as e:
        logger.warning(f"[面板] 浏览器打开失败: {e}")
        if sys.platform == "win32":
            try:
                subprocess.run(["start", panel_url], shell=True)
            except Exception:
                pass


if __name__ == "__main__":
    main()
