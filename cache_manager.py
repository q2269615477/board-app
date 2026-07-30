"""
cache_manager.py - 综合缓存管理机制
功能：进程健康/缓存监控/自动清理/预热/异常恢复/监控告警
"""
import os
import sys
import time
import json
import signal
import socket
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Any

logger = logging.getLogger('cache_mgr')

BASE_DIR = Path(__file__).parent
PID_FILE = BASE_DIR / '.app.pid'
CACHE_META_FILE = BASE_DIR / 'data' / 'cache_meta.json'

# ============================================================
# 1. 进程健康检查
# ============================================================

class ProcessManager:
    """进程生命周期管理"""

    @staticmethod
    def write_pid():
        pid = os.getpid()
        PID_FILE.write_text(str(pid), encoding='utf-8')
        logger.info(f"[进程] PID={pid} 已写入 {PID_FILE}")

    @staticmethod
    def read_pid() -> Optional[int]:
        if PID_FILE.exists():
            try:
                return int(PID_FILE.read_text(encoding='utf-8').strip())
            except (ValueError, OSError):
                return None
        return None

    @staticmethod
    def is_process_alive(pid: int) -> bool:
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    @staticmethod
    def kill_orphaned():
        """清理孤儿进程：杀死旧实例"""
        old_pid = ProcessManager.read_pid()
        if old_pid and old_pid != os.getpid():
            if ProcessManager.is_process_alive(old_pid):
                logger.warning(f"[进程] 发现旧实例 PID={old_pid}，正在终止...")
                try:
                    if sys.platform == 'win32':
                        import ctypes
                        kernel32 = ctypes.windll.kernel32
                        handle = kernel32.OpenProcess(1, False, old_pid)
                        if handle:
                            kernel32.TerminateProcess(handle, 1)
                            kernel32.CloseHandle(handle)
                            logger.info(f"[进程] 旧实例 PID={old_pid} 已终止")
                    else:
                        os.kill(old_pid, signal.SIGTERM)
                        time.sleep(1)
                        if ProcessManager.is_process_alive(old_pid):
                            os.kill(old_pid, signal.SIGKILL)
                        logger.info(f"[进程] 旧实例 PID={old_pid} 已终止")
                except Exception as e:
                    logger.error(f"[进程] 终止旧实例失败: {e}")
            else:
                logger.info(f"[进程] 旧实例 PID={old_pid} 已不存在")
        PID_FILE.unlink(missing_ok=True)

    @staticmethod
    def register_cleanup():
        """注册优雅退出信号处理"""
        def _cleanup(signum, frame):
            logger.info(f"[进程] 收到信号 {signum}，执行清理...")
            PID_FILE.unlink(missing_ok=True)
            # 清理端口文件
            port_file = BASE_DIR / '.port'
            port_file.unlink(missing_ok=True)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)


# ============================================================
# 2. 缓存层（多级缓存）
# ============================================================

class CacheEntry:
    def __init__(self, data: Any, ttl: int = 300):
        self.data = data
        self.created = time.time()
        self.ttl = ttl
        self.access_count = 0
        self.last_access = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.created > self.ttl

    @property
    def age(self) -> float:
        return time.time() - self.created

    def access(self):
        self.access_count += 1
        self.last_access = time.time()


class CacheManager:
    """
    多级缓存管理器
    层级1: Python 字典（内存）— 最快
    层级2: 元数据文件（持久化）— 进程重启后恢复
    """

    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._default_ttl = default_ttl
        self._stats = {
            'hits': 0, 'misses': 0, 'evictions': 0,
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._max_entries = 200
        # 定期清理线程
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    # ---- 核心操作 ----

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._stats['misses'] += 1
                return None
            if entry.expired:
                del self._store[key]
                self._stats['evictions'] += 1
                self._stats['misses'] += 1
                return None
            entry.access()
            self._stats['hits'] += 1
            return entry.data

    def set(self, key: str, data: Any, ttl: Optional[int] = None):
        with self._lock:
            # 数据完整性校验：拒绝空数据（显示标记情况除外）
            if self._is_stale_empty(data, key):
                logger.warning(f"[缓存] 拒绝缓存空数据: {key}")
                return
            self._store[key] = CacheEntry(data, ttl or self._default_ttl)
            # 淘汰策略：超过上限时淘汰最久未访问的
            if len(self._store) > self._max_entries:
                oldest_key = min(self._store.keys(),
                                 key=lambda k: self._store[k].last_access)
                del self._store[oldest_key]
                self._stats['evictions'] += 1

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            count = len(self._store)
            self._store.clear()
            logger.info(f"[缓存] 已清除 {count} 条缓存")
            return count

    def clear_by_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    # ---- 数据完整性校验 ----

    def _is_stale_empty(self, data: Any, key: str) -> bool:
        """判断是否为"脏空数据"（有数据的项突然返回空）"""
        if data is None:
            return True
        if isinstance(data, (list, tuple)) and len(data) == 0:
            # 如果之前有数据，现在突然空，说明异常
            with self._lock:
                existing = self._store.get(key)
                if existing is not None and len(existing.data) > 0:
                    logger.warning(f"[缓存] 检测到脏空数据: {key} (之前有{len(existing.data)}条)")
                    return False  # 允许覆盖，让后续请求真正去拉数据
                return False  # 首次无数据，允许缓存
        return False

    # ---- 定期清理 ----

    def _cleanup_loop(self):
        """每60秒检查并清除过期缓存"""
        while True:
            time.sleep(60)
            self._evict_expired()

    def _evict_expired(self):
        with self._lock:
            now = time.time()
            expired = [k for k, v in self._store.items() if v.expired]
            for k in expired:
                del self._store[k]
            if expired:
                logger.info(f"[缓存] 清理 {len(expired)} 条过期缓存")
                self._stats['evictions'] += len(expired)

    # ---- 状态查询 ----

    def status(self) -> dict:
        with self._lock:
            now = time.time()
            entries = []
            for k, v in sorted(self._store.items()):
                entries.append({
                    'key': k,
                    'age_sec': round(v.age, 1),
                    'ttl_sec': v.ttl,
                    'expired': v.expired,
                    'access_count': v.access_count,
                    'data_size': len(v.data) if isinstance(v.data, (list, tuple)) else '?',
                })
            return {
                'stats': self._stats,
                'total_entries': len(self._store),
                'entries': entries,
            }

    def health_check(self) -> dict:
        """缓存健康检查"""
        with self._lock:
            total = len(self._store)
            expired = sum(1 for v in self._store.values() if v.expired)
            empty = sum(1 for v in self._store.values()
                        if isinstance(v.data, (list, tuple)) and len(v.data) == 0)
            hit_rate = (self._stats['hits'] / (self._stats['hits'] + self._stats['misses'] or 1)) * 100
            return {
                'status': 'healthy' if empty < total * 0.3 else 'warning',
                'total_entries': total,
                'expired_entries': expired,
                'empty_entries': empty,
                'hit_rate': round(hit_rate, 1),
                'hits': self._stats['hits'],
                'misses': self._stats['misses'],
                'evictions': self._stats['evictions'],
                'memory_estimate_mb': round(total * 0.5, 2),  # 粗略估计
            }


# ============================================================
# 3. 全局实例
# ============================================================

g_cache = CacheManager(default_ttl=300)


# ============================================================
# 4. 缓存预热
# ============================================================

def warmup_cache(code: str, data_type: str, loader_func, period: str = 'daily'):
    """预热单个数据项到缓存"""
    cache_key = f'{data_type}:{code}:{period}'
    try:
        df = loader_func(code, period)
        if df is not None and not df.empty:
            from app import _format_kline
            data = _format_kline(df)
            g_cache.set(cache_key, data, ttl=600)  # 预热项TTL加倍
            logger.info(f"[预热] 缓存 {cache_key}: {len(data)} 条")
            return len(data)
    except Exception as e:
        logger.warning(f"[预热] 失败 {cache_key}: {e}")
    return 0


# ============================================================
# 5. 监控告警
# ============================================================

class Monitor:
    """系统健康监控器"""

    def __init__(self):
        self._alerts: list[dict] = []
        self._max_alerts = 100

    def add_alert(self, level: str, source: str, message: str):
        alert = {
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': level,
            'source': source,
            'message': message,
        }
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts.pop(0)
        log_fn = logger.warning if level == 'WARN' else logger.error
        log_fn(f"[告警] [{source}] {message}")

    def get_alerts(self, since: Optional[str] = None) -> list:
        if since:
            return [a for a in self._alerts if a['time'] >= since]
        return self._alerts

    def health(self) -> dict:
        return {
            'status': 'ok' if len([a for a in self._alerts if a['level'] == 'ERROR'][-5:]) < 5 else 'degraded',
            'total_alerts': len(self._alerts),
            'recent_errors': len([a for a in self._alerts[-20:] if a['level'] == 'ERROR']),
        }


g_monitor = Monitor()
