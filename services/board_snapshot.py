"""
板块实时快照（盘中东财 push2delay）；盘后不使用，由 board_api 切 Tushare dc_index。

职责：
- 在 A 股交易日盘中（09:15–15:05）从东财 push2delay 拉取全量行业/概念板块 OHLCV 快照
- 盘中实时模式：每次 ensure_snapshot 都拉取最新东财价格，不再冻结
- 15:05 之后不再 capture，下游由 board_api 走 Tushare dc_index
- 非交易日不 capture
- 进程内最小节流间隔（默认 5s），force=True 立即刷新

使用方式：
    from services.board_snapshot import get_snapshot_cache
    cache = get_snapshot_cache()
    cache.ensure_snapshot()           # 自动判断时段 + 节流
    cache.ensure_snapshot(force=True) # 手动刷新（盘中有效，无视节流）
    row = cache.get_board_today('concept', 'BK0800')
    all_rows = cache.get_all('industry')
    print(cache.stats())
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger('board_snapshot')

# A 股交易时段：周一至五 09:15–15:05
_SESSION_START = 915
_SESSION_END = 1505

# 进程内最小 capture 节流间隔（秒），避免 30s 守护 + 用户点击同时打东财
_MIN_CAPTURE_INTERVAL = 5

# 东财 push2delay clist API
_EM_BASE_URLS = (
    'https://push2delay.eastmoney.com/api/qt/clist/get',
    'https://push2.eastmoney.com/api/qt/clist/get',
)
_EM_HEADERS = {'User-Agent': 'Mozilla/5.0'}

# 板块类型 -> 东财 fs 参数
_FS_MAP = {
    'industry': 'm:90+t:2',
    'concept': 'm:90+t:3',
}

# 板块类型 -> 最大页数
_MAX_PAGES = {
    'industry': 5,
    'concept': 6,
}

# 请求每页条数
_PAGE_SIZE = 100

# 请求超时（秒）
_REQUEST_TIMEOUT = 8

# 数值字段：None / 非数字均转为 0
_NUMERIC_FIELDS = ('open', 'high', 'low', 'close', 'pre_close', 'pct', 'volume', 'amount')


def _safe_float(val) -> float:
    """将任意值转为 float，None / 非数字 → 0.0"""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(val) -> str:
    if val is None:
        return ''
    return str(val)


def _now_hhmm() -> int:
    """当前时间转为 HHMM 整数"""
    now = datetime.now()
    return now.hour * 100 + now.minute


def _is_a_share_session() -> bool:
    """A 股交易时段（含午休）：周一至五 09:15–15:05。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hhmm = _now_hhmm()
    return _SESSION_START <= hhmm <= _SESSION_END


def _today_str() -> str:
    return datetime.now().strftime('%Y%m%d')


class BoardSnapshotCache:
    """板块快照内存缓存（单例）。

    内存结构：
        {date_str: {
            'industry': {code: row_dict, ...},
            'concept': {code: row_dict, ...},
            'captured_at': float (time.time()),
            'frozen': bool,
        }}

    row_dict 字段：
        code, name, open, high, low, close, pre_close, pct,
        volume, amount, trade_date, channel, ts
    """

    _instance: Optional['BoardSnapshotCache'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._data: Dict[str, dict] = {}
        self._last_capture_ts: float = 0.0
        self._initialized = True

    def _ensure_today(self, date_str: str):
        """确保今日 dict 存在；跨日自动清空旧日 dict"""
        if date_str not in self._data:
            self._data.clear()
            self._data[date_str] = {
                'industry': {},
                'concept': {},
                'captured_at': 0.0,
                'frozen': False,
            }

    def capture_all(self, board_type: str) -> int:
        """从东财 push2delay 拉取全量板块数据。

        Args:
            board_type: 'industry' 或 'concept'

        Returns:
            成功拉取的板块数量（0 表示失败）
        """
        import requests

        # 强制直连，避免走 7688 VPN 代理
        try:
            from core.env_bootstrap import force_direct_network
            force_direct_network()
        except Exception:
            import os
            for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                      'http_proxy', 'https_proxy', 'all_proxy'):
                os.environ.pop(k, None)
            os.environ['NO_PROXY'] = '*'
            os.environ['no_proxy'] = '*'

        if board_type not in _FS_MAP:
            logger.warning('[board_snapshot] 未知 board_type=%s', board_type)
            return 0

        fs_val = _FS_MAP[board_type]
        max_pages = _MAX_PAGES.get(board_type, 6)
        today = _today_str()
        result: Dict[str, dict] = {}
        trade_date = today

        session = requests.Session()
        session.proxies = {'http': None, 'https': None}
        session.trust_env = False

        for base_url in _EM_BASE_URLS:
            result = {}
            try:
                for page in range(1, max_pages + 1):
                    url = (
                        f'{base_url}?pn={page}&pz={_PAGE_SIZE}&po=1&np=1'
                        f'&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2'
                        f'&fid=f3&fs={fs_val}'
                        f'&fields=f12,f14,f2,f3,f4,f5,f6,f7,f8,f15,f16,f17,f18'
                    )
                    r = session.get(url, headers=_EM_HEADERS, timeout=_REQUEST_TIMEOUT)
                    if r.status_code != 200 or not r.text:
                        break
                    payload = r.json()
                    data = payload.get('data') if isinstance(payload, dict) else None
                    if not data:
                        break
                    diff = data.get('diff') or []
                    if not diff:
                        break
                    for item in diff:
                        code = _safe_str(item.get('f12', ''))
                        if not code:
                            continue
                        result[code] = {
                            'code': code,
                            'name': _safe_str(item.get('f14', '')),
                            'open': _safe_float(item.get('f17')),
                            'high': _safe_float(item.get('f15')),
                            'low': _safe_float(item.get('f16')),
                            'close': _safe_float(item.get('f2')),
                            'pre_close': _safe_float(item.get('f18')),
                            'pct': _safe_float(item.get('f3')),
                            'volume': _safe_float(item.get('f5')),
                            'amount': _safe_float(item.get('f6')),
                            'trade_date': today,
                            'channel': 'eastmoney_push2delay',
                            'ts': time.time(),
                        }
                if result:
                    logger.info(
                        '[board_snapshot] %s 拉取 %d 条 via %s',
                        board_type, len(result), base_url
                    )
                    break
            except Exception as e:
                logger.warning('[board_snapshot] %s via %s 失败: %s', board_type, base_url, e)
                continue

        session.close()

        # 写入今日 dict
        if result:
            self._ensure_today(today)
            self._data[today][board_type] = result
            self._data[today]['captured_at'] = time.time()

        return len(result)

    def ensure_snapshot(self, force: bool = False) -> bool:
        """确保今日快照已生成（实时模式）。

        策略：
        - 非交易日 / 15:05 后：不 capture，返回当前是否有数据
        - 盘中 09:15–15:05：每次调用都 capture（push2delay），写入当日 dict
        - 节流：非 force 且距上次 capture < 5s，跳过重拉、返回当前有数据
        - force=True：无视节流，立即拉一次
        - 捕获失败：保留上次数据，不抛异常

        Args:
            force: 强制重新 capture（无视节流）

        Returns:
            当前是否有数据
        """
        today = _today_str()

        # 非交易日 / 15:05 后：不 capture，返回当前是否有数据
        if not _is_a_share_session():
            self._ensure_today(today)
            return bool(self._data[today].get('industry') or self._data[today].get('concept'))

        # 盘中
        self._ensure_today(today)
        today_data = self._data[today]

        # 节流检查：非 force 且距上次 capture < 阈值，跳过
        now = time.time()
        if not force and (now - self._last_capture_ts) < _MIN_CAPTURE_INTERVAL:
            return bool(today_data.get('industry') or today_data.get('concept'))

        # capture（实时模式，不 freeze）
        try:
            n_ind = self.capture_all('industry')
            n_con = self.capture_all('concept')
            self._last_capture_ts = time.time()
            return n_ind > 0 or n_con > 0
        except Exception as e:
            logger.warning('[board_snapshot] capture 异常，保留上次数据: %s', e)
            return bool(today_data.get('industry') or today_data.get('concept'))

    def get_board_today(self, board_type: str, code: str) -> Optional[dict]:
        """获取今日某板块的快照数据。

        Args:
            board_type: 'industry' 或 'concept'
            code: 板块代码（如 'BK0800'）

        Returns:
            板块数据 dict，不存在返回 None
        """
        today = _today_str()
        if today not in self._data:
            return None
        return self._data[today].get(board_type, {}).get(code)

    def get_all(self, board_type: str) -> dict:
        """获取今日某类板块的全部快照。

        Args:
            board_type: 'industry' 或 'concept'

        Returns:
            {code: row_dict, ...}，无数据返回空 dict
        """
        today = _today_str()
        if today not in self._data:
            return {}
        return dict(self._data[today].get(board_type, {}))

    def is_frozen(self) -> bool:
        """始终返回 False（实时模式，兼容旧接口）"""
        return False

    def stats(self) -> dict:
        """返回统计信息"""
        today = _today_str()
        if today not in self._data:
            return {
                'date': None,
                'captured_count_industry': 0,
                'captured_count_concept': 0,
                'frozen': False,
                'captured_at': None,
                'mode': 'off',
            }
        d = self._data[today]
        mode = 'live' if _is_a_share_session() else 'off'
        return {
            'date': today,
            'captured_count_industry': len(d.get('industry', {})),
            'captured_count_concept': len(d.get('concept', {})),
            'frozen': False,
            'captured_at': d.get('captured_at'),
            'mode': mode,
        }

    def get_date(self) -> Optional[str]:
        """今日 dict 的交易日期（YYYYMMDD），snapshot 为空返回 None"""
        today = _today_str()
        if today not in self._data:
            return None
        d = self._data[today]
        if d.get('industry') or d.get('concept'):
            return today
        return None


def get_snapshot_cache() -> BoardSnapshotCache:
    """返回 BoardSnapshotCache 单例"""
    return BoardSnapshotCache()
