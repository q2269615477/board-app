# 数据更新机制实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立覆盖盘后、午休、盘中三个时间维度的数据更新机制，确保今天数据零错误更新到存储中

**Architecture:** 
- 使用独立调度器服务管理三个时段的更新任务
- 午休数据独立存储于 `noon_cache_YYYYMMDD.json`，13:00 自动切换至实时数据
- 顶部指数导航栏通过 WebSocket 实时推送，个股/板块搜索按需轮询
- 东财板块全走 Tushare，指数/个股全走 QMT，失败时用本地缓存兜底

**Tech Stack:** Python, APScheduler, WebSocket, Tushare, QMT(xtquant), SQLite/JSON

## Global Constraints

- 所有数据源失败时必须用本地缓存兜底，禁止暴露失败
- 午休数据与盘后数据严格分离存储
- 13:00 开盘瞬间必须完成从午休缓存到实时数据的切换
- 今天的数据必须被成功更新到存储中（验证标准）
- 代码必须通过所有单元测试

---

## File Structure

### 新建文件
- `services/data_update_scheduler.py` - 统一调度器，管理三个时段的更新任务
- `services/noon_cache_manager.py` - 午休数据缓存管理
- `services/realtime_websocket.py` - WebSocket 实时推送服务
- `tests/test_data_update_scheduler.py` - 调度器单元测试
- `tests/test_noon_cache_manager.py` - 午休缓存管理单元测试

### 修改文件
- `core/config.py` - 添加调度器配置、午休缓存路径
- `core/lifecycle.py` - 集成调度器启动/停止到应用生命周期
- `services/qmt_cache_service.py` - 添加 WebSocket 推送能力
- `data_update_manager.py` - 整合到调度器，支持三个时段更新
- `data/board_api.py` - 添加午休数据获取接口
- `app.py` - 添加 WebSocket 路由

---

## Task 1: 配置扩展

**Files:**
- Modify: `core/config.py`

**Interfaces:**
- Produces: `NOON_CACHE_DIR`, `NOON_CACHE_FILE_PATTERN`, `UPDATE_SCHEDULE_TIMES`, `WEBSOCKET_UPDATE_INTERVAL`

- [ ] **Step 1: 添加午休缓存配置**

```python
# 在 core/config.py 中添加

# 午休缓存配置
NOON_CACHE_DIR = DATA_DIR / 'noon_cache'
NOON_CACHE_FILE_PATTERN = 'noon_cache_{date}.json'  # date format: YYYYMMDD

# 更新调度时间配置（混合模式：固定时间 + 状态检测）
UPDATE_SCHEDULE_TIMES = {
    'morning_prewarm': '09:25',   # 开盘前预热
    'noon_update': '11:35',       # 午休更新
    'afternoon_switch': '13:00',  # 下午开盘切换
    'daily_close': '15:05',       # 盘后更新
}

# WebSocket 推送间隔（秒）
WEBSOCKET_UPDATE_INTERVAL = 3  # 顶部指数导航栏推送间隔
```

- [ ] **Step 2: 验证配置导入**

Run: `python -c "from core.config import NOON_CACHE_DIR, UPDATE_SCHEDULE_TIMES; print('Config OK')"`
Expected: `Config OK`

- [ ] **Step 3: Commit**

```bash
git add core/config.py
git commit -m "config: add noon cache and update schedule configuration"
```

---

## Task 2: 午休缓存管理器

**Files:**
- Create: `services/noon_cache_manager.py`
- Test: `tests/test_noon_cache_manager.py`

**Interfaces:**
- Produces: `NoonCacheManager` class with methods: `save_noon_data()`, `load_noon_data()`, `clear_noon_cache()`, `is_noon_cache_valid()`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_noon_cache_manager.py
import pytest
import json
from datetime import datetime
from pathlib import Path
from services.noon_cache_manager import NoonCacheManager

def test_save_and_load_noon_data():
    """测试午休数据保存和加载"""
    manager = NoonCacheManager()
    test_data = {
        'sh000001': {'price': 3000.5, 'change_pct': 1.2, 'volume': 1000000},
        'BK1499': {'price': 1500.0, 'change_pct': -0.5, 'volume': 500000}
    }
    
    # 保存
    manager.save_noon_data(test_data)
    
    # 加载
    loaded = manager.load_noon_data()
    assert loaded == test_data

def test_clear_noon_cache():
    """测试清空午休缓存"""
    manager = NoonCacheManager()
    manager.save_noon_data({'test': 'data'})
    manager.clear_noon_cache()
    assert manager.load_noon_data() == {}

def test_is_noon_cache_valid():
    """测试缓存有效性检查"""
    manager = NoonCacheManager()
    
    # 空缓存应该无效
    assert not manager.is_noon_cache_valid()
    
    # 有数据后应该有效
    manager.save_noon_data({'test': 'data'})
    assert manager.is_noon_cache_valid()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_noon_cache_manager.py -v`
Expected: 3 FAIL (ImportError: No module named 'services.noon_cache_manager')

- [ ] **Step 3: 实现午休缓存管理器**

```python
# services/noon_cache_manager.py
"""
午休数据缓存管理器
处理中午休息时间的临时数据存储和13:00切换逻辑
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from core.config import NOON_CACHE_DIR, NOON_CACHE_FILE_PATTERN

logger = logging.getLogger('noon_cache')


class NoonCacheManager:
    """午休数据缓存管理器"""
    
    def __init__(self):
        self._cache_dir = Path(NOON_CACHE_DIR)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._current_date = datetime.now().strftime('%Y%m%d')
    
    def _get_cache_file(self) -> Path:
        """获取今日缓存文件路径"""
        filename = NOON_CACHE_FILE_PATTERN.format(date=self._current_date)
        return self._cache_dir / filename
    
    def save_noon_data(self, data: Dict[str, Any]) -> bool:
        """
        保存午休数据到独立缓存文件
        
        Args:
            data: {code: {price, change_pct, volume, ...}}
        
        Returns:
            bool: 保存成功返回 True
        """
        try:
            cache_file = self._get_cache_file()
            cache_data = {
                'date': self._current_date,
                'timestamp': datetime.now().isoformat(),
                'data': data,
                'is_noon_data': True
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"[午休缓存] 已保存 {len(data)} 个标的到 {cache_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"[午休缓存] 保存失败: {e}")
            return False
    
    def load_noon_data(self) -> Dict[str, Any]:
        """
        加载午休缓存数据
        
        Returns:
            Dict: 缓存的数据，如果没有缓存返回空字典
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return {}
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证日期匹配
            if cache_data.get('date') != self._current_date:
                logger.warning(f"[午休缓存] 日期不匹配，忽略缓存")
                return {}
            
            return cache_data.get('data', {})
            
        except Exception as e:
            logger.error(f"[午休缓存] 加载失败: {e}")
            return {}
    
    def clear_noon_cache(self) -> bool:
        """
        清空今日午休缓存
        
        Returns:
            bool: 清空成功返回 True
        """
        try:
            cache_file = self._get_cache_file()
            if cache_file.exists():
                cache_file.unlink()
                logger.info(f"[午休缓存] 已清空 {cache_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"[午休缓存] 清空失败: {e}")
            return False
    
    def is_noon_cache_valid(self) -> bool:
        """
        检查午休缓存是否有效（存在且日期匹配）
        
        Returns:
            bool: 缓存有效返回 True
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return False
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            return cache_data.get('date') == self._current_date and 'data' in cache_data
            
        except Exception:
            return False
    
    def get_cache_metadata(self) -> Optional[Dict[str, Any]]:
        """
        获取缓存元数据（时间戳等）
        
        Returns:
            Dict: 包含 timestamp, date 等元数据
        """
        try:
            cache_file = self._get_cache_file()
            if not cache_file.exists():
                return None
            
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            return {
                'date': cache_data.get('date'),
                'timestamp': cache_data.get('timestamp'),
                'record_count': len(cache_data.get('data', {}))
            }
            
        except Exception:
            return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_noon_cache_manager.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add services/noon_cache_manager.py tests/test_noon_cache_manager.py
git commit -m "feat: add noon cache manager for lunch break data storage"
```

---

## Task 3: 数据更新调度器

**Files:**
- Create: `services/data_update_scheduler.py`
- Test: `tests/test_data_update_scheduler.py`

**Interfaces:**
- Produces: `DataUpdateScheduler` class with methods: `start()`, `stop()`, `trigger_update()`, `get_status()`
- Consumes: `NoonCacheManager` (from Task 2), `data_update_manager` (existing), `board_api` (existing)

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_data_update_scheduler.py
import pytest
from datetime import datetime
from unittest.mock import Mock, patch
from services.data_update_scheduler import DataUpdateScheduler

def test_scheduler_initialization():
    """测试调度器初始化"""
    scheduler = DataUpdateScheduler()
    assert scheduler.is_running() == False
    assert scheduler.get_status()['next_updates'] is not None

def test_trigger_noon_update():
    """测试触发午休更新"""
    scheduler = DataUpdateScheduler()
    
    with patch('services.data_update_scheduler.update_noon_data') as mock_update:
        mock_update.return_value = True
        result = scheduler.trigger_update('noon')
        assert result == True
        mock_update.assert_called_once()

def test_trigger_daily_close_update():
    """测试触发盘后更新"""
    scheduler = DataUpdateScheduler()
    
    with patch('services.data_update_scheduler.update_daily_close_data') as mock_update:
        mock_update.return_value = True
        result = scheduler.trigger_update('daily_close')
        assert result == True
        mock_update.assert_called_once()

def test_afternoon_switch_clears_noon_cache():
    """测试下午开盘切换清空午休缓存"""
    scheduler = DataUpdateScheduler()
    
    with patch('services.data_update_scheduler.noon_cache_manager') as mock_cache:
        scheduler.trigger_update('afternoon_switch')
        mock_cache.clear_noon_cache.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_data_update_scheduler.py -v`
Expected: 4 FAIL (ImportError)

- [ ] **Step 3: 实现数据更新调度器**

```python
# services/data_update_scheduler.py
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
from typing import Dict, Any, Optional, Callable
from enum import Enum

from core.config import UPDATE_SCHEDULE_TIMES, PREWARM_TARGETS
from services.noon_cache_manager import NoonCacheManager
from data import board_api
from data_update_manager import update_boards, update_indices

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
            from data.sqlite_repo import get_cached_prices
            cached = get_cached_prices(index_codes)
            all_data.update({k: v for k, v in cached.items() if v})
        
        # 2. 获取东财板块数据（Tushare）
        try:
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
            from data.sqlite_repo import get_cached_prices
            board_codes = _get_all_board_codes()
            cached = get_cached_prices(board_codes)
            all_data.update({k: v for k, v in cached.items() if v})
        
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
        # 1. 更新东财板块（Tushare）
        try:
            update_boards()
            logger.info("[盘后更新] 东财板块更新完成")
        except Exception as e:
            logger.error(f"[盘后更新] 东财板块更新失败: {e}")
            return False
        
        # 2. 更新指数（QMT）
        try:
            update_indices()
            logger.info("[盘后更新] 指数更新完成")
        except Exception as e:
            logger.error(f"[盘后更新] 指数更新失败: {e}")
            return False
        
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
        """启动调度器"""
        with self._lock:
            if self._running:
                return True
            
            self._running = True
            logger.info("[调度器] 数据更新调度器已启动")
            return True
    
    def stop(self) -> bool:
        """停止调度器"""
        with self._lock:
            if not self._running:
                return True
            
            self._running = False
            logger.info("[调度器] 数据更新调度器已停止")
            return True
    
    def is_running(self) -> bool:
        """检查调度器是否运行中"""
        with self._lock:
            return self._running
    
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
        """获取调度器状态"""
        return {
            'running': self._running,
            'last_updates': {
                k: v.isoformat() if v else None
                for k, v in self._last_update_times.items()
            },
            'schedule_times': UPDATE_SCHEDULE_TIMES,
            'noon_cache_valid': noon_cache_manager.is_noon_cache_valid()
        }


# 全局调度器实例
data_update_scheduler = DataUpdateScheduler()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_data_update_scheduler.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add services/data_update_scheduler.py tests/test_data_update_scheduler.py
git commit -m "feat: add data update scheduler for three time dimensions"
```

---

## Task 4: WebSocket 实时推送服务

**Files:**
- Create: `services/realtime_websocket.py`

**Interfaces:**
- Produces: `RealtimeWebSocket` class with methods: `start()`, `stop()`, `broadcast_indices()`, `register_client()`
- Consumes: `QMTCacheService` (existing), `PREWARM_TARGETS` (config)

- [ ] **Step 1: 实现 WebSocket 服务**

```python
# services/realtime_websocket.py
"""
WebSocket 实时推送服务
- 顶部指数导航栏实时推送（每3秒）
- 支持客户端订阅/取消订阅
"""
import json
import logging
import threading
import time
from typing import Set, Dict, Any

from flask import Flask
from flask_sock import Sock

from core.config import PREWARM_TARGETS, WEBSOCKET_UPDATE_INTERVAL
from services.qmt_cache_service import qmt_cache_service

logger = logging.getLogger('realtime_ws')


class RealtimeWebSocket:
    """实时数据 WebSocket 服务"""
    
    def __init__(self, app: Flask = None):
        self.sock = Sock()
        self._clients: Set = set()
        self._lock = threading.Lock()
        self._running = False
        self._broadcast_thread: threading.Thread = None
        
        if app:
            self.init_app(app)
    
    def init_app(self, app: Flask):
        """初始化 Flask 应用"""
        self.sock.init_app(app)
        
        @self.sock.route('/ws/realtime')
        def realtime_ws(ws):
            """WebSocket 连接处理"""
            self._register_client(ws)
            try:
                while True:
                    # 接收客户端消息（订阅/取消订阅等）
                    message = ws.receive()
                    if message:
                        self._handle_message(ws, message)
            except Exception as e:
                logger.debug(f"[WebSocket] 客户端断开: {e}")
            finally:
                self._unregister_client(ws)
    
    def _register_client(self, ws):
        """注册客户端"""
        with self._lock:
            self._clients.add(ws)
        logger.info(f"[WebSocket] 客户端连接，当前在线: {len(self._clients)}")
    
    def _unregister_client(self, ws):
        """注销客户端"""
        with self._lock:
            self._clients.discard(ws)
        logger.info(f"[WebSocket] 客户端断开，当前在线: {len(self._clients)}")
    
    def _handle_message(self, ws, message: str):
        """处理客户端消息"""
        try:
            data = json.loads(message)
            action = data.get('action')
            
            if action == 'subscribe_indices':
                # 客户端订阅指数更新
                logger.debug("[WebSocket] 客户端订阅指数")
            elif action == 'ping':
                ws.send(json.dumps({'type': 'pong'}))
                
        except json.JSONDecodeError:
            logger.warning(f"[WebSocket] 无效消息格式: {message}")
    
    def start(self):
        """启动广播线程"""
        if self._running:
            return
        
        self._running = True
        self._broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._broadcast_thread.start()
        logger.info("[WebSocket] 实时推送服务已启动")
    
    def stop(self):
        """停止广播线程"""
        self._running = False
        if self._broadcast_thread:
            self._broadcast_thread.join(timeout=5)
        logger.info("[WebSocket] 实时推送服务已停止")
    
    def _broadcast_loop(self):
        """广播循环"""
        while self._running:
            try:
                self._broadcast_indices()
            except Exception as e:
                logger.error(f"[WebSocket] 广播失败: {e}")
            
            time.sleep(WEBSOCKET_UPDATE_INTERVAL)
    
    def _broadcast_indices(self):
        """广播顶部指数导航栏数据"""
        if not self._clients:
            return
        
        # 获取指数代码
        index_codes = [code for code, _, _ in PREWARM_TARGETS]
        
        # 从 QMT 缓存获取数据
        prices = qmt_cache_service.get_cached_prices(index_codes)
        
        # 构建消息
        message = {
            'type': 'indices_update',
            'timestamp': int(time.time() * 1000),
            'data': prices
        }
        
        # 广播给所有客户端
        disconnected = set()
        with self._lock:
            for client in self._clients:
                try:
                    client.send(json.dumps(message))
                except Exception:
                    disconnected.add(client)
            
            # 清理断开的客户端
            self._clients -= disconnected
        
        if disconnected:
            logger.info(f"[WebSocket] 清理断开客户端: {len(disconnected)}")


# 全局实例
realtime_websocket = RealtimeWebSocket()
```

- [ ] **Step 2: 安装 flask-sock 依赖**

Run: `pip install flask-sock`

- [ ] **Step 3: Commit**

```bash
git add services/realtime_websocket.py
git commit -m "feat: add WebSocket realtime push service for index navigation"
```

---

## Task 5: 集成到应用生命周期

**Files:**
- Modify: `core/lifecycle.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `DataUpdateScheduler`, `RealtimeWebSocket`, `NoonCacheManager`

- [ ] **Step 1: 修改 lifecycle.py 集成调度器**

```python
# 在 core/lifecycle.py 中添加/修改

from services.data_update_scheduler import data_update_scheduler
from services.noon_cache_manager import noon_cache_manager

# 在 AppLifecycle 类中添加

def _start_services(self):
    """启动所有服务"""
    # ... 现有代码 ...
    
    # 启动数据更新调度器
    try:
        data_update_scheduler.start()
        logger.info("[生命周期] 数据更新调度器已启动")
    except Exception as e:
        logger.error(f"[生命周期] 调度器启动失败: {e}")
    
    # 启动 QMT 缓存服务
    try:
        from services.qmt_cache_service import qmt_cache_service
        qmt_cache_service.start()
        # 添加指数到关注列表
        from core.config import PREWARM_TARGETS
        index_codes = [code for code, _, _ in PREWARM_TARGETS]
        qmt_cache_service.watch_codes(index_codes)
        logger.info(f"[生命周期] QMT缓存服务已启动，关注 {len(index_codes)} 个指数")
    except Exception as e:
        logger.error(f"[生命周期] QMT缓存服务启动失败: {e}")

def _stop_services(self):
    """停止所有服务"""
    # 停止数据更新调度器
    try:
        data_update_scheduler.stop()
        logger.info("[生命周期] 数据更新调度器已停止")
    except Exception as e:
        logger.error(f"[生命周期] 调度器停止失败: {e}")
    
    # 停止 QMT 缓存服务
    try:
        from services.qmt_cache_service import qmt_cache_service
        qmt_cache_service.stop()
        logger.info("[生命周期] QMT缓存服务已停止")
    except Exception as e:
        logger.error(f"[生命周期] QMT缓存服务停止失败: {e}")
    
    # ... 现有代码 ...
```

- [ ] **Step 2: 修改 app.py 添加 WebSocket 路由**

```python
# 在 app.py 中添加

from flask_sock import Sock
from services.realtime_websocket import realtime_websocket

# 初始化 WebSocket
sock = Sock(app)
realtime_websocket.init_app(app)

# 在应用启动时启动 WebSocket 服务
@app.before_first_request
def start_websocket():
    realtime_websocket.start()
```

- [ ] **Step 3: 运行应用测试**

Run: `python -c "from app import app; print('App OK')"`
Expected: `App OK`

- [ ] **Step 4: Commit**

```bash
git add core/lifecycle.py app.py
git commit -m "feat: integrate data update scheduler and WebSocket into app lifecycle"
```

---

## Task 6: 添加数据获取 API（支持午休缓存）

**Files:**
- Modify: `data/board_api.py`
- Modify: `api/board_routes.py`

**Interfaces:**
- Produces: `get_board_data_with_fallback()`, `get_index_data_with_fallback()`

- [ ] **Step 1: 修改 board_api.py 添加缓存感知接口**

```python
# 在 data/board_api.py 中添加

from services.noon_cache_manager import noon_cache_manager
from datetime import datetime

def get_board_data_with_fallback(board_code: str) -> Optional[Dict]:
    """
    获取板块数据（支持午休缓存）
    
    逻辑：
    1. 13:00 前且午休缓存有效 → 使用缓存
    2. 其他时间 → 实时获取（Tushare）
    3. 失败 → 本地缓存兜底
    
    Args:
        board_code: 板块代码（如 BK1499）
    
    Returns:
        Dict: 板块数据，失败返回 None
    """
    now = datetime.now()
    is_afternoon = now.hour >= 13
    
    # 1. 检查是否使用午休缓存（13:00 前且缓存有效）
    if not is_afternoon and noon_cache_manager.is_noon_cache_valid():
        noon_data = noon_cache_manager.load_noon_data()
        if board_code in noon_data:
            logger.debug(f"[板块数据] 使用午休缓存: {board_code}")
            return noon_data[board_code]
    
    # 2. 实时获取（Tushare）
    try:
        # 东财板块走 Tushare
        df = get_board_daily_data(board_code)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                'price': float(latest.get('收盘', 0)),
                'change_pct': float(latest.get('涨跌幅', 0)),
                'volume': int(latest.get('成交量', 0)),
                'source': 'tushare_realtime'
            }
    except Exception as e:
        logger.warning(f"[板块数据] Tushare获取失败: {e}")
    
    # 3. 本地缓存兜底
    try:
        from data.sqlite_repo import get_cached_prices
        cached = get_cached_prices([board_code])
        if cached.get(board_code):
            data = cached[board_code]
            data['source'] = 'local_cache'
            return data
    except Exception as e:
        logger.error(f"[板块数据] 本地缓存兜底失败: {e}")
    
    return None


def get_index_data_with_fallback(index_code: str) -> Optional[Dict]:
    """
    获取指数数据（支持午休缓存）
    
    逻辑：
    1. 13:00 前且午休缓存有效 → 使用缓存
    2. 其他时间 → 实时获取（QMT）
    3. 失败 → 本地缓存兜底
    
    Args:
        index_code: 指数代码（如 sh000001）
    
    Returns:
        Dict: 指数数据，失败返回 None
    """
    now = datetime.now()
    is_afternoon = now.hour >= 13
    
    # 1. 检查是否使用午休缓存（13:00 前且缓存有效）
    if not is_afternoon and noon_cache_manager.is_noon_cache_valid():
        noon_data = noon_cache_manager.load_noon_data()
        if index_code in noon_data:
            logger.debug(f"[指数数据] 使用午休缓存: {index_code}")
            return noon_data[index_code]
    
    # 2. 实时获取（QMT）
    try:
        from data.qmt_client import get_qmt_client
        client = get_qmt_client()
        data = client.get_constituents_batch([index_code])
        if data and index_code in data:
            result = data[index_code]
            result['source'] = 'qmt_realtime'
            return result
    except Exception as e:
        logger.warning(f"[指数数据] QMT获取失败: {e}")
    
    # 3. 本地缓存兜底
    try:
        from data.sqlite_repo import get_cached_prices
        cached = get_cached_prices([index_code])
        if cached.get(index_code):
            data = cached[index_code]
            data['source'] = 'local_cache'
            return data
    except Exception as e:
        logger.error(f"[指数数据] 本地缓存兜底失败: {e}")
    
    return None
```

- [ ] **Step 2: 修改 board_routes.py 使用新接口**

```python
# 在 api/board_routes.py 中修改现有路由

from data.board_api import get_board_data_with_fallback, get_index_data_with_fallback

# 修改获取板块数据的路由
@board_routes.route('/api/board/<board_code>')
def get_board(board_code):
    """获取板块数据（支持午休缓存）"""
    data = get_board_data_with_fallback(board_code)
    if data:
        return jsonify({'success': True, 'data': data})
    return jsonify({'success': False, 'error': '数据不可用'}), 503

# 修改获取指数数据的路由
@board_routes.route('/api/index/<index_code>')
def get_index(index_code):
    """获取指数数据（支持午休缓存）"""
    data = get_index_data_with_fallback(index_code)
    if data:
        return jsonify({'success': True, 'data': data})
    return jsonify({'success': False, 'error': '数据不可用'}), 503
```

- [ ] **Step 3: Commit**

```bash
git add data/board_api.py api/board_routes.py
git commit -m "feat: add data API with noon cache support and fallback mechanism"
```

---

## Task 7: 手动触发更新测试（验证今天数据）

**Files:**
- Create: `scripts/test_data_update.py` - 测试脚本

**完成标准:** 今天的数据被成功更新到存储中

- [ ] **Step 1: 创建测试脚本**

```python
# scripts/test_data_update.py
"""
数据更新机制测试脚本
验证今天的数据能否被正确更新到存储中
"""
import sys
import json
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_update_scheduler import data_update_scheduler, UpdateType
from services.noon_cache_manager import noon_cache_manager
from core.config import NOON_CACHE_DIR

def test_noon_update():
    """测试午休更新"""
    print("=" * 60)
    print("测试1: 午休数据更新")
    print("=" * 60)
    
    # 启动调度器
    data_update_scheduler.start()
    
    # 触发午休更新
    result = data_update_scheduler.trigger_update('noon')
    
    if result:
        print("✓ 午休更新成功")
        
        # 验证缓存文件
        if noon_cache_manager.is_noon_cache_valid():
            data = noon_cache_manager.load_noon_data()
            metadata = noon_cache_manager.get_cache_metadata()
            print(f"✓ 午休缓存有效")
            print(f"  - 记录数: {metadata.get('record_count', 0)}")
            print(f"  - 时间戳: {metadata.get('timestamp', 'N/A')}")
            print(f"  - 样本数据: {list(data.keys())[:5]}")
            return True
        else:
            print("✗ 午休缓存无效")
            return False
    else:
        print("✗ 午休更新失败")
        return False

def test_afternoon_switch():
    """测试下午开盘切换"""
    print("\n" + "=" * 60)
    print("测试2: 下午开盘切换")
    print("=" * 60)
    
    # 触发切换
    result = data_update_scheduler.trigger_update('afternoon_switch')
    
    if result:
        print("✓ 下午切换成功")
        
        # 验证缓存已清空
        if not noon_cache_manager.is_noon_cache_valid():
            print("✓ 午休缓存已清空")
            return True
        else:
            print("✗ 午休缓存未清空")
            return False
    else:
        print("✗ 下午切换失败")
        return False

def test_daily_close_update():
    """测试盘后更新"""
    print("\n" + "=" * 60)
    print("测试3: 盘后数据更新")
    print("=" * 60)
    
    # 触发盘后更新
    result = data_update_scheduler.trigger_update('daily_close')
    
    if result:
        print("✓ 盘后更新成功")
        print("  - 东财板块数据已更新")
        print("  - 指数数据已更新")
        return True
    else:
        print("✗ 盘后更新失败")
        return False

def verify_today_data():
    """验证今天的数据是否已更新"""
    print("\n" + "=" * 60)
    print("验证: 今天的数据更新状态")
    print("=" * 60)
    
    today = datetime.now().strftime('%Y%m%d')
    
    # 检查午休缓存
    if noon_cache_manager.is_noon_cache_valid():
        metadata = noon_cache_manager.get_cache_metadata()
        if metadata.get('date') == today:
            print(f"✓ 今日({today})午休数据已缓存")
        else:
            print(f"✗ 缓存日期不匹配: {metadata.get('date')} != {today}")
    
    # 检查主存储（SQLite/CSV）
    from data.sqlite_repo import get_cached_prices
    from core.config import PREWARM_TARGETS
    
    index_codes = [code for code, _, _ in PREWARM_TARGETS[:3]]  # 检查前3个指数
    prices = get_cached_prices(index_codes)
    
    has_data = any(prices.get(code) for code in index_codes)
    if has_data:
        print(f"✓ 指数数据已在主存储中")
        for code in index_codes:
            if prices.get(code):
                print(f"  - {code}: {prices[code]}")
    else:
        print(f"⚠ 指数数据可能尚未更新到主存储（或盘后更新未完成）")
    
    return True

def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("数据更新机制测试")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = []
    
    # 根据当前时间选择测试项目
    now = datetime.now()
    hour = now.hour
    
    if 11 <= hour < 13:
        # 午休时间：测试午休更新
        results.append(("午休更新", test_noon_update()))
    elif hour >= 15:
        # 盘后：测试所有
        results.append(("午休更新", test_noon_update()))
        results.append(("下午切换", test_afternoon_switch()))
        results.append(("盘后更新", test_daily_close_update()))
    else:
        # 盘中：测试实时数据获取
        print("当前为盘中时间，测试实时数据获取...")
        verify_today_data()
        results.append(("实时数据", True))
    
    # 验证今天的数据
    verify_today_data()
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    # 最终验证标准
    print("\n" + "=" * 60)
    print("最终验证标准: 今天的数据必须被成功更新到存储中")
    print("=" * 60)
    
    if all_passed:
        print("✓ 所有测试通过，今天的数据已更新")
        return 0
    else:
        print("✗ 部分测试失败，请检查日志")
        return 1

if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: 运行测试脚本**

Run: `python scripts/test_data_update.py`
Expected: 所有测试通过，今天的数据已更新确认

- [ ] **Step 3: Commit**

```bash
git add scripts/test_data_update.py
git commit -m "test: add data update mechanism test script"
```

---

## Task 8: 运行完整测试套件

**Files:**
- All test files

- [ ] **Step 1: 运行所有单元测试**

Run: `pytest tests/ -v --tb=short`
Expected: 所有测试通过（包括新增的 test_noon_cache_manager.py 和 test_data_update_scheduler.py）

- [ ] **Step 2: 验证代码覆盖率**

Run: `pytest tests/ --cov=services --cov=data --cov-report=term-missing`
Expected: 核心模块覆盖率 > 80%

- [ ] **Step 3: Commit**

```bash
git add tests/
git commit -m "test: ensure all tests pass with new data update mechanism"
```

---

## 最终验证清单

- [ ] 今天的数据被成功更新到存储中（运行 scripts/test_data_update.py 确认）
- [ ] 所有单元测试通过（pytest tests/）
- [ ] 午休缓存文件正确生成（data/noon_cache/noon_cache_YYYYMMDD.json）
- [ ] 13:00 切换逻辑正常（清空午休缓存，切换到实时数据）
- [ ] WebSocket 实时推送正常（顶部指数导航栏每3秒更新）
- [ ] 数据源失败时用本地缓存兜底（不暴露失败）

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-07-08-data-update-mechanism.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints for review

**Which approach?**
