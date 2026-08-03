"""数据更新调度门面。

真实时间循环位于 data_update_manager；本模块只提供状态查询和手动触发，
且所有手动入口必须复用生产数据源，不能另建一套午休/盘后抓取链路。
"""
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum

from core.config import UPDATE_SCHEDULE_TIMES, PREWARM_TARGETS
from services.noon_cache_manager import NoonCacheManager

logger = logging.getLogger('data_scheduler')


class UpdateType(Enum):
    """更新类型枚举"""
    MORNING_PREWARM = 'morning_prewarm'
    NOON = 'noon'
    AFTERNOON_SWITCH = 'afternoon_switch'
    DAILY_CLOSE = 'daily_close'


# 全局实例
noon_cache_manager = NoonCacheManager()


def update_noon_data() -> bool:
    """把统一行情缓存中的上午收盘快照保存为午休快照。"""
    logger.info("[午休更新] 开始更新午休数据...")

    try:
        all_data = {}

        # 顶部栏：国内标的由 QMT 18080，境外标的由既有 HTTP 聚合器。
        try:
            from services.nav_spot_service import fetch_nav_spots
            nav = fetch_nav_spots(force=True)
            index_data = nav.get('data') or {}
            all_data.update(index_data)
            logger.info(f"[午休更新] 顶部行情: {len(index_data)} 个")
        except Exception as e:
            logger.warning(f"[午休更新] 顶部统一行情读取失败: {e}")

        # 板块：BoardSpotCache 在午休阶段冻结上午最后一份 push2delay 快照。
        # 禁止午休时调用 Tushare；dc_daily 只用于 17:00 后正式结算。
        try:
            from services.board_spot_cache import get_board_spot_cache
            cache = get_board_spot_cache()
            board_data = {}
            for board_type in ('industry', 'concept'):
                frame = cache.get(board_type)
                if frame is None:
                    continue
                if isinstance(frame, dict):
                    rows = frame.items()
                elif hasattr(frame, 'empty') and not frame.empty:
                    rows = (
                        (
                            str(row.get('板块代码') or row.get('code') or ''),
                            row,
                        )
                        for _, row in frame.iterrows()
                    )
                else:
                    continue
                for frame_code, row in rows:
                    code = str(
                        row.get('板块代码') or row.get('code') or frame_code or ''
                    )
                    if not code:
                        continue
                    change_pct = row.get('涨跌幅')
                    if change_pct is None:
                        change_pct = row.get('change_pct')
                    board_data[code] = {
                        'price': row.get('最新价') or row.get('price'),
                        'change_pct': change_pct,
                        'changePct': change_pct,
                        'source': 'eastmoney_push2delay_frozen',
                    }
            all_data.update(board_data)
            logger.info(f"[午休更新] 冻结板块快照: {len(board_data)} 个")
        except Exception as e:
            logger.warning(f"[午休更新] 板块统一缓存读取失败: {e}")

        if all_data:
            success = noon_cache_manager.save_noon_data(all_data)
            if success:
                logger.info(f"[午休更新] 成功更新 {len(all_data)} 个标的数据")
                return True
        
        return False
        
    except Exception as e:
        logger.error(f"[午休更新] 更新失败: {e}")
        return False


def update_daily_close_data() -> bool:
    """
    更新盘后数据（正式数据）
    - 15:30 后国内指数/个股 QMT 日线
    - 17:00 后东财板块 Tushare 正式日线
    - 写入主存储（SQLite/CSV）
    
    Returns:
        bool: 更新成功返回 True
    """
    logger.info("[盘后更新] 开始更新盘后数据...")
    
    try:
        # 使用现有的 update_all_today 函数
        from data_update_manager import update_all_today
        result = update_all_today()
        ready = bool(result.get('completion_ready'))
        logger.info("[盘后更新] 完成状态: %s", ready)
        return ready
        
    except Exception as e:
        logger.error(f"[盘后更新] 更新失败: {e}")
        return False


def afternoon_switch() -> bool:
    """
    下午开盘切换
    - 清空午休缓存
    - 切换到实时数据源
    
    Returns:
        bool: 切换成功返回 True
    """
    logger.info("[下午切换] 13:00 切换到实时数据源...")
    
    try:
        # 清空午休缓存
        noon_cache_manager.clear_noon_cache()
        logger.info("[下午切换] 午休缓存已清空，切换到实时数据")
        return True
        
    except Exception as e:
        logger.error(f"[下午切换] 切换失败: {e}")
        return False


def _get_all_board_codes() -> list:
    """获取所有东财板块代码"""
    codes = []
    try:
        import json
        from core.config import BOARD_CLASSIFICATION_FILE
        
        with open(BOARD_CLASSIFICATION_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for category in data.get('categories', []):
            for board in category.get('boards', []):
                code = board.get('code', '')
                if code and code.startswith('BK'):
                    codes.append(code)
    except Exception as e:
        logger.warning(f"获取板块代码失败: {e}")
    
    return codes


class DataUpdateScheduler:
    """数据更新调度器"""
    
    def __init__(self):
        self._running = False
        self._lock = threading.Lock()
        self._last_update_times: Dict[str, Optional[datetime]] = {
            'morning_prewarm': None,
            'noon': None,
            'afternoon_switch': None,
            'daily_close': None
        }
    
    def start(self) -> bool:
        """启动调度器门面，并挂接真实日更循环（data_update_manager）。

        历史问题：此处仅置 _running=True，不启任何循环，导致
        system status 显示 scheduler_running=true 但 update_status
        的 last_run/next_run 长期陈旧、日更不触发。
        """
        with self._lock:
            if not self._running:
                self._running = True
                logger.info("[调度器] 门面已启动")
        # 真实循环：交易日历感知 + update_all_today / 盘中指数同步
        try:
            from data_update_manager import start_scheduler as start_real_scheduler
            start_real_scheduler()
            logger.info("[调度器] 已挂接 data_update_manager 日更循环")
        except Exception as e:
            logger.error(f"[调度器] 挂接真实日更循环失败: {e}")
            # 门面仍标记 running，避免 API 误导为“完全未启动”；
            # 但 get_status 会带上 real_scheduler 细节供诊断。
        return True
    
    def stop(self) -> bool:
        """停止调度器门面，并尝试停止真实日更循环"""
        with self._lock:
            self._running = False
            logger.info("[调度器] 门面已停止")
        try:
            from data_update_manager import stop_scheduler as stop_real_scheduler
            stop_real_scheduler()
        except Exception as e:
            logger.warning(f"[调度器] 停止真实日更循环失败: {e}")
        return True
    
    def is_running(self) -> bool:
        """检查调度器是否运行中（门面 flag；真实线程见 get_status）"""
        with self._lock:
            facade = self._running
        real_alive = False
        try:
            from data_update_manager import _scheduler_thread, _scheduler_running
            real_alive = bool(_scheduler_running and _scheduler_thread and _scheduler_thread.is_alive())
        except Exception:
            pass
        # 任一侧在跑都视为 running，避免启动竞态下 status 闪 false
        return facade or real_alive
    
    def trigger_update(self, update_type: str) -> bool:
        """
        触发指定类型的更新
        
        Args:
            update_type: 'morning_prewarm', 'noon', 'afternoon_switch', 'daily_close'
        
        Returns:
            bool: 更新成功返回 True
        """
        if not self._running:
            logger.warning("[调度器] 调度器未运行，无法触发更新")
            return False
        
        logger.info(f"[调度器] 触发更新: {update_type}")
        
        try:
            if update_type == 'noon':
                result = update_noon_data()
            elif update_type == 'daily_close':
                result = update_daily_close_data()
            elif update_type == 'afternoon_switch':
                result = afternoon_switch()
            elif update_type == 'morning_prewarm':
                # 早盘预热：清空昨日缓存，准备今日数据
                noon_cache_manager.clear_noon_cache()
                result = True
            else:
                logger.error(f"[调度器] 未知更新类型: {update_type}")
                return False
            
            if result:
                self._last_update_times[update_type] = datetime.now()
            
            return result
            
        except Exception as e:
            logger.error(f"[调度器] 更新失败: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态（含真实日更循环诊断字段）"""
        real = {
            'thread_alive': False,
            'loop_flag': False,
            'last_run': None,
            'next_run': None,
            'status': None,
        }
        try:
            from data_update_manager import (
                _scheduler_thread, _scheduler_running, get_update_status,
            )
            real['thread_alive'] = bool(
                _scheduler_thread is not None and _scheduler_thread.is_alive()
            )
            real['loop_flag'] = bool(_scheduler_running)
            st = get_update_status() or {}
            sch = st.get('scheduler') or {}
            real['last_run'] = sch.get('last_run')
            real['next_run'] = sch.get('next_run')
            real['status'] = sch.get('status')
        except Exception as e:
            real['error'] = str(e)[:120]
        with self._lock:
            facade_running = self._running
        return {
            'running': facade_running or real.get('thread_alive') or real.get('loop_flag'),
            'facade_running': facade_running,
            'real_scheduler': real,
            'last_updates': {
                k: v.isoformat() if v else None
                for k, v in self._last_update_times.items()
            },
            'schedule_times': UPDATE_SCHEDULE_TIMES,
            'noon_cache_valid': noon_cache_manager.is_noon_cache_valid()
        }


# 全局调度器实例
data_update_scheduler = DataUpdateScheduler()
