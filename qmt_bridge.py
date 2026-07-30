"""
qmt_bridge.py - QMT 行情桥接器（修正版）
通过 xtdata.connect(port=58610) 连接 XtMiniQmt 服务
将 QMT 本地数据写入面板的 SQLite 数据库

端口说明：
  - 58610: XtMiniQmt 行情服务（由本模块启动）
  - 58670: 独立 miniquote 服务端口（未使用）

启动方式：
  1. 本模块自动调用 qmt_launcher.py 启动 XtMiniQmt.exe
  2. 连接成功后通过 xtdata API 读取数据
  3. 数据写入 kline.db，面板现有逻辑零改动
"""
import os
import sys
import time
import logging
import subprocess
import threading
import socket
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from core.config import QMT_DIR, QMT_PORTS

logger = logging.getLogger('qmt_bridge')

# QMT安装路径（统一从 config.py 读取）
QMT_BIN = QMT_DIR
QMT_MINI = os.path.join(QMT_BIN, 'XtMiniQmt.exe')

# ====== 进程管理 ======

_qmt_process = None
_qmt_connected = False
_qmt_connect_lock = threading.Lock()

def ensure_runtime() -> bool:
    """确保 XtMiniQmt 行情服务正在运行"""
    global _qmt_process
    
    # 先检查端口是否已开放
    if _port_open(QMT_PORTS[1]):
        return True
    
    # 尝试启动
    if not os.path.exists(QMT_MINI):
        logger.error(f"[QMT] XtMiniQmt.exe 不存在: {QMT_MINI}")
        return False
    
    try:
        logger.info(f"[QMT] 启动 XtMiniQmt.exe...")
        # DETACHED_PROCESS 方式经验证可以启动成功（进程存活15-30分钟）
        # 配合懒启动 + 惰性重启机制，在数据请求时自动拉起，确保不干扰QMT客户端
        _qmt_process = subprocess.Popen(
            [QMT_MINI],
            cwd=QMT_BIN,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        # 等待端口开放（最长10秒）
        for i in range(20):
            time.sleep(0.5)
            if _port_open(QMT_PORTS[1]):
                logger.info(f"[QMT] XtMiniQmt 启动成功 (PID={_qmt_process.pid})")
                return True
            if i == 10:
                logger.info(f"[QMT] 等待启动中... ({(i+1)*0.5:.0f}s)")
        
        logger.warning(f"[QMT] XtMiniQmt 启动超时")
        return False
    except Exception as e:
        logger.error(f"[QMT] 启动失败: {e}")
        return False


def _port_open(port: int = None) -> bool:
    if port is None:
        port = QMT_PORTS[1]
    """检查 TCP 端口是否可连接"""
    try:
        s = socket.socket()
        s.settimeout(1)
        r = s.connect_ex(('127.0.0.1', port))
        s.close()
        return r == 0
    except Exception:
        return False  # 端口不可达（QMT未运行）


def start_watchdog():
    """启动后台守护线程，每30秒检查QMT端口，离线自动重启"""
    def _watch():
        while True:
            if not _port_open(QMT_PORTS[1]):
                logger.warning("[QMT] 检测到行情服务离线，正在重启...")
                ensure_runtime()
            time.sleep(30)
    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    logger.info("[QMT] 守护线程已启动（每30秒心跳）")


def stop():
    """停止 QMT 行情服务"""
    global _qmt_process, _qmt_connected
    _qmt_connected = False
    if _qmt_process:
        _qmt_process.terminate()
        _qmt_process = None


# ====== 连接管理 ======

def connect() -> bool:
    """连接到 QMT 行情服务（保持长连接，避免频繁断开触发进程清理）"""
    global _qmt_connected
    with _qmt_connect_lock:
        # 长连接：一旦连上就不再主动断开
        if _qmt_connected:
            # 检查端口是否真的还在
            if _port_open(QMT_PORTS[1]):
                return True
            _qmt_connected = False
        
        # 确保进程在运行
        if not ensure_runtime():
            return False
        
        try:
            from xtquant import xtdata
            xtdata.connect(port=QMT_PORTS[1])
            xtdata.enable_hello = False
            _qmt_connected = True
            logger.info(f"[QMT] xtdata 连接成功 (127.0.0.1:{QMT_PORTS[1]})")
            # 启动心跳线程
            _start_keepalive()
            return True
        except Exception as e:
            logger.warning(f"[QMT] 连接失败: {e}")
            _qmt_connected = False
            return False


_keepalive_started = False

def _start_keepalive():
    """后台心跳线程：每60秒做一次轻量查询，保持连接活性"""
    global _keepalive_started
    if _keepalive_started:
        return
    _keepalive_started = True
    def _ping():
        while True:
            time.sleep(60)
            try:
                if _qmt_connected and _port_open(QMT_PORTS[1]):
                    from xtquant import xtdata
                    # 轻量查询：获取一个已知代码的详情，保持连接活性
                    xtdata.get_instrument_detail('000001.SH')
            except Exception:
                pass  # QMT心跳查询失败不影响主流程
    threading.Thread(target=_ping, daemon=True).start()


def disconnect():
    global _qmt_connected
    _qmt_connected = False


# ====== 数据读取 ======

def get_kline(qmt_code: str, start_date: str = '20200101', end_date: str = '') -> list:
    """
    从 QMT 读取日线数据
    返回 [{date, open, high, low, close, volume}, ...]
    """
    if not connect():
        return []
    try:
        from xtquant import xtdata
        end = end_date or datetime.now().strftime('%Y%m%d')
        data = xtdata.get_local_data(
            field_list=['time', 'open', 'high', 'low', 'close', 'volume'],
            stock_list=[qmt_code],
            period='1d',
            start_time=start_date,
            end_time=end,
            count=0
        )
        if isinstance(data, dict) and qmt_code in data:
            df = data[qmt_code]
            if df is not None and not df.empty:
                result = []
                for _, row in df.iterrows():
                    ts = row.get('time', 0)
                    if isinstance(ts, (int, float)) and ts > 1e12:
                        date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                    else:
                        s = str(ts)
                        # YYYYMMDD → YYYY-MM-DD
                        if len(s) == 8 and s.isdigit():
                            date_str = f'{s[:4]}-{s[4:6]}-{s[6:8]}'
                        else:
                            date_str = s[:10]
                    result.append({
                        'date': date_str,
                        'open': float(row.get('open', 0)),
                        'high': float(row.get('high', 0)),
                        'low': float(row.get('low', 0)),
                        'close': float(row.get('close', 0)),
                        'volume': float(row.get('volume', 0)),
                    })
                return result
    except Exception as e:
        logger.warning(f"[QMT] 读取 {qmt_code} 失败: {e}")
    return []


def download_latest(qmt_code: str) -> bool:
    """触发 QMT 下载最新数据"""
    try:
        from xtquant import xtdata
        today = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        xtdata.download_history_data(qmt_code, period='1d', start_time=start, end_time=today)
        return True
    except Exception as e:
        logger.warning(f"[QMT] 下载 {qmt_code} 失败: {e}")
        return False


# ====== 代码映射 ======

def map_code(code: str, data_type: str) -> Optional[str]:
    """将面板代码映射为 QMT 代码"""
    idx_map = {
        'sh000001': '000001.SH', 'sz399006': '399006.SZ',
        'sh000688': '000688.SH', 'sh000300': '000300.SH',
        'sh000016': '000016.SH', 'sh000852': '000852.SH',
        'sh000853': '000853.SH', 'sh000985': '000985.SH',
        'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
    }
    if code in idx_map:
        return idx_map[code]
    if data_type == 'hk' and len(code) == 5:
        return f'{code}.HK'
    if data_type == 'stock' and len(code) == 6:
        return f'{code}.SH' if code.startswith(('6', '9')) else f'{code}.SZ'
    return None


# ====== 写入 SQLite ======

def write_to_db(code: str, period: str, data: list) -> int:
    if not data:
        return 0
    import sqlite3
    conn = sqlite3.connect(str(Path('data') / 'kline.db'))
    cur = conn.cursor()
    rows = 0
    for d in data:
        cur.execute(
            'INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (code, period, d['date'], d['open'], d['high'], d['low'], d['close'], d['volume'])
        )
        rows += 1
    conn.commit()
    conn.close()
    return rows


# ====== 一键更新 ======

def sync_index(code: str, name: str = '') -> int:
    """同步单个指数"""
    qmt_code = map_code(code, 'index')
    if not qmt_code:
        return 0
    if not connect():
        return 0
    download_latest(qmt_code)
    data = get_kline(qmt_code)
    if not data:
        return 0
    # 增量合并：INSERT OR REPLACE 不会删除已有早期数据
    import sqlite3
    conn = sqlite3.connect(str(Path('data') / 'kline.db'))
    conn.commit()
    conn.close()
    n = write_to_db(code, 'daily', data)
    if n:
        logger.info(f"[QMT] 同步 {name or code}: {n}条, 最新{data[-1]['date']}")
    return n


def sync_all_indices() -> dict:
    """同步全部10个指数"""
    indices = [
        ('sh000001', '上证指数'), ('sz399006', '创业板指'),
        ('sh000688', '科创50'), ('sh000300', '沪深300'),
        ('sh000016', '上证50'), ('sh000852', '中证1000'),
        ('sh000853', '中证2000'), ('sh000985', '中证全指'),
        ('HSI', '恒生指数'), ('HSTECH', '恒生科技'),
    ]
    result = {'success': 0, 'failed': 0}
    for code, name in indices:
        n = sync_index(code, name)
        if n > 0:
            result['success'] += 1
        else:
            result['failed'] += 1
    return result


def fast_update_today() -> dict:
    """快速增量更新（仅同步今天及最近3天）"""
    indices = [
        ('sh000001', '000001.SH'), ('sz399006', '399006.SZ'),
        ('sh000688', '000688.SH'), ('sh000300', '000300.SH'),
        ('sh000016', '000016.SH'), ('sh000852', '000852.SH'),
        ('sh000853', '000853.SH'), ('sh000985', '000985.SH'),
        ('HSI', 'HSI.HK'), ('HSTECH', 'HSTECH.HK'),
    ]
    if not connect():
        return {'success': 0, 'failed': len(indices), 'error': '未连接'}

    import sqlite3
    conn = sqlite3.connect(str(Path('data') / 'kline.db'))
    cur = conn.cursor()
    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')

    result = {'success': 0, 'failed': 0}
    for code, qmt_code in indices:
        try:
            download_latest(qmt_code)
            data = get_kline(qmt_code, start_date=start, end_date=today)
            if data:
                for d in data:
                    cur.execute(
                        'INSERT OR REPLACE INTO kline (code, period, date, open, high, low, close, volume) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        (code, 'daily', d['date'], d['open'], d['high'], d['low'], d['close'], d['volume'])
                    )
                result['success'] += 1
            else:
                result['failed'] += 1
        except Exception as e:
            logger.warning(f"[QMT] {code} 更新失败: {e}")
            result['failed'] += 1

    conn.commit()
    conn.close()
    return result


# ====== 状态 ======

def status() -> dict:
    s = socket.socket()
    port_open = s.connect_ex(('127.0.0.1', QMT_PORTS[1])) == 0
    s.close()
    return {
        'xtmini_running': port_open,
        'qmt_connected': _qmt_connected,
        'xtquant_installed': True,
        'process': _qmt_process.pid if _qmt_process else None,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    r = fast_update_today()
    print(f"\n增量同步: 成功{r['success']}, 失败{r['failed']}")

