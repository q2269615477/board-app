#!/usr/bin/env python
"""排查 MiniQMT 自启动问题"""
import os
import sys
import time
import subprocess
from pathlib import Path

os.environ['TUSHARE_TOKEN'] = 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print('='*70)
print('MiniQMT 问题排查')
print(f'时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
print('='*70)

# 1. 检查 MiniQMT 进程是否存在
print('\n[1] 检查 MiniQMT 进程:')
result = subprocess.run(
    ['tasklist', '/FI', 'IMAGENAME eq XtMiniQmt.exe'],
    capture_output=True, text=True
)
if 'XtMiniQmt.exe' in result.stdout:
    print('  ✓ MiniQMT 进程存在')
    # 提取 PID
    for line in result.stdout.split('\n'):
        if 'XtMiniQmt.exe' in line:
            print(f'  {line.strip()}')
else:
    print('  ✗ MiniQMT 进程不存在')

# 2. 检查端口 58600
print('\n[2] 检查 RPC 端口 58600:')
import socket
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    result = sock.connect_ex(('127.0.0.1', 58600))
    sock.close()
    if result == 0:
        print('  ✓ 端口 58600 可连接')
    else:
        print(f'  ✗ 端口 58600 无法连接 (错误码: {result})')
except Exception as e:
    print(f'  ✗ 检查端口时出错: {e}')

# 3. 检查服务状态
print('\n[3] 检查 MiniQMT 服务状态:')
from services.miniqmt_service import miniqmt_service
status = miniqmt_service.get_status()
for key, value in status.items():
    print(f'  {key}: {value}')

# 4. 检查文件路径
print('\n[4] 检查 MiniQMT 文件路径:')
from core.config import QMT_MINI_PATH, QMT_DIR
mini_path = Path(QMT_MINI_PATH)
qmt_dir = Path(QMT_DIR)
print(f'  配置路径: {mini_path}')
print(f'  文件存在: {mini_path.exists()}')
print(f'  工作目录: {qmt_dir}')
print(f'  目录存在: {qmt_dir.exists()}')

# 5. 手动测试启动
print('\n[5] 手动测试启动 MiniQMT:')
if not status['process_alive']:
    print('  尝试手动启动...')
    try:
        # 使用 Windows 后台启动
        creation_flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS |
            subprocess.CREATE_NO_WINDOW
        )
        proc = subprocess.Popen(
            [str(mini_path), '-background'],
            cwd=str(qmt_dir),
            creationflags=creation_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f'  ✓ 进程已启动 (PID: {proc.pid})')
        print('  等待 5 秒检测端口...')
        time.sleep(5)
        
        # 再次检测端口
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 58600))
        sock.close()
        if result == 0:
            print('  ✓ 端口 58600 已就绪')
        else:
            print(f'  ⚠ 端口仍无法连接，可能启动中...')
    except Exception as e:
        print(f'  ✗ 启动失败: {e}')
else:
    print('  MiniQMT 已在运行，跳过启动测试')

# 6. 检查服务线程
print('\n[6] 检查服务线程:')
import threading
print(f'  监控线程存活: {miniqmt_service._monitor_thread.is_alive() if miniqmt_service._monitor_thread else False}')
print(f'  看门狗线程存活: {miniqmt_service._watchdog_thread.is_alive() if miniqmt_service._watchdog_thread else False}')

print('\n' + '='*70)
print('排查完成')
print('='*70)
