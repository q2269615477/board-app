"""
core/cache.py — 统一缓存管理器
替代旧有的 _board_chg_cache、_MARKET_CAP_CACHE、_STOCK_CHG_CACHE 等7个手写缓存
"""
import time
import json
import os
import sys
import signal
import threading
import logging
import tempfile
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

logger = logging.getLogger('cache')

from .config import CACHE_DEFAULT_TTL, CACHE_CLEAN_INTERVAL, CACHE_MAX_ITEMS


class CacheEntry:
    def __init__(self, data: Any, ttl: int = CACHE_DEFAULT_TTL):
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
    """统一线程安全内存缓存"""

    def __init__(self, default_ttl: int = CACHE_DEFAULT_TTL, max_items: int = CACHE_MAX_ITEMS):
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._default_ttl = default_ttl
        self._max_items = max_items
        self._stats = {
            'hits': 0, 'misses': 0, 'evictions': 0,
            'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        # 启动清理线程
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

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
            if self._is_stale_empty(data, key):
                logger.warning(f"[缓存] 拒绝缓存空数据: {key}")
                return
            self._store[key] = CacheEntry(data, ttl or self._default_ttl)
            if len(self._store) > self._max_items:
                oldest = min(self._store.keys(), key=lambda k: self._store[k].last_access)
                del self._store[oldest]
                self._stats['evictions'] += 1

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def clear_by_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def _is_stale_empty(self, data: Any, key: str) -> bool:
        if data is None:
            return True
        if isinstance(data, (list, tuple)) and len(data) == 0:
            existing = self._store.get(key)
            if existing is not None and isinstance(existing.data, (list, tuple)) and len(existing.data) > 0:
                # 已有非空数据，拒绝用空数据覆盖（防止瞬时异常导致缓存被清空）
                return True
            return False  # 首次无数据，允许缓存空列表
        return False

    def _cleanup_loop(self):
        while True:
            time.sleep(CACHE_CLEAN_INTERVAL)
            self._evict_expired()

    def _evict_expired(self):
        with self._lock:
            expired = [k for k, v in self._store.items() if v.expired]
            for k in expired:
                del self._store[k]
            if expired:
                self._stats['evictions'] += len(expired)

    def status(self) -> dict:
        with self._lock:
            entries = []
            for k, v in sorted(self._store.items()):
                entries.append({
                    'key': k, 'age_sec': round(v.age, 1),
                    'ttl_sec': v.ttl, 'expired': v.expired,
                    'access_count': v.access_count,
                    'data_size': len(v.data) if isinstance(v.data, (list, tuple)) else '?',
                })
            return {'stats': self._stats, 'total_entries': len(self._store), 'entries': entries}


# 全局单例
_cache_instance: Optional[CacheManager] = None


def get_cache() -> CacheManager:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheManager()
    return _cache_instance


# ---------- 进程管理（从原来 cache_manager.py 迁移） ----------

_pid_file = Path(tempfile.gettempdir()) / 'board-app.pid'


def write_pid():
    pid = os.getpid()
    _pid_file.write_text(str(pid), encoding='utf-8')


def read_pid() -> Optional[int]:
    if _pid_file.exists():
        try:
            return int(_pid_file.read_text(encoding='utf-8').strip())
        except (ValueError, OSError):
            return None
    return None


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


def kill_orphaned():
    old = read_pid()
    if old and old != os.getpid():
        if is_process_alive(old):
            try:
                if sys.platform == 'win32':
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(1, False, old)
                    if handle:
                        kernel32.TerminateProcess(handle, 1)
                        kernel32.CloseHandle(handle)
                else:
                    os.kill(old, signal.SIGTERM)
                    time.sleep(1)
                    if is_process_alive(old):
                        os.kill(old, signal.SIGKILL)
            except Exception:
                pass
    _pid_file.unlink(missing_ok=True)


def register_cleanup():
    def handler(signum, frame):
        _pid_file.unlink(missing_ok=True)
        port_file = _pid_file.parent / '.port'
        port_file.unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
