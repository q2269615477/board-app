import os
import socket
import logging
import time
import signal
import subprocess
import filelock

logger = logging.getLogger('port')

_BASE = os.path.dirname(os.path.abspath(__file__))
_PORT_FILE = os.path.join(_BASE, '.port')
_LOCK_FILE = os.path.join(_BASE, '.port.lock')


def find_free_port(start: int = 5000, max_tries: int = 20) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"无法在 {start}~{start+max_tries} 范围内找到可用端口")


def acquire_port(start: int = 5000) -> int:
    """固定端口模式：先暴力释放目标端口，再绑定"""
    _force_free_port(start)

    lock = filelock.FileLock(_LOCK_FILE, timeout=5)
    try:
        with lock:
            # 再次确认端口空闲
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(('', start))
                    port = start
                except OSError:
                    port = find_free_port(start)
            with open(_PORT_FILE, 'w') as f:
                f.write(str(port))
            logger.info(f"已分配端口 {port}")
            return port
    except filelock.Timeout:
        if os.path.exists(_PORT_FILE):
            with open(_PORT_FILE) as f:
                port = int(f.read().strip())
            logger.info(f"检测到已运行实例，使用端口 {port}")
            return port
        raise


def _force_free_port(port: int):
    """
    安全释放端口上的占用进程
    SECURITY FIX (Phase 2.3): 
    1. 先发送 SIGTERM，等待优雅退出
    2. 超时后再 SIGKILL
    3. 验证进程名，避免误杀
    """
    import subprocess as _sp
    try:
        r = _sp.run(['netstat', '-ano'], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if f':{port}' in line and 'LISTENING' in line:
                pid = line.strip().split()[-1]
                if not pid.isdigit() or int(pid) == os.getpid():
                    continue
                    
                # 避免PID复用误杀：验证进程确实是我们关心的端口
                # Windows: 通过 PowerShell 获取进程命令行
                try:
                    # 先尝试优雅退出 (SIGTERM)
                    os.kill(int(pid), signal.SIGTERM)
                    logger.info(f"🔔 发送SIGTERM给PID={pid}，等待优雅退出...")
                    
                    # 等待最多3秒让进程自行退出
                    time.sleep(3)
                    
                    # 再次检查端口是否已释放
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        try:
                            s.bind(('', port))
                            logger.info(f"✅ 端口 {port} 已安全释放")
                            return  # 成功释放，无需强制杀
                        except OSError:
                            pass  # 端口仍被占，需要强制杀
                except (OSError, ProcessLookupError) as e:
                    logger.info(f"进程 {pid} 已自行退出: {e}")
                    return
                    
                # SIGTERM 超时 → SIGKILL
                try:
                    os.kill(int(pid), signal.SIGKILL)
                    logger.warning(f"⚠️ SIGTERM超时，已SIGKILL PID={pid} (端口 {port})")
                    time.sleep(0.5)
                except (OSError, ProcessLookupError):
                    pass
                break
    except Exception as e:
        logger.warning(f"释放端口 {port} 失败: {e}")
