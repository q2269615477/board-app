"""
lifecycle.py — 应用生命周期管理
端口架构：
  - 58600: QMT标准客户端 server_formula（公式 RPC / qmt_api 主取数通道）
  - xtdata 行情服务常需 Mini 58610；本环境可能空壳，日线以 qmt_api 为准
"""
import os
import json
import time
import threading
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('lifecycle')

from core.config import (
    QMT_PYTHON_PATH, QMT_DIR, QMT_ENABLED, QMT_AUTO_START, QMT_DATA_DIR,
    QMT_STARTUP_HISTORY_SYNC, STARTUP_PREWARM,
    BASE_DIR, DATA_DIR, PREWARM_TARGETS, BOARD_CHG_REFRESH_INTERVAL
)
from services.data_update_scheduler import data_update_scheduler
from services.miniqmt_service import miniqmt_service
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
        self._start_lock = threading.Lock()
        self._started = False
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
            'miniqmt_service': miniqmt_service.get_status(),
        }

    def start(self):
        """启动应用：依赖检查 → 后台/调度 → 异步探测 58600 行情

        主通道：完整 QMT 登录后走 58600 RPC（不依赖 MiniQMT）。
        MiniQMT 仅当 QMT_AUTO_START=1 时由应用托管；默认可不启动。

        启动锁覆盖整个启动序列，保证并发调用只执行一次。只有完整序列
        成功后才标记为已启动。依赖检查阶段失败可安全重试；后台线程启动
        后的异常由各启动步骤自行隔离，避免重复创建后台资源。
        """
        with self._start_lock:
            if self._started:
                logger.debug("=== 应用已启动，跳过重复启动 ===")
                return

            logger.info("=== 应用启动 ===")
            # 注入 TUSHARE_TOKEN 等本地 env（.env / ~/.board-app.env）
            try:
                from core.env_bootstrap import ensure_tushare_token
                if ensure_tushare_token():
                    logger.info("[OK] TUSHARE_TOKEN 已就绪")
                else:
                    logger.warning("[WARN] TUSHARE_TOKEN 未就绪，Tushare 回填/板块日更将失败")
            except Exception as e:
                logger.warning(f"[WARN] env bootstrap 失败: {e}")
            self._check_dependencies()
            self._start_background_services()  # 可选 MiniQMT + 真实日更调度
            self._start_qmt()  # 异步探测完整 QMT / 58600
            kill_orphaned()
            write_pid()
            register_cleanup()
            self._started = True
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
        """QMT 标准客户端预热（58600 RPC，完整 QMT 登录后可用）"""
        if not QMT_ENABLED:
            logger.info("[QMT] 已禁用")
            return
        t = threading.Thread(target=self._qmt_init_worker, daemon=True)
        t.start()
        self._threads.append(t)

    def _qmt_init_worker(self):
        """验证 58600 行情可用性（不依赖 MiniQMT 进程）。

        协议分流（文档已实证）：
        - **主通道 qmt_api 公式口**：net.RPCClient.request('getMarketData')，同 58600，
          可取真实日线 OHLCV（不依赖 Mini 58610）。
        - **次通道 xtdata**：get_market_data3，常需 server_ipythonapi(58610)；
          仅连 58600 公式口时往往空壳（无 bar / subscribe=-2）。

        判定顺序：先公式口有 bar → qmt_available=True；再记录 xtdata 状态。
        """
        global _qmt_available
        logger.info("[QMT] 验证 58600（优先 qmt_api 公式口日线）...")
        if not os.path.exists(QMT_PYTHON_PATH):
            self._qmt_warning = f"QMT Python 不存在: {QMT_PYTHON_PATH}"
            logger.warning(f"[QMT] {self._qmt_warning}")
            logger.info("[QMT] ⚠️ 使用缓存数据（无QMT）")
            return

        for i in range(8):
            if _port_open(58600):
                if i:
                    logger.info(f"[QMT] 58600 已监听，开始探测 ({i+1}s)")
                break
            time.sleep(1)
        else:
            logger.warning("[QMT] 58600 暂未监听，仍尝试取数")

        try:
            # ---- 主通道：qmt_api 公式 RPC（文档记载可取真实日线）----
            formula_rows = 0
            formula_last = ''
            try:
                from data.qmt_client import get_qmt_client
                probe = get_qmt_client().probe_formula_ready()
                if probe.get('ok') and int(probe.get('rows') or 0) > 0:
                    formula_rows = int(probe['rows'])
                    formula_last = str(probe.get('last_date') or '')
                    _qmt_available = True
                    self.qmt_available = True
                    self._qmt_warning = (
                        f"QMT 公式口就绪（qmt_api / net.RPCClient getMarketData @58600），"
                        f"日线样本 {formula_rows} 条"
                        + (f"，末 bar={formula_last}" if formula_last else "")
                        + "。xtdata 标准路径若空壳属预期（需 Mini 58610 行情服务）。"
                    )
                    logger.info(
                        f"[QMT] ✓ 公式口就绪 rows={formula_rows} last={formula_last!r}"
                    )
                    self._start_qmt_history_sync()
                    return
                logger.info(f"[QMT] 公式口探测未就绪: {probe}")
            except Exception as e:
                logger.warning(f"[QMT] 公式口探测异常: {e}")

            # ---- 次通道：xtdata（58610/完整行情服务）----
            probe = (
                'from xtquant import xtdata\n'
                'import json\n'
                f'DATA_DIR = r"{QMT_DATA_DIR}"\n'
                'xtdata.reconnect("127.0.0.1", 58600)\n'
                'c = xtdata.get_client()\n'
                'connected = bool(c.is_connected()) if c is not None else False\n'
                'client_data_dir = ""\n'
                'client_app_dir = ""\n'
                'try:\n'
                '    client_data_dir = str(c.get_data_dir() or "")\n'
                'except Exception:\n'
                '    pass\n'
                'try:\n'
                '    client_app_dir = str(c.get_app_dir() or "")\n'
                'except Exception:\n'
                '    pass\n'
                'rows = 0\n'
                'detail_ok = False\n'
                'sector_n = 0\n'
                'err = ""\n'
                'quote_err = ""\n'
                'try:\n'
                '    xtdata.download_history_data("000001.SH", "1d", "20260601", "20260717")\n'
                'except Exception as e:\n'
                '    err = str(e)[:120]\n'
                'try:\n'
                '    d = xtdata.get_local_data(["time","open","high","low","close","volume"],'
                '["000001.SH"],"1d","20260601","20260717",count=5,data_dir=DATA_DIR)\n'
                '    df = d.get("000001.SH") if isinstance(d, dict) else None\n'
                '    if df is not None and hasattr(df, "empty") and not df.empty:\n'
                '        rows = int(len(df))\n'
                'except Exception as e:\n'
                '    err = (err + "|" + str(e)[:80]) if err else str(e)[:120]\n'
                'try:\n'
                '    det = xtdata.get_instrument_detail("000001.SH") or {}\n'
                '    detail_ok = bool(det.get("InstrumentID") or det.get("InstrumentName")'
                ' or det.get("LastPrice"))\n'
                'except Exception:\n'
                '    pass\n'
                'try:\n'
                '    lst = xtdata.get_stock_list_in_sector("沪深A股") or []\n'
                '    sector_n = len(lst)\n'
                'except Exception as e:\n'
                '    quote_err = str(e)[:80]\n'
                'try:\n'
                '    sid = xtdata.subscribe_quote("000001.SH", period="1d", count=1)\n'
                '    if int(sid) < 0:\n'
                '        quote_err = (quote_err + f"|subscribe={sid}").strip("|")\n'
                'except Exception as e:\n'
                '    quote_err = (quote_err + "|" + str(e)[:80]).strip("|")\n'
                'print(json.dumps({"connected": connected, "rows": rows, '
                '"detail_ok": detail_ok, "sector_n": sector_n, '
                '"err": err, "quote_err": quote_err, "data_dir": DATA_DIR, '
                '"client_data_dir": client_data_dir, "client_app_dir": client_app_dir}, '
                'ensure_ascii=False))\n'
            )
            proc = subprocess.run(
                [QMT_PYTHON_PATH, '-c', probe],
                capture_output=True, timeout=30, text=True, cwd=QMT_DIR
            )
            out_lines = (proc.stdout or '').strip().splitlines()
            payload = {}
            for line in reversed(out_lines):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    payload = json.loads(line)
                    break
            connected = bool(payload.get('connected'))
            rows = int(payload.get('rows') or 0)
            detail_ok = bool(payload.get('detail_ok'))
            sector_n = int(payload.get('sector_n') or 0)
            err = str(payload.get('err') or '')
            quote_err = str(payload.get('quote_err') or '')
            client_data_dir = str(payload.get('client_data_dir') or '')
            client_app_dir = str(payload.get('client_app_dir') or '')
            data_ok = rows > 0 or detail_ok

            if connected and data_ok:
                _qmt_available = True
                self.qmt_available = True
                self._qmt_warning = ""
                logger.info(
                    f"[QMT] ✓ xtdata 行情就绪 connected={connected} rows={rows} "
                    f"detail_ok={detail_ok} sector_n={sector_n} "
                    f"client_data_dir={client_data_dir!r}"
                )
                self._start_qmt_history_sync()
                return

            if connected and not data_ok:
                self._qmt_warning = (
                    "QMT 58600 已连接，但公式口与 xtdata 均未取到日线。"
                    "请确认完整 QMT 已登录且本地有 K 线缓存；"
                    "分钟线另需 Mini 行情服务(58610)。"
                )
                extras = []
                if not client_data_dir:
                    extras.append("client_data_dir=空")
                if sector_n:
                    extras.append(f"sector_n={sector_n}")
                if err:
                    extras.append(f"err={err}")
                if quote_err:
                    extras.append(f"quote={quote_err}")
                if extras:
                    self._qmt_warning += " | " + " ; ".join(extras)
                logger.warning(f"[QMT] ⚠️ {self._qmt_warning}")
            else:
                self._qmt_warning = (
                    "QMT 未连接 58600。请先启动并登录完整 QMT 客户端。"
                    f" stdout={(proc.stdout or '')[:100]!r}"
                )
                logger.warning(f"[QMT] ⚠️ {self._qmt_warning}")
        except Exception as e:
            logger.warning(f"[QMT] RPC验证失败: {e}")
            self._qmt_warning = f"QMT连接失败: {str(e)[:100]}。请检查完整 QMT 是否已登录"
        logger.info("[QMT] ⚠️ 使用缓存/Tushare 回退（QMT 行情未就绪）")

    def _start_qmt_history_sync(self):
        """Run the legacy startup history sync only when explicitly enabled."""
        if not QMT_STARTUP_HISTORY_SYNC:
            logger.info(
                "[QMT同步] 跳过启动全历史同步；日更管线按需维护数据 "
                "(QMT_STARTUP_HISTORY_SYNC=1 可恢复旧行为)"
            )
            return
        t = threading.Thread(target=self._qmt_sync_all, daemon=True)
        t.start()
        self._threads.append(t)

    def _qmt_sync_all(self):
        """后台同步常用标的到 SQLite（优先 qmt_api 公式口日线）"""
        import sqlite3
        from core.config import Config

        targets = [
            ('sh000001', '000001.SH'), ('sz399006', '399006.SZ'),
            ('sh000688', '000688.SH'), ('sh000300', '000300.SH'),
            ('sh000016', '000016.SH'), ('sh000852', '000852.SH'),
            ('sh000853', '000853.SH'), ('sh000985', '000985.SH'),
            ('HSI', 'HSI.HK'), ('HSTECH', 'HSTECH.HK'),
            ('600519', '600519.SH'), ('600036', '600036.SH'),
        ]
        try:
            from data.qmt_client import get_qmt_client
            client = get_qmt_client()
        except Exception as e:
            logger.warning(f"[QMT同步] 无法加载 qmt_client: {e}")
            return

        # 批量公式口（单子进程）覆盖多数标的
        qmt_codes = [qc for _, qc in targets]
        batch = {}
        try:
            batch = client.get_daily_batch(qmt_codes, start='20200101', count=-1) or {}
        except Exception as e:
            logger.debug(f"[QMT同步] batch 失败，改逐只: {e}")

        synced = 0
        for code, qmt_code in targets:
            try:
                df = batch.get(qmt_code)
                if df is None or getattr(df, 'empty', True):
                    df = client.get_daily(qmt_code, start='20200101', count=-1)
                if df is None or getattr(df, 'empty', True):
                    continue
                conn = sqlite3.connect(str(Config.SQLITE_PATH))
                for _, row in df.iterrows():
                    raw_date = str(row.get('date', ''))
                    if len(raw_date) == 8 and raw_date.isdigit():
                        raw_date = f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}'
                    elif len(raw_date) >= 10:
                        raw_date = raw_date[:10]
                    conn.execute(
                        'INSERT OR REPLACE INTO kline '
                        '(code,period,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?,?)',
                        (
                            code, 'daily', raw_date,
                            float(row.get('open') or 0),
                            float(row.get('high') or 0),
                            float(row.get('low') or 0),
                            float(row.get('close') or 0),
                            int(float(row.get('volume') or 0)),
                        ),
                    )
                conn.commit()
                conn.close()
                synced += 1
            except Exception as e:
                logger.debug(f"[QMT同步] {code} 失败: {e}")
        logger.info(f"[QMT同步] 公式口/统一通道写入 {synced}/{len(targets)} 个标的")

    # ---- 后台服务 ----

    def _start_background_services(self):
        """启动后台守护线程"""
        t1 = threading.Thread(target=self._board_chg_loop, daemon=True)
        t1.start()
        self._threads.append(t1)

        if STARTUP_PREWARM:
            t2 = threading.Thread(target=self._prewarm_indices, daemon=True)
            t2.start()
            self._threads.append(t2)
        else:
            logger.info(
                "[生命周期] 跳过启动指数预热；首屏按缓存优先加载 "
                "(BOARD_APP_STARTUP_PREWARM=1 可恢复旧行为)"
            )

        # MiniQMT 可选：默认关闭（完整 QMT 已登录即可）；需托管时设 QMT_AUTO_START=1
        if QMT_AUTO_START:
            try:
                miniqmt_service.set_mode('application')
                success = miniqmt_service.start()
                if success:
                    time.sleep(2)
                    status = miniqmt_service.get_status()
                    if status['process_alive']:
                        logger.info("[生命周期] MiniQMT 服务已启动（QMT_AUTO_START=1，心跳+看门狗）")
                    else:
                        logger.warning("[生命周期] MiniQMT 服务已启动，但进程尚未就绪，监控线程将继续尝试")
                else:
                    logger.error("[生命周期] MiniQMT 服务启动失败")
            except Exception as e:
                logger.error(f"[生命周期] MiniQMT 服务启动异常: {e}")
        else:
            logger.info("[生命周期] 跳过 MiniQMT 自动启动（主通道=完整 QMT / 58600；需要时设 QMT_AUTO_START=1）")

        # 启动数据更新调度器：
        # services.data_update_scheduler 仅是状态/手动触发门面；
        # 真实交易日历循环在 data_update_manager.start_scheduler()。
        try:
            # start() 内部已挂接 data_update_manager 真实日更循环，勿重复 start
            data_update_scheduler.start()
            logger.info("[生命周期] 数据更新调度器已启动（门面+真实日更循环）")
        except Exception as e:
            logger.error(f"[生命周期] 调度器启动失败: {e}")

    def _board_chg_loop(self):
        """后台循环：刷新板块涨跌幅"""
        while not self._stop_event.is_set():
            self._reload_board_changes()
            self._stop_event.wait(BOARD_CHG_REFRESH_INTERVAL)

    def _reload_board_changes(self):
        """从统一 BoardSpotCache 派生板块涨跌幅。"""
        try:
            from services.board_spot_cache import get_board_spot_cache
            cache = get_board_spot_cache()
            cache.get('industry')
            cache.get('concept')
            result = cache.get_chgs()
        except Exception as e:
            logger.debug(f"[涨跌幅] BoardSpotCache 不可用: {e}")
            result = {}
        with self.board_chg_lock:
            self.board_chg_cache = result
            if result:
                logger.info(f"[涨跌幅] BoardSpotCache 已加载 {len(result)} 个板块涨跌幅")

    def get_board_changes_cached(self) -> dict:
        """获取板块涨跌幅（线程安全，首次调用同步加载）"""
        with self.board_chg_lock:
            if self.board_chg_cache:
                return self.board_chg_cache.copy()
        # 首次调用同步触发统一缓存，后续由后台线程按 TTL 刷新
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

        from data.market_kline import load_index_kline, load_hk_index_kline
        from data.board_kline import load_board_kline
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
_app_context_lock = threading.Lock()


def get_app_context() -> AppContext:
    global _app_context
    if _app_context is None:
        with _app_context_lock:
            if _app_context is None:
                _app_context = AppContext()
    return _app_context


def start_app() -> AppContext:
    """启动应用"""
    ctx = get_app_context()
    ctx.start()
    return ctx
