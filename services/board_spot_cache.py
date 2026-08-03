"""
BoardSpotCache — 板块行情统一缓存单例

替代原先 board_api._spot_cache + lifecycle.board_chg_cache 双缓存，
消除两缓存 TTL 不一致导致的 bug。

职责：
- 单一 TTL（盘中 30s / 盘后 300s）
- 统一失效入口（invalidate / invalidate_all）
- 提供 get_chgs() 作为 board_chg_cache 的只读派生视图
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger('board_spot_cache')

# TTL 常量
_TTL_TRADING = 30.0   # 盘中 30 秒
_TTL_AFTER = 300.0    # 盘后 5 分钟

# 单例
_instance: Optional['BoardSpotCache'] = None
_instance_lock = threading.Lock()


def _tushare_expected_trade_date(now=None) -> str:
    """Return the settled date allowed in this phase, or empty before 17:00."""
    now = now or datetime.now()
    hhmm = now.hour * 100 + now.minute
    try:
        from data.board_api import get_last_trading_date
        if now.weekday() >= 5 or hhmm >= 1700:
            return get_last_trading_date(now.strftime('%Y-%m-%d'))
        if hhmm < 915:
            previous = (now.date() - timedelta(days=1)).strftime('%Y-%m-%d')
            return get_last_trading_date(previous)
    except Exception:
        return ''
    return ''


class BoardSpotCache:
    """板块行情统一缓存单例"""

    def __init__(self):
        self._data: dict[str, dict] = {}      # {board_type: {code: spot_dict}}
        self._ts: dict[str, float] = {}       # {board_type: last_refresh_timestamp}
        self._frozen: bool = False            # frozen 语义：盘后锁定快照
        self._frozen_chgs: Optional[dict] = None  # 冻结时的涨跌幅快照
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'BoardSpotCache':
        global _instance
        if _instance is None:
            with _instance_lock:
                if _instance is None:
                    _instance = cls()
        return _instance

    # ------------------------------------------------------------------
    # TTL
    # ------------------------------------------------------------------

    def _ttl(self) -> float:
        """当前 TTL：盘中 30s，盘后 300s"""
        try:
            from services.board_snapshot import _is_a_share_session
            return _TTL_TRADING if _is_a_share_session() else _TTL_AFTER
        except Exception:
            return _TTL_AFTER

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def get(self, board_type: str, force: bool = False) -> Optional[dict]:
        """获取板块行情。force=True 强制刷新。

        Fallback chain:
        1. TTL 缓存命中 → 直接返回
        2. 午休 → 上午最后快照（禁止外部重拉）
        3. 盘中 → BoardSnapshotCache 快照（东财 push2delay）
        4. 盘后 → Tushare 目标日期结算，失败保留东财收盘候选
        5. 上次缓存兜底
        """
        now = time.time()
        ttl = self._ttl()

        # 手动 frozen → 直接返回缓存，不再调用任何 API
        if self._frozen:
            with self._lock:
                cached = self._data.get(board_type)
            if cached:
                return cached
            logger.warning(
                '[BoardSpotCache] get(%s) called while frozen but no cache available',
                board_type,
            )

        # 1. 检查 TTL 缓存
        if not force:
            with self._lock:
                if board_type in self._data:
                    if (now - self._ts.get(board_type, 0)) < ttl:
                        return self._data[board_type]

        result: Optional[dict] = None
        source = ''

        # 2. 午休只读上午快照；盘中读取实时快照；盘后读取收盘候选。
        is_lunch = False
        is_live = False
        try:
            from services.board_snapshot import (
                _is_lunch_break, _is_a_share_live_session,
                get_snapshot_cache,
            )
            is_lunch = _is_lunch_break()
            is_live = _is_a_share_live_session()
            sc = get_snapshot_cache()
            if is_lunch:
                snap_all = sc.get_all(board_type) or {}
                if snap_all:
                    result = {
                        code: _snap_row_to_spot(row, frozen=True)
                        for code, row in snap_all.items()
                    }
                    source = 'eastmoney_push2delay_frozen'
            elif is_live:
                sc.ensure_snapshot(force=force)
                snap_all = sc.get_all(board_type) or {}
                if snap_all:
                    frozen = sc.is_frozen()
                    result = {
                        code: _snap_row_to_spot(row, frozen)
                        for code, row in snap_all.items()
                    }
                    source = 'eastmoney_push2delay_frozen' if frozen else 'eastmoney_push2delay_live'
                    logger.info(
                        '[BoardSpotCache] %s snapshot 命中: %d 个 channel=%s',
                        board_type, len(result), source,
                    )
            else:
                snap_all = sc.get_all(board_type) or {}
                if snap_all:
                    result = {
                        code: _snap_row_to_spot(row, frozen=False)
                        for code, row in snap_all.items()
                    }
                    source = 'eastmoney_push2delay_close_candidate'
        except Exception as e:
            logger.warning('[BoardSpotCache] snapshot 读取失败(%s): %s', board_type, e)

        # 3. 盘中 snapshot 失败时，使用同一东财适配器补抓。
        if not result and is_live:
            try:
                from data.board_api import _fetch_em_board_spot
                em_result = _fetch_em_board_spot(board_type)
                if em_result:
                    result = em_result
                    source = 'eastmoney_push2delay'
            except Exception as e:
                logger.warning('[BoardSpotCache] 东财 fallback failed (%s): %s', board_type, e)

        # 4. 盘后只有目标交易日的 Tushare 结果才能替换候选。
        if not is_lunch and not is_live:
            expected_trade_date = _tushare_expected_trade_date()
            if expected_trade_date:
                try:
                    from data.board_api import _fetch_tushare_board_spot
                    ts_result = _fetch_tushare_board_spot(
                        board_type,
                        expected_trade_date=expected_trade_date,
                    )
                    if ts_result:
                        result = ts_result
                        source = 'tushare_dc_index_settled'
                except Exception as e:
                    logger.warning('[BoardSpotCache] Tushare fallback failed (%s): %s', board_type, e)

        # 5. 盘后没有候选时，补抓一份东财临时收盘候选。
        if not result and not is_lunch and not is_live:
            try:
                from data.board_api import _fetch_em_board_spot
                em_result = _fetch_em_board_spot(board_type)
                if em_result:
                    result = em_result
                    source = 'eastmoney_push2delay_close_candidate'
            except Exception as e:
                logger.warning('[BoardSpotCache] 东财 close candidate failed (%s): %s', board_type, e)

        # 6. 写缓存
        if result:
            with self._lock:
                self._data[board_type] = result
                self._ts[board_type] = time.time()
            logger.info(
                '[BoardSpotCache] %s 同步完成: %d 个 channel=%s',
                board_type, len(result), source,
            )
            return result

        # 7. 上次缓存兜底
        with self._lock:
            return self._data.get(board_type)

    # ------------------------------------------------------------------
    # Frozen 语义
    # ------------------------------------------------------------------

    def set_frozen(self) -> None:
        """标记当前缓存为冻结状态。

        冻结后：
        - get_chgs() 返回锁定快照，不再 invalidate
        - 不再调用 Tushare/东财 API
        - invalidate() 被忽略（保护快照）
        """
        with self._lock:
            if not self._frozen:
                self._frozen = True
                # 快照当前涨跌幅
                self._frozen_chgs = self._snapshot_chgs()
                logger.info(
                    '[BoardSpotCache] frozen ON — snapshot %d chgs',
                    len(self._frozen_chgs),
                )

    def is_frozen(self) -> bool:
        """检查缓存是否已冻结。"""
        return self._frozen

    def _snapshot_chgs(self) -> dict:
        """内部：从当前 _data 派生涨跌幅快照（与 get_chgs 逻辑一致）。"""
        result: dict[str, float] = {}
        for board_type in ('industry', 'concept'):
            spot = self._data.get(board_type, {})
            for code, data in spot.items():
                if data and '涨跌幅' in data:
                    chg = round(float(data['涨跌幅'] or 0), 2)
                    result[f'{board_type}:{code}'] = chg
                    if board_type == 'industry':
                        result[code] = chg
                    elif code not in result:
                        result[code] = chg
        return result

    # ------------------------------------------------------------------
    # 失效
    # ------------------------------------------------------------------

    def invalidate(self, board_type: Optional[str] = None) -> None:
        """清除指定板块类型的缓存。board_type=None 清除全部。

        frozen 状态下忽略 invalidate，保护快照。
        """
        if self._frozen:
            logger.debug('[BoardSpotCache] invalidate ignored (frozen)')
            return
        with self._lock:
            if board_type:
                self._data.pop(board_type, None)
                self._ts.pop(board_type, None)
            else:
                self._data.clear()
                self._ts.clear()
        logger.info('[BoardSpotCache] invalidated: %s', board_type or 'all')

    def invalidate_all(self) -> None:
        """清除全部缓存。"""
        self.invalidate()

    # ------------------------------------------------------------------
    # 盘后 Tushare 刷新
    # ------------------------------------------------------------------

    def refresh_from_tushare_post_market(self, board_type: str) -> bool:
        """盘后 Tushare 全量刷新。返回是否成功。

        frozen 状态下拒绝刷新，保护快照。
        """
        if self._frozen:
            logger.debug('[BoardSpotCache] refresh_from_tushare_post_market ignored (frozen)')
            return False
        try:
            from data.board_api import _fetch_tushare_board_spot
            result = _fetch_tushare_board_spot(board_type)
            if result:
                with self._lock:
                    self._data[board_type] = result
                    self._ts[board_type] = time.time()
                logger.info('[BoardSpotCache] Tushare %s → %d items', board_type, len(result))
                return True
        except Exception as e:
            logger.warning('[BoardSpotCache] Tushare refresh failed (%s): %s', board_type, e)
        return False

    # ------------------------------------------------------------------
    # 涨跌幅聚合（替代 lifecycle.board_chg_cache）
    # ------------------------------------------------------------------

    def get_chgs(self) -> dict:
        """返回板块涨跌幅 dict，兼容 lifecycle.board_chg_cache 三键结构。

        结构: {industry:CODE:chg, CODE:chg, concept:CODE:chg}
        industry 裸 key 优先于 concept（与 _reload_board_changes 一致）。

        frozen 状态下返回锁定快照，不再 invalidate。
        """
        # frozen → 直接返回锁定快照
        if self._frozen and self._frozen_chgs is not None:
            return self._frozen_chgs.copy()

        result: dict[str, float] = {}

        for board_type in ('industry', 'concept'):
            with self._lock:
                spot = self._data.get(board_type, {})
            for code, data in spot.items():
                if data and '涨跌幅' in data:
                    chg = round(float(data['涨跌幅'] or 0), 2)
                    result[f'{board_type}:{code}'] = chg
                    # bare key: industry wins over concept
                    if board_type == 'industry':
                        result[code] = chg
                    elif code not in result:
                        result[code] = chg

        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """返回缓存统计信息。"""
        with self._lock:
            return {
                'boards': list(self._data.keys()),
                'counts': {k: len(v) for k, v in self._data.items()},
                'timestamps': {
                    k: time.strftime('%H:%M:%S', time.localtime(v))
                    for k, v in self._ts.items()
                },
                'ttl': self._ttl(),
                'frozen': self._frozen,
            }

    # ------------------------------------------------------------------
    # 向后兼容别名
    # ------------------------------------------------------------------

    def refresh_from_tushare(self, board_type: str) -> bool:
        """向后兼容：调用 refresh_from_tushare_post_market。"""
        return self.refresh_from_tushare_post_market(board_type)


# ======================================================================
# 模块级辅助
# ======================================================================

def _snap_row_to_spot(row: dict, frozen: bool) -> dict:
    """将 snapshot 行转成 spot 缓存格式（复用 board_api 的实现）。"""
    try:
        from data.board_api import _snap_row_to_spot as _impl
        return _impl(row, frozen)
    except Exception:
        # fallback: 直接转换
        channel = 'eastmoney_push2delay_frozen' if frozen else 'eastmoney_push2delay_live'
        return {
            '名称': str(row.get('name', '') or ''),
            '涨跌幅': float(row.get('pct', 0) or 0),
            '最新价': float(row.get('close', 0) or 0),
            'trade_date': str(row.get('trade_date', '') or ''),
            'channel': channel,
        }


# ======================================================================
# 模块级便捷入口
# ======================================================================

def get_board_spot_cache() -> BoardSpotCache:
    """获取 BoardSpotCache 单例（快捷方式）。"""
    return BoardSpotCache.get_instance()
