"""
QMT缓存服务
后端高速缓存QMT数据，前端批量刷新
使用线程模型（Flask同步架构兼容）
"""

import time
import logging
import threading
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger('qmt_cache')


@dataclass
class PriceData:
    """价格数据结构"""
    code: str
    price: float
    change_pct: float
    volume: int
    timestamp: int


class QMTCacheService:
    """QMT缓存服务（线程模型，兼容Flask同步架构）"""

    def __init__(self, refresh_interval: int = 3):
        self.cache: Dict[str, PriceData] = {}
        self.refresh_interval = refresh_interval  # 秒
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._watched_codes = set()
        self._lock = threading.Lock()

    def start(self):
        """启动缓存服务（后台线程）"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._cache_loop, daemon=True)
        self._thread.start()
        logger.info(f"QMT缓存服务已启动，刷新间隔: {self.refresh_interval}秒")

    def stop(self):
        """停止缓存服务"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("QMT缓存服务已停止")

    def _cache_loop(self):
        """缓存刷新循环（后台线程）"""
        while not self._stop_event.is_set():
            try:
                self._refresh_cache()
            except Exception as e:
                logger.error(f"缓存刷新失败: {e}")
            self._stop_event.wait(self.refresh_interval)

    def _refresh_cache(self):
        """刷新缓存数据 — 从QMT获取真实数据"""
        if not self._watched_codes:
            return

        codes = list(self._watched_codes)
        try:
            from data.qmt_client import get_qmt_client
            from core.lifecycle import is_qmt_available

            if not is_qmt_available():
                logger.debug("[QMT缓存] QMT不可用，跳过刷新")
                return

            client = get_qmt_client()
            # 使用get_constituents_batch获取日线收盘数据
            raw = client.get_constituents_batch(codes)
            if not raw:
                # 回退：尝试实时数据
                raw = client.get_constituents_live(codes)

            now_ms = int(time.time() * 1000)
            with self._lock:
                for code, info in raw.items():
                    self.cache[code] = PriceData(
                        code=code,
                        price=info.get('close', 0),
                        change_pct=info.get('change_pct', 0),
                        volume=info.get('volume', 0),
                        timestamp=now_ms
                    )

            logger.debug(f"[QMT缓存] 已刷新 {len(raw)}/{len(codes)} 个标的")
        except ImportError:
            logger.warning("[QMT缓存] qmt_client不可用")
        except Exception as e:
            logger.warning(f"[QMT缓存] 刷新异常: {e}")

    def watch_codes(self, codes: List[str]):
        """添加关注的标的"""
        with self._lock:
            self._watched_codes.update(codes)
        logger.info(f"添加关注: {codes}")

    def unwatch_codes(self, codes: List[str]):
        """移除关注的标的"""
        with self._lock:
            for code in codes:
                self._watched_codes.discard(code)
                self.cache.pop(code, None)
        logger.info(f"移除关注: {codes}")

    def get_cached_prices(self, codes: List[str]) -> Dict[str, dict]:
        """从缓存获取价格"""
        result = {}
        with self._lock:
            for code in codes:
                data = self.cache.get(code)
                if data:
                    # 兼容 PriceData 对象和 dict 两种格式
                    if isinstance(data, dict):
                        result[code] = {
                            'price': round(data.get('price', 0), 2),
                            'changePct': round(data.get('change_pct', data.get('changePct', 0)), 2),
                            'volume': data.get('volume', 0),
                            'timestamp': data.get('timestamp', 0)
                        }
                    else:
                        result[code] = {
                            'price': round(data.price, 2),
                            'changePct': round(data.change_pct, 2),
                            'volume': data.volume,
                            'timestamp': data.timestamp
                        }
                else:
                    result[code] = None
        return result

    def get_all_cached(self) -> Dict[str, dict]:
        """获取所有缓存数据"""
        with self._lock:
            result = {}
            for code, data in self.cache.items():
                if isinstance(data, dict):
                    result[code] = {
                        'price': round(data.get('price', 0), 2),
                        'changePct': round(data.get('change_pct', data.get('changePct', 0)), 2),
                        'volume': data.get('volume', 0),
                        'timestamp': data.get('timestamp', 0)
                    }
                else:
                    result[code] = {
                        'price': round(data.price, 2),
                        'changePct': round(data.change_pct, 2),
                        'volume': data.volume,
                        'timestamp': data.timestamp
                    }
            return result

    def is_running(self) -> bool:
        """服务是否运行中"""
        return self._running

    def get_watched_count(self) -> int:
        """获取关注标的数量"""
        with self._lock:
            return len(self._watched_codes)

    def get_watched_codes(self) -> list:
        """获取关注标的列表"""
        with self._lock:
            return list(self._watched_codes)


# 全局缓存服务实例
qmt_cache_service = QMTCacheService(refresh_interval=3)
