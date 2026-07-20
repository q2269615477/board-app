"""
数据更新调度器
管理三个时间维度的数据更新：
- 盘后 (15:05): 更新所有东财板块 + 顶部指数导航栏
- 午休 (11:35): 更新顶部指数 + 所有东财板块到独立缓存
- 盘中: 顶部指数 WebSocket 推送 + 搜索实时拉取
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
    """
    更新午休数据
    - 顶部指数导航栏（QMT）
    - 所有东财板块（Tushare）
    - 保存到独立缓存文件
    
    Returns:
        bool: 更新成功返回 True
    """
    logger.info("[午休更新] 开始更新午休数据...")
    
    try:
        all_data = {}
        
        # 1. 获取顶部指数导航栏数据（QMT）
        index_codes = [code for code, _, _ in PREWARM_TARGETS if not code.startswith('BK')]
        try:
            from data.qmt_client import get_qmt_client
            client = get_qmt_client()
            index_data = client.get_constituents_batch(index_codes)
            all_data.update(index_data)
            logger.info(f"[午休更新] 指数数据: {len(index_data)} 个")
        except Exception as e:
            logger.warning(f"[午休更新] QMT指数获取失败，使用缓存: {e}")
            # 使用本地缓存兜底
            try:
                from data.sqlite_repo import get_cached_prices
                cached = get_cached_prices(index_codes)
                all_data.update({k: v for k, v in cached.items() if v})
            except Exception as cache_e:
                logger.warning(f"[午休更新] 本地缓存兜底也失败: {cache_e}")
        
        # 2. 获取东财板块数据（Tushare）
        try:
            from data import board_api
            industry_df = board_api.get_industry_boards()
            concept_df = board_api.get_concept_boards()
            
            if industry_df is not None:
                for _, row in industry_df.iterrows():
                    code = row.get('板块代码', '')
                    if code:
                        all_data[code] = {
                            'price': row.get('涨跌幅', 0),
                            'change_pct': row.get('涨跌幅', 0),
                            'volume': 0,
                            'source': 'tushare_industry'
                        }
            
            if concept_df is not None:
                for _, row in concept_df.iterrows():
                    code = row.get('板块代码', '')
                    if code:
                        all_data[code] = {
                            'price': row.get('涨跌幅', 0),
                            'change_pct': row.get('涨跌幅', 0),
                            'volume': 0,
                            'source': 'tushare_concept'
                        }
            
            logger.info(f"[午休更新] 东财板块数据已获取")
            
        except Exception as e:
            logger.warning(f"[午休更新] Tushare板块获取失败: {e}")
            # 使用本地缓存兜底
            try:
                from data.sqlite_repo import get_cached_prices
                board_codes = _get_all_board_codes()
                cached = get_cached_prices(board_codes)
                all_data.update({k: v for k, v in cached.items() if v})
            except Exception as cache_e:
                logger.warning(f"[午休更新] 板块本地缓存兜底也失败: {cache_e}")
        
        # 3. 保存到午休缓存
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
    - 所有东财板块（Tushare）
    - 顶部指数导航栏（QMT）
    - 写入主存储（SQLite/CSV）
    
    Returns:
        bool: 更新成功返回 True
    """
    logger.info("[盘后更新] 开始更新盘后数据...")
    
    try:
        # 使用现有的 update_all_today 函数
        from data_update_manager import update_all_today
        update_all_today()
        logger.info("[盘后更新] 盘后数据更新完成")
        return True
        
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
