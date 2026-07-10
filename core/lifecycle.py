"""
lifecycle.py — 应用生命周期管理
端口架构：
  - 58600: QMT标准客户端 rpc_init（公式/策略引擎）
（MiniQMT 58610 已移除，所有数据走 58600 RPC）
"""
import os
import time
import threading
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('lifecycle')

from core.config import (
    QMT_PYTHON_PATH, QMT_DIR, QMT_ENABLED, BASE_DIR, DATA_DIR,
    PREWARM_TARGETS, BOARD_CHG_REFRESH_INTERVAL
)
from services.data_update_scheduler import data_update_scheduler
from services.miniqmt_manager import miniqmt_manager
from core.cache import get_cache, kill_orphaned, write_pid, register_cleanup


def _port_open(port: int) -> bool:
    """检测 TCP 端口是否可连接"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        r = s.connect_ex(('127.0.0.1', port))
        s.close()
        return r == 0
    except Exception:
        return False

# QMT可用标志（由QMT初始化线程设置）
_qmt_available = False


def is_qmt_available() -> bool:
    """QMT是否可用"""
    return _qmt_available


# ---- 应用上下文 ----

class AppContext:
    """统一管理应用运行时状态"""

    def __init__(self):
        self.started_at = time.time()
        self.qmt_available = False
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self.board_chg_cache: dict = {}
        self.board_chg_lock = threading.Lock()
        self._qmt_warning: str = ""

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    def get_qmt_warning(self) -> str:
        """获取QMT连接警告信息"""
        return self._qmt_warning

    def get_system_status(self) -> dict:
        """获取系统整体状态（用于前端展示）"""
        return {
            'qmt_available': self.qmt_available,
            'qmt_warning': self._qmt_warning,
            'uptime_seconds': self.uptime_seconds,
            'scheduler_running': data_update_scheduler.is_running(),
            'miniqmt_manager': miniqmt_manager.get_status(),
        }

    def start(self):
        """启动应用：依赖检查 → QMT预热 → 后台服务"""
        logger.info("=== 应用启动 ===")
        self._check_dependencies()
        self._start_qmt()
        self._start_background_services()
        kill_orphaned()
        write_pid()
        register_cleanup()
        logger.info("=== 启动完成 ===")

    def _check_dependencies(self):
        from core.config import Config
        missing = Config.validate()
        if missing:
            for m in missing:
                logger.error(f"缺少必要文件: {m}")
            raise RuntimeError(f"缺失依赖: {missing}")
        logger.info("[OK] 依赖检查通过")

    # ---- QMT 初始化 ----

    def _start_qmt(self):
        """QMT标准客户端预热（58600端口，无需启动miniquote）"""
        if not QMT_ENABLED:
            logger.info("[QMT] 已禁用")
            return
        t = threading.Thread(target=self._qmt_init_worker, daemon=True)
        t.start()
        self._threads.append(t)

    def _qmt_init_worker(self):
        """QMT初始化：验证标准QMT客户端RPC并测试数据可用性"""
        global _qmt_available
        logger.info("[QMT] 验证标准客户端RPC(58600)...")
        try:
            # Step 1: rpc_init
            proc = subprocess.run(
                [QMT_PYTHON_PATH, '-c',
                 'from xtquant import xtdata;'
                 'r=xtdata.rpc_init("127.0.0.1:58600");'
                 'print("RPC="+str(r))'],
                capture_output=True, timeout=10, text=True, cwd=QMT_DIR
            )
            if 'RPC=0' not in proc.stdout:
                logger.info("[QMT] ⚠️ 使用缓存数据（无QMT）")
                return

            # Step 2: 实际数据测试（验证QMT.exe正在运行）
            proc2 = subprocess.run(
                [QMT_PYTHON_PATH, '-c',
                 'from xtquant import xtdata;'
                 'xtdata.rpc_init("127.0.0.1:58600");'
                 'xtdata.download_history_data("000001.SH","1d","20260701","20260702");'
                 'd=xtdata.get_local_data(["time","open","high","low","close","volume"],["000001.SH"],"1d","20260701","20260702",count=1);'
                 'print("OK" if "000001.SH" in d and d["000001.SH"] is not None and not d["000001.SH"].empty else "FAIL")'],
                capture_output=True, timeout=15, text=True, cwd=QMT_DIR
            )
            if 'OK' in proc2.stdout:
                _qmt_available = True
                self.qmt_available = True
                logger.info("[QMT] ✓ 标准客户端RPC就绪（数据服务正常）")
                # 后台同步常用标的到SQLite
                threading.Thread(target=self._qmt_sync_all, daemon=True).start()
                return
            else:
                logger.warning("[QMT] ⚠️ RPC连接成功但数据服务不可用（请确认QMT.exe已启动）")
                self._qmt_warning = "QMT RPC连接成功但数据服务不可用，请确认QMT.exe已启动并连接行情"
        except Exception as e:
            logger.warning(f"[QMT] RPC验证失败: {e}")
            self._qmt_warning = f"QMT连接失败: {str(e)[:100]}，请检查MiniQMT是否启动"
        logger.info("[QMT] ⚠️ 使用缓存数据（无QMT）")

    def _qmt_sync_all(self):
        """后台同步常用标的到SQLite"""
        import json
        import sqlite3
        import pandas as pd
        from core.config import Config

        today = pd.Timestamp.now().strftime('%Y%m%d')
        targets = [
            ('sh000001','000001.SH'),('sz399006','399006.SZ'),
            ('sh000688','000688.SH'),('sh000300','000300.SH'),
            ('sh000016','000016.SH'),('sh000852','000852.SH'),
            ('sh000853','000853.SH'),('sh000985','000985.SH'),
            ('HSI','HSI.HK'),('HSTECH','HSTECH.HK'),
            ('600519','600519.SH'),('600036','600036.SH'),
        ]
        for code, qmt_code in targets:
            try:
                script = (
                    "from xtquant import xtdata; import pandas as pd, json\n"
                    f"xtdata.rpc_init('127.0.0.1:58600')\n"
                    f"xtdata.download_history_data('{qmt_code}','1d','{today}','{today}')\n"
                    f"d=xtdata.get_local_data(['time','open','high','low','close','volume'],['{qmt_code}'],'1d','{today}','{today}',count=2)\n"
                    f"if '{qmt_code}' in d and d['{qmt_code}'] is not None and not d['{qmt_code}'].empty:\n"
                    "  rows=[]\n"
                    "  for idx,row in d['{qmt_code}'].iterrows():\n"
                    "    t=int(idx) if isinstance(idx,(int,float)) and idx>1e9 else 0\n"
                    "    ds=str(pd.to_datetime(t,unit='ms').date()) if t>1e9 else str(idx)[:10]\n"
                    "    vol=int(float(row['volume'])) or 0\n"
                    "    if vol==0: continue\n"
                    "    rows.append({'date':ds,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol})\n"
                    "  print(json.dumps({'ok':True,'data':rows}))\n"
                    "else: print(json.dumps({'ok':False}))"
                )
                proc = subprocess.run(
                    [QMT_PYTHON_PATH, '-c', script],
                    capture_output=True, timeout=15, cwd=QMT_DIR
                )
                out = proc.stdout.decode('utf-8', errors='ignore').strip()
                result = json.loads(out) if out else {}
                if result.get('ok') and result.get('data'):
                    conn = sqlite3.connect(str(Config.SQLITE_PATH))
                    for row in result['data']:
                        # 统一日期格式为 YYYY-MM-DD，防止写入混合格式
                        raw_date = str(row.get('date', ''))
                        if len(raw_date) == 8 and raw_date.isdigit():
                            raw_date = f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}'
                        conn.execute(
                            'INSERT OR REPLACE INTO kline (code,period,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?,?)',
                            (code, 'daily', raw_date, row.get('open'), row.get('high'), row.get('low'), row.get('close'), row.get('volume')))
                    conn.commit()
                    conn.close()
            except Exception:
                pass

    # ---- 后台服务 ----

    def _start_background_services(self):
        """启动后台守护线程"""
        t1 = threading.Thread(target=self._board_chg_loop, daemon=True)
        t1.start()
        self._threads.append(t1)

        t2 = threading.Thread(target=self._prewarm_indices, daemon=True)
        t2.start()
        self._threads.append(t2)

        # 启动 MiniQMT 常驻管理器（心跳自动唤醒）
        try:
            miniqmt_manager.start()
            logger.info("[生命周期] MiniQMT 常驻管理器已启动（心跳间隔 30 秒）")
        except Exception as e:
            logger.error(f"[生命周期] MiniQMT 管理器启动失败: {e}")

        # 启动数据更新调度器
        try:
            data_update_scheduler.start()
            logger.info("[生命周期] 数据更新调度器已启动")
        except Exception as e:
            logger.error(f"[生命周期] 调度器启动失败: {e}")

    def _board_chg_loop(self):
        """后台循环：刷新板块涨跌幅"""
        while not self._stop_event.is_set():
            self._reload_board_changes()
            self._stop_event.wait(BOARD_CHG_REFRESH_INTERVAL)

    def _reload_board_changes(self):
        """读取CSV获取板块涨跌幅（用 reader 按列定位，兼容 DictReader 无表头问题）"""
        import csv
        import re
        result = {}
        base = DATA_DIR
        for dirname, dtype in [('行业板块K线数据', 'industry'), ('概念板块K线数据', 'concept')]:
            d = base / dirname
            if not d.exists():
                continue
            for fn in d.iterdir():
                if not fn.name.endswith('.csv'):
                    continue
                m = re.match(r'.+_(BK\d+)\.csv$', fn.name)
                if not m:
                    continue
                code = m.group(1)
                try:
                    with open(fn, 'r', encoding='utf-8-sig') as f:
                        f.seek(0, 2)
                        size = f.tell()
                        if size < 50:
                            continue
                        f.seek(max(0, size - 500))
                        lines = f.read().strip().split('\n')
                        if len(lines) < 2:
                            continue
                        # 读最后两行：CSV列位 收盘=2, 涨跌幅=5
                        last = list(csv.reader([lines[-1]]))[0]
                        if len(last) >= 6 and last[5]:
                            chg = round(float(last[5]), 2)
                        elif len(last) >= 3 and last[2]:
                            prev = list(csv.reader([lines[-2]]))[0]
                            c0 = float(last[2])
                            c1 = float(prev[2]) if len(prev) >= 3 else c0
                            chg = round((c0 / c1 - 1) * 100, 2) if c1 != 0 else 0
                        else:
                            continue
                        result[f'{dtype}:{code}'] = chg
                except Exception as e:
                    logger.debug(f"[涨跌幅] 读取 {code} CSV 失败: {e}")
        with self.board_chg_lock:
            self.board_chg_cache = result
            if result:
                logger.info(f"[涨跌幅] 已加载 {len(result)} 个板块涨跌幅")

    def get_board_changes_cached(self) -> dict:
        """获取板块涨跌幅（线程安全，首次调用同步加载）"""
        with self.board_chg_lock:
            if self.board_chg_cache:
                return self.board_chg_cache.copy()
        # 首次调用：同步加载（991个CSV约1-2秒），后续由后台线程刷新
        self._reload_board_changes()
        with self.board_chg_lock:
            return self.board_chg_cache.copy()

    def _prewarm_indices(self):
        """预加载顶部指数"""
        try:
            from data_update_manager import is_today_updated
            if is_today_updated():
                logger.info("[预热] 今日已更新，跳过")
                return
        except Exception:
            pass

        from data_loader import load_index_kline, load_hk_index_kline, load_board_kline
        logger.info("[预热] 预加载顶部指数...")
        for code, name, typ in PREWARM_TARGETS:
            if self._stop_event.is_set():
                break
            try:
                t0 = time.time()
                if typ == 'hk_index':
                    load_hk_index_kline(code)
                elif typ == 'concept':
                    load_board_kline('concept', name, code)
                else:
                    load_index_kline(code)
                logger.info(f"[预热] {name} 完成 ({time.time()-t0:.1f}s)")
            except Exception as e:
                logger.warning(f"[预热] {name} 失败: {e}")


# 全局应用上下文
_app_context: Optional[AppContext] = None


def get_app_context() -> AppContext:
    global _app_context
    if _app_context is None:
        _app_context = AppContext()
    return _app_context


def start_app() -> AppContext:
    """启动应用"""
    ctx = get_app_context()
    ctx.start()
    return ctx
