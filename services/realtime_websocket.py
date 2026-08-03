"""
WebSocket 实时推送服务
- 顶部指数导航栏实时推送（每3秒）
- 支持客户端订阅/取消订阅
"""
import json
import logging
import threading
import time
from typing import Set

from flask import Flask
from flask_sock import Sock

from core.config import WEBSOCKET_UPDATE_INTERVAL

logger = logging.getLogger('realtime_ws')


def _quote_fingerprint_row(row):
    if not isinstance(row, dict):
        return row
    price = row.get('price')
    if price is None:
        price = row.get('last')
    if price is None:
        price = row.get('close')
    change_pct = row.get('changePct')
    if change_pct is None:
        change_pct = row.get('change_pct')
    return {
        'price': price,
        'change_pct': change_pct,
        'unavailable': bool(row.get('unavailable')),
        'reason': row.get('reason'),
        'market': row.get('market'),
        'market_phase': row.get('market_phase'),
        'market_open': row.get('market_open') is True,
    }


def _indices_fingerprint(prices, market_signature):
    stable = {
        'data': {
            code: _quote_fingerprint_row(row)
            for code, row in (prices or {}).items()
        },
        'market_signature': list(market_signature or ()),
    }
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class RealtimeWebSocket:
    """实时数据 WebSocket 服务"""
    
    def __init__(self, app: Flask = None):
        self.sock = Sock()
        self._clients: Set = set()
        self._lock = threading.Lock()
        self._running = False
        self._broadcast_thread: threading.Thread = None
        self._last_indices_fingerprint = None
        
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
            # A newly connected tab still needs one complete snapshot even
            # when prices have not changed since the previous broadcast.
            # Existing tabs safely ignore it because the frontend only
            # animates an actually changed quote while its market is open.
            self._last_indices_fingerprint = None
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
            return False

        # 与 HTTP 首屏共用缓存优先快照，覆盖 A 股、港股、亚太和美股。
        from services.nav_spot_service import fetch_nav_spots_fast, get_nav_targets
        from services.market_session import market_phase_signature

        snapshot = fetch_nav_spots_fast()
        prices = snapshot.get('data') or {}
        market_signature = (snapshot.get('meta') or {}).get('market_signature')
        if market_signature is None:
            market_signature = market_phase_signature(get_nav_targets())
        fingerprint = _indices_fingerprint(prices, market_signature)
        with self._lock:
            if fingerprint == self._last_indices_fingerprint:
                return False
            self._last_indices_fingerprint = fingerprint
        
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
        return True


# 全局实例
realtime_websocket = RealtimeWebSocket()
