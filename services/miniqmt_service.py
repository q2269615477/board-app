"""
MiniQMT Windows 服务包装器
三种启动模式：
1. 开机启动模式 - 注册为 Windows 开机任务
2. 后台启动模式 - 无窗口后台运行
3. 应用托管模式 - AI炒股面板托管，带持续保障

特性：
- 后台运行，不显示桌面窗口
- 心跳机制持续监控
- 自动重启保障
- 与应用生命周期解耦
"""
import os
import sys
import time
import logging
import threading
import subprocess
import ctypes
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from core.config import QMT_MINI_PATH, QMT_DIR, QMT_PORTS

logger = logging.getLogger('miniqmt_service')


class MiniQMTService:
    """
    MiniQMT 服务管理器
    支持三种模式：开机启动、后台启动、应用托管
    """
    
    # 单例模式
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
        self._rpc_port = QMT_PORTS[0]
        
        # 服务状态
        self._running = False
        self._mode = 'application'  # 'boot', 'background', 'application'
        
        # 监控线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # 心跳参数
        self._heartbeat_interval = 30  # 30秒
        self._max_failures = 3
        self._consecutive_failures = 0
        self._last_heartbeat = 0
        self._last_restart = 0
        self._restart_cooldown = 60
        
        # 看门狗参数
        self._watchdog_interval = 60  # 60秒检查一次管理器本身
        
        # 回调函数
        self._on_status_change: Optional[Callable] = None
        
    # ==================== 模式配置 ====================
    
    def set_mode(self, mode: str):
        """设置运行模式: 'boot' | 'background' | 'application'"""
        if mode not in ('boot', 'background', 'application'):
            raise ValueError(f"无效模式: {mode}")
        self._mode = mode
        logger.info(f"[MiniQMT服务] 模式设置为: {mode}")
    
    # ==================== 核心控制 ====================
    
    def start(self) -> bool:
        """启动 MiniQMT 服务"""
        if self._running:
            logger.info("[MiniQMT服务] 已在运行")
            return True
        
        if not self._mini_path.exists():
            logger.error(f"[MiniQMT服务] 找不到: {self._mini_path}")
            return False
        
        self._running = True
        self._stop_event.clear()
        self._consecutive_failures = 0
        
        # 启动监控线程
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="MiniQMT-Monitor"
        )
        self._monitor_thread.start()
        
        # 等待监控线程启动
        time.sleep(0.5)
        if not self._monitor_thread.is_alive():
            logger.error("[MiniQMT服务] 监控线程启动失败")
            self._running = False
            return False
        
        # 启动看门狗线程（确保监控线程不死）
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="MiniQMT-Watchdog"
        )
        self._watchdog_thread.start()
        
        # 等待看门狗线程启动
        time.sleep(0.5)
        if not self._watchdog_thread.is_alive():
            logger.error("[MiniQMT服务] 看门狗线程启动失败")
            self._running = False
            return False
        
        logger.info(f"[MiniQMT服务] 已启动，模式: {self._mode}")
        return True
    
    def stop(self):
        """停止 MiniQMT 服务"""
        logger.info("[MiniQMT服务] 正在停止...")
        self._running = False
        self._stop_event.set()
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5)
        
        # 停止 MiniQMT 进程
        self._stop_process()
        
        logger.info("[MiniQMT服务] 已停止")
    
    # ==================== 监控循环 ====================
    
    def _monitor_loop(self):
        """主监控循环 - 心跳检测（强化版，异常不退出）"""
        logger.info("[MiniQMT监控] 循环启动")
        
        # 首次启动（带重试）
        retry_count = 0
        while not self._is_process_running() and retry_count < 3:
            logger.info(f"[MiniQMT监控] 首次启动 MiniQMT... (尝试 {retry_count + 1}/3)")
            if self._start_process():
                break
            retry_count += 1
            time.sleep(2)
        
        # 主循环 - 异常捕获确保不退出
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while not self._stop_event.is_set():
            try:
                self._do_heartbeat()
                consecutive_errors = 0  # 成功则重置错误计数
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"[MiniQMT监控] 异常 ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                # 连续错误过多，尝试重启监控
                if consecutive_errors >= max_consecutive_errors:
                    logger.critical("[MiniQMT监控] 连续错误过多，尝试恢复...")
                    try:
                        if not self._is_process_running():
                            self._auto_restart()
                    except Exception as restart_error:
                        logger.error(f"[MiniQMT监控] 恢复失败: {restart_error}")
                    consecutive_errors = 0
            
            self._stop_event.wait(self._heartbeat_interval)
        
        logger.info("[MiniQMT监控] 循环结束")
    
    def _do_heartbeat(self):
        """执行一次心跳检测"""
        self._last_heartbeat = time.time()
        
        is_alive = self._is_process_running()
        
        if is_alive:
            if self._consecutive_failures > 0:
                logger.info("[MiniQMT心跳] ✓ 服务恢复")
                self._consecutive_failures = 0
                self._notify_status_change('recovered')
        else:
            self._consecutive_failures += 1
            logger.warning(
                f"[MiniQMT心跳] ✗ 无响应 "
                f"({self._consecutive_failures}/{self._max_failures})"
            )
            
            if self._consecutive_failures >= self._max_failures:
                self._notify_status_change('failed')
                self._auto_restart()
    
    def _auto_restart(self):
        """自动重启"""
        elapsed = time.time() - self._last_restart
        if elapsed < self._restart_cooldown:
            logger.info(f"[MiniQMT重启] 冷却中，{self._restart_cooldown - elapsed:.0f}秒后再试")
            return False
        
        logger.error("[MiniQMT重启] 连续失败，执行重启...")
        self._notify_status_change('restarting')
        
        self._stop_process()
        success = self._start_process()
        
        if success:
            self._consecutive_failures = 0
            logger.info("[MiniQMT重启] ✓ 成功")
            self._notify_status_change('restarted')
        else:
            logger.error("[MiniQMT重启] ✗ 失败")
        
        return success
    
    # ==================== 看门狗 ====================
    
    def _watchdog_loop(self):
        """看门狗循环 - 确保监控线程不死"""
        logger.info("[MiniQMT看门狗] 启动")
        
        while not self._stop_event.is_set():
            self._stop_event.wait(self._watchdog_interval)
            
            if self._stop_event.is_set():
                break
            
            # 检查监控线程是否存活
            if self._monitor_thread and not self._monitor_thread.is_alive():
                logger.error("[MiniQMT看门狗] 监控线程死亡，重新启动...")
                self._monitor_thread = threading.Thread(
                    target=self._monitor_loop,
                    daemon=True,
                    name="MiniQMT-Monitor"
                )
                self._monitor_thread.start()
                logger.info("[MiniQMT看门狗] 监控线程已重启")
    
    # ==================== 进程管理 ====================
    
    def _start_process(self) -> bool:
        """启动 MiniQMT 进程（后台模式，无窗口）"""
        try:
            logger.info(f"[MiniQMT进程] 启动: {self._mini_path.name}")
            
            # Windows 后台启动参数
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP |
                subprocess.DETACHED_PROCESS |
                subprocess.CREATE_NO_WINDOW  # 不创建窗口
            )
            
            # 启动进程
            proc = subprocess.Popen(
                [str(self._mini_path), '-background'],
                cwd=str(self._qmt_dir),
                creationflags=creation_flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            
            self._last_restart = time.time()
            logger.info(f"[MiniQMT进程] 已启动 (PID: {proc.pid})")
            
            # 等待服务就绪（增加等待时间）
            logger.info("[MiniQMT进程] 等待服务就绪...")
            for i in range(30):  # 最多等待 30 秒
                time.sleep(1)
                if self._is_process_running():
                    logger.info(f"[MiniQMT进程] ✓ 服务已就绪 (用时 {i+1} 秒)")
                    return True
                if (i + 1) % 10 == 0:
                    logger.info(f"[MiniQMT进程] 等待中... ({i+1}/30 秒)")
            
            logger.warning("[MiniQMT进程] 启动超时，可能仍在初始化")
            return True  # 仍返回 True，让监控线程继续检测
            
        except Exception as e:
            logger.error(f"[MiniQMT进程] 启动失败: {e}")
            return False
    
    def _stop_process(self):
        """停止 MiniQMT 进程"""
        try:
            # 先尝试优雅终止
            subprocess.run(
                ['taskkill', '/IM', 'XtMiniQmt.exe'],
                capture_output=True,
                timeout=5
            )
            time.sleep(2)
            
            # 强制终止残留进程
            subprocess.run(
                ['taskkill', '/F', '/IM', 'XtMiniQmt.exe'],
                capture_output=True,
                timeout=5
            )
            logger.info("[MiniQMT进程] 已停止")
        except Exception as e:
            logger.warning(f"[MiniQMT进程] 停止时出错: {e}")
    
    def _is_process_running(self) -> bool:
        """检测 XtMiniQmt.exe 是否存活。

        注意：58600 也可能被 XtItClient.exe 占用；仅看端口会把
        “仅有 RPC 壳、无行情” 误判为 MiniQMT 正常。
        """
        # 1. 必须看到 XtMiniQmt 进程名（根因判定）
        try:
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq XtMiniQmt.exe'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if 'XtMiniQmt.exe' in (result.stdout or ''):
                return True
        except Exception:
            pass

        # 2. 端口仅作辅助日志，不再单独判定存活
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            port_open = sock.connect_ex(('127.0.0.1', self._rpc_port)) == 0
            sock.close()
            if port_open:
                logger.debug(
                    f"[MiniQMT] 端口 {self._rpc_port} 开放但未见 XtMiniQmt.exe"
                    "（可能是 XtItClient 占用）"
                )
        except Exception:
            pass
        return False
    
    # ==================== 状态通知 ====================
    
    def set_status_callback(self, callback: Callable):
        """设置状态变更回调"""
        self._on_status_change = callback
    
    def _notify_status_change(self, status: str):
        """通知状态变更"""
        if self._on_status_change:
            try:
                self._on_status_change(status)
            except Exception as e:
                logger.warning(f"[MiniQMT服务] 状态回调异常: {e}")
    
    # ==================== 状态查询 ====================
    
    def get_status(self) -> dict:
        """获取服务状态"""
        return {
            'running': self._running,
            'mode': self._mode,
            'process_alive': self._is_process_running(),
            'last_heartbeat': self._last_heartbeat,
            'consecutive_failures': self._consecutive_failures,
            'heartbeat_interval': self._heartbeat_interval,
            'rpc_port': self._rpc_port,
        }
    
    def is_healthy(self) -> bool:
        """健康检查"""
        if not self._running:
            return False
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            return False
        return self._is_process_running()


# 全局服务实例
miniqmt_service = MiniQMTService()


def ensure_service_running(mode: str = 'application') -> bool:
    """便捷函数：确保服务在运行"""
    miniqmt_service.set_mode(mode)
    return miniqmt_service.start()


# ==================== Windows 开机启动配置 ====================

def setup_boot_start():
    """
    配置 Windows 开机启动
    创建计划任务，开机时自动启动 MiniQMT 服务
    """
    import subprocess
    
    script_path = Path(__file__).resolve()
    python_path = sys.executable
    
    # 创建启动脚本
    boot_script = script_path.parent.parent / 'scripts' / 'miniqmt_boot.py'
    boot_script.write_text(f'''#!/usr/bin/env python
import sys
sys.path.insert(0, r'{script_path.parent.parent}')
from services.miniqmt_service import miniqmt_service

# 开机启动模式
miniqmt_service.set_mode('boot')
miniqmt_service.start()

# 保持运行
try:
    import time
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    miniqmt_service.stop()
''', encoding='utf-8')
    
    # 创建计划任务命令
    task_name = "MiniQMT_AutoStart"
    cmd = f'''
    schtasks /Create /F /TN "{task_name}" \\
        /TR "\"{python_path}\" \"{boot_script}\"" \\
        /SC ONLOGON \\
        /RL HIGHEST \\
        /NP
    '''
    
    print(f"[开机启动配置] 创建计划任务: {task_name}")
    print(f"  命令: {cmd}")
    print("\n请以管理员身份运行以下命令:")
    print(cmd)
    
    return boot_script


def remove_boot_start():
    """移除 Windows 开机启动配置"""
    task_name = "MiniQMT_AutoStart"
    cmd = f'schtasks /Delete /F /TN "{task_name}"'
    print(f"[开机启动配置] 移除计划任务: {task_name}")
    print(f"  命令: {cmd}")


if __name__ == '__main__':
    # 测试运行
    logging.basicConfig(level=logging.INFO)
    
    print("MiniQMT 服务测试")
    print("="*50)
    
    # 设置回调
    def on_status(status):
        print(f"[状态变更] {status}")
    
    miniqmt_service.set_status_callback(on_status)
    
    # 启动服务
    miniqmt_service.set_mode('application')
    miniqmt_service.start()
    
    try:
        while True:
            time.sleep(5)
            status = miniqmt_service.get_status()
            print(f"状态: 运行={status['running']}, 进程={status['process_alive']}")
    except KeyboardInterrupt:
        miniqmt_service.stop()
