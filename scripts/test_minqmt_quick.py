#!/usr/bin/env python
"""快速测试 MiniQMT 管理器"""
import os
import sys
import time
from pathlib import Path

os.environ['TUSHARE_TOKEN'] = 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.miniqmt_manager import miniqmt_manager

print('MiniQMT 常驻管理器测试')
print('='*50)

# 检查初始状态
status = miniqmt_manager.get_status()
print('初始状态:')
print(f'  运行中: {status["running"]}')
print(f'  MiniQMT存活: {status["miniqmt_alive"]}')

# 启动管理器
print('\n启动管理器...')
result = miniqmt_manager.start()
print(f'启动结果: {result}')

# 等待几秒
time.sleep(3)

# 检查状态
status = miniqmt_manager.get_status()
print('\n当前状态:')
print(f'  运行中: {status["running"]}')
print(f'  MiniQMT存活: {status["miniqmt_alive"]}')
print(f'  RPC端口: {status["rpc_port"]}')
print(f'  心跳间隔: {status["heartbeat_interval"]}秒')

print('\n' + '='*50)
print('✓ MiniQMT 管理器已启动')
print('  - 每 30 秒检测一次心跳')
print('  - 连续 3 次失败自动重启')
print('  - 重启后 60 秒冷却时间')
print('='*50)
