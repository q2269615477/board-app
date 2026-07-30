"""
MiniQMT 常驻管理器
- 开机后保持常驻
- 心跳检测，自动唤醒
- 进程守护，崩溃自动重启
"""
import os
import time
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from core.config import QMT_MINI_PATH, QMT_DIR, QMT_PORTS

logger = logging.getLogger('miniqmt_manager')


class MiniQMTManager:
    """MiniQMT 常驻管理器（单例）"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        self._mini_path = Path(QMT_MINI_PATH)
        self._qmt_dir = Path(QMT_DIR)
        self._rpc_port = QMT_PORTS[0]  # 58600
        
        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        self._last_heartbeat = 0
        self._consecutive_failures = 0
        self._max_failures = 3
        
        # 心跳间隔（秒）
        self._heartbeat_interval = 30  # 30秒检测一次
        self._restart_cooldown = 60  # 重启后60秒内不重复重启
        self._last_restart = 0
    
    def start(self) -> bool:
        """启动 MiniQMT 常驻管理"""
        if self._running:
            logger.info("[MiniQMT管理器] 已在运行")
            return True
        
        if not self._mini_path.exists():
            logger.error(f"[MiniQMT管理器] 找不到 MiniQMT: {self._mini_path}")
            return False
        
        self._running = True
        self._stop_event.clear()
        
        # 启动心跳线程
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="MiniQMT-Heartbeat"
        )
        self._heartbeat_thread.start()
        
        logger.info("[MiniQMT管理器] 已启动，心跳间隔 30 秒")
        return True
    
    def stop(self):
        """停止 MiniQMT 常驻管理"""
        self._running = False
        self._stop_event.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        logger.info("[MiniQMT管理器] 已停止")
    
    def is_running(self) -> bool:
        """管理器是否运行中"""
        return self._running
    
    def is_miniqmt_alive(self) -> bool:
        """检测 MiniQMT 是否存活（端口检测）"""
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', self._rpc_port))
            sock.close()
            return result == 0
        except Exception:
            return False
    
    def _heartbeat_loop(self):
        """心跳检测循环"""
        logger.info("[MiniQMT心跳] 检测循环启动")
        
        # 首次启动 MiniQMT
        if not self.is_miniqmt_alive():
            logger.info("[MiniQMT心跳] 初始启动 MiniQMT...")
            self._start_minqmt()
        
        while not self._stop_event.is_set():
            try:
                self._do_heartbeat()
            except Exception as e:
                logger.error(f"[MiniQMT心跳] 异常: {e}")
            
            self._stop_event.wait(self._heartbeat_interval)
        
        logger.info("[MiniQMT心跳] 检测循环结束")
    
    def _do_heartbeat(self):
        """执行一次心跳检测"""
        self._last_heartbeat = time.time()
        
        is_alive = self.is_miniqmt_alive()
        
        if is_alive:
            if self._consecutive_failures > 0:
                logger.info(f"[MiniQMT心跳] ✓ 服务恢复，连续失败次数重置")
                self._consecutive_failures = 0
            else:
                logger.debug("[MiniQMT心跳] ✓ 服务正常")
        else:
            self._consecutive_failures += 1
            logger.warning(
                f"[MiniQMT心跳] ✗ 服务无响应 "
                f"(连续 {self._consecutive_failures}/{self._max_failures} 次)"
            )
            
            if self._consecutive_failures >= self._max_failures:
                logger.error("[MiniQMT心跳] 连续失败达到阈值，尝试重启...")
                self._restart_minqmt()
    
    def _start_minqmt(self) -> bool:
        """启动 MiniQMT 进程"""
        try:
            logger.info(f"[MiniQMT启动] 启动: {self._mini_path.name}")
            
            # 使用 -background 参数后台启动
            proc = subprocess.Popen(
                [str(self._mini_path), '-background'],
                cwd=str(self._qmt_dir),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            self._last_restart = time.time()
            logger.info(f"[MiniQMT启动] 进程已启动 (PID: {proc.pid})")
            
            # 等待服务就绪
            for i in range(10):  # 最多等待 10 秒
                time.sleep(1)
                if self.is_miniqmt_alive():
                    logger.info("[MiniQMT启动] ✓ 服务已就绪")
                    return True
            
            logger.warning("[MiniQMT启动] 服务启动超时，可能仍在初始化")
            return False
            
        except Exception as e:
            logger.error(f"[MiniQMT启动] 失败: {e}")
            return False
    
    def _stop_minqmt(self):
        """停止 MiniQMT 进程"""
        try:
            # 查找并终止 XtMiniQmt.exe 进程
            subprocess.run(
                ['taskkill', '/F', '/IM', 'XtMiniQmt.exe'],
                capture_output=True,
                timeout=10
            )
            logger.info("[MiniQMT停止] 已终止现有进程")
            time.sleep(2)  # 等待进程完全退出
        except Exception as e:
            logger.warning(f"[MiniQMT停止] 终止进程时出错: {e}")
    
    def _restart_minqmt(self):
        """重启 MiniQMT"""
        # 检查冷却时间
        elapsed = time.time() - self._last_restart
        if elapsed < self._restart_cooldown:
            logger.info(f"[MiniQMT重启] 冷却中，{self._restart_cooldown - elapsed:.0f} 秒后再试")
            return False
        
        logger.info("[MiniQMT重启] 开始重启流程...")
        
        # 1. 停止现有进程
        self._stop_minqmt()
        
        # 2. 启动新进程
        success = self._start_minqmt()
        
        if success:
            self._consecutive_failures = 0
            logger.info("[MiniQMT重启] ✓ 重启成功")
        else:
            logger.error("[MiniQMT重启] ✗ 重启失败")
        
        return success
    
    def get_status(self) -> dict:
        """获取管理器状态"""
        return {
            'running': self._running,
            'miniqmt_alive': self.is_miniqmt_alive(),
            'last_heartbeat': self._last_heartbeat,
            'consecutive_failures': self._consecutive_failures,
            'heartbeat_interval': self._heartbeat_interval,
            'rpc_port': self._rpc_port,
        }


# 全局实例
miniqmt_manager = MiniQMTManager()


def ensure_minqmt_running() -> bool:
    """确保 MiniQMT 在运行（便捷函数）"""
    if not miniqmt_manager.is_running():
        return miniqmt_manager.start()
    return miniqmt_manager.is_miniqmt_alive()
