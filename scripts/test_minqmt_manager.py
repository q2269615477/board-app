#!/usr/bin/env python
"""测试 MiniQMT 常驻管理器"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.miniqmt_manager import miniqmt_manager, ensure_minqmt_running

print('='*70)
print('MiniQMT 常驻管理器测试')
print(f'测试时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
print('='*70)

# 1. 初始状态
print('\n[1] 初始状态检查:')
status = miniqmt_manager.get_status()
print(f'    管理器运行: {status["running"]}')
print(f'    MiniQMT存活: {status["miniqmt_alive"]}')
print(f'    RPC端口: {status["rpc_port"]}')

# 2. 启动管理器
print('\n[2] 启动 MiniQMT 管理器:')
if not miniqmt_manager.is_running():
    result = miniqmt_manager.start()
    result_text = "成功" if result else "失败"
    print(f'    启动结果: {result_text}')
else:
    print('    管理器已在运行')

# 3. 等待并检测
print('\n[3] 等待服务就绪 (10秒)...')
time.sleep(10)

status = miniqmt_manager.get_status()
print(f'    MiniQMT存活: {status["miniqmt_alive"]}')
print(f'    最后心跳: {status["last_heartbeat"]}')

# 4. 便捷函数测试
print('\n[4] 测试便捷函数 ensure_minqmt_running():')
result = ensure_minqmt_running()
result_text = "运行中" if result else "未运行"
print(f'    结果: {result_text}')

# 5. 状态监控
print('\n[5] 状态监控 (持续 30 秒，每 5 秒检测一次):')
for i in range(6):
    time.sleep(5)
    status = miniqmt_manager.get_status()
    alive_status = "✓存活" if status["miniqmt_alive"] else "✗无响应"
    timestamp = time.strftime("%H:%M:%S")
    failures = status["consecutive_failures"]
    print(f'    {timestamp} - {alive_status} - 失败次数:{failures}')

# 6. 最终状态
print('\n[6] 最终状态:')
status = miniqmt_manager.get_status()
print(f'    管理器运行: {status["running"]}')
print(f'    MiniQMT存活: {status["miniqmt_alive"]}')
print(f'    心跳间隔: {status["heartbeat_interval"]} 秒')
print(f'    连续失败: {status["consecutive_failures"]} 次')

print('\n' + '='*70)
print('测试完成')
print('='*70)
print('说明:')
print('- 管理器会持续运行，每 30 秒检测一次 MiniQMT 状态')
print('- 如果连续 3 次检测失败，会自动重启 MiniQMT')
print('- 重启后有 60 秒冷却时间，防止频繁重启')
print('='*70)
