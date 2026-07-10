#!/usr/bin/env python
"""测试修复后的 MiniQMT 服务"""
import os
import sys
import time
from pathlib import Path

os.environ['TUSHARE_TOKEN'] = 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.miniqmt_service import miniqmt_service

print('='*70)
print('MiniQMT 服务修复测试')
print(f'时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
print('='*70)

# 1. 初始状态
print('\n[1] 初始状态:')
status = miniqmt_service.get_status()
print(f'  运行: {status["running"]}')
print(f'  进程存活: {status["process_alive"]}')

# 2. 启动服务
print('\n[2] 启动服务:')
if not miniqmt_service.is_healthy():
    miniqmt_service.set_mode('application')
    result = miniqmt_service.start()
    print(f'  启动结果: {result}')
    
    if result:
        print('  等待服务初始化...')
        time.sleep(3)
        
        # 检查线程
        print(f'  监控线程存活: {miniqmt_service._monitor_thread.is_alive() if miniqmt_service._monitor_thread else False}')
        print(f'  看门狗线程存活: {miniqmt_service._watchdog_thread.is_alive() if miniqmt_service._watchdog_thread else False}')
else:
    print('  服务已在运行')

# 3. 监控 30 秒
print('\n[3] 监控 30 秒:')
for i in range(6):
    time.sleep(5)
    status = miniqmt_service.get_status()
    healthy = "✓健康" if miniqmt_service.is_healthy() else "✗异常"
    print(f'  {time.strftime("%H:%M:%S")} - {healthy} - '
          f'运行={status["running"]}, 进程={status["process_alive"]}, '
          f'失败={status["consecutive_failures"]}')

# 4. 最终状态
print('\n[4] 最终状态:')
status = miniqmt_service.get_status()
for key, value in status.items():
    print(f'  {key}: {value}')

print('\n' + '='*70)
print('修复内容:')
print('  ✓ 服务启动时验证线程真的启动')
print('  ✓ 监控循环异常捕获，不退出')
print('  ✓ 增加连续错误恢复机制')
print('  ✓ 进程启动等待时间增加到 30 秒')
print('  ✓ 看门狗持续监控监控线程')
print('='*70)
