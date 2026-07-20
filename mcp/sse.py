"""
SSE (Server-Sent Events) 管理器
用于向前端推送实时事件
"""

import json
import queue
import threading
from typing import Dict, Any, Optional


class SSEClient:
    """SSE客户端连接"""
    
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.queue = queue.Queue()
        self.connected = True
        
    def put(self, event: Dict[str, Any]):
        """发送事件到客户端队列"""
        if self.connected:
            self.queue.put(event)
    
    def get(self, timeout: float = 30) -> Optional[Dict[str, Any]]:
        """获取事件（阻塞）"""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def disconnect(self):
        """断开连接"""
        self.connected = False


class SSEManager:
    """SSE连接管理器"""
    
    def __init__(self):
        self.clients: Dict[str, SSEClient] = {}
        self.lock = threading.Lock()
        self._counter = 0
        
    def subscribe(self, client_id: str = None) -> SSEClient:
        """订阅SSE，返回客户端对象"""
        with self.lock:
            if client_id is None:
                self._counter += 1
                client_id = f"client_{self._counter}"
            
            client = SSEClient(client_id)
            self.clients[client_id] = client
            return client
    
    def unsubscribe(self, client_id: str):
        """取消订阅"""
        with self.lock:
            client = self.clients.pop(client_id, None)
            if client:
                client.disconnect()
    
    def broadcast(self, event_type: str, data: Dict[str, Any]):
        """广播事件到所有客户端"""
        event = {
            'type': event_type,
            'data': data,
            'timestamp': self._get_timestamp()
        }
        
        with self.lock:
            # 清理已断开的客户端
            dead_clients = []
            for client_id, client in self.clients.items():
                try:
                    client.put(event)
                except:
                    dead_clients.append(client_id)
            
            # 移除断开的客户端
            for client_id in dead_clients:
                self.clients.pop(client_id, None)
    
    def send_to(self, client_id: str, event_type: str, data: Dict[str, Any]):
        """发送事件到指定客户端"""
        with self.lock:
            client = self.clients.get(client_id)
            if client:
                event = {
                    'type': event_type,
                    'data': data,
                    'timestamp': self._get_timestamp()
                }
                try:
                    client.put(event)
                except:
                    self.clients.pop(client_id, None)
    
    def get_client_count(self) -> int:
        """获取连接数"""
        with self.lock:
            return len(self.clients)
    
    def _get_timestamp(self) -> int:
        """获取当前时间戳（毫秒）"""
        import time
        return int(time.time() * 1000)


# 全局SSE管理器实例
sse_manager = SSEManager()
