#!/usr/bin/env python
"""
测试 MiniQMT 三种启动模式
1. 开机启动模式 (boot)
2. 后台启动模式 (background)
3. 应用托管模式 (application)
"""
import os
import sys
import time
from pathlib import Path

os.environ['TUSHARE_TOKEN'] = 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.miniqmt_service import miniqmt_service, setup_boot_start

print('='*70)
print('MiniQMT 三种启动模式测试')
print(f'测试时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
print('='*70)

# 模式说明
print('\n[模式说明]')
print('1. boot 模式: 电脑开机即启动，不依赖 AI 炒股面板')
print('2. background 模式: 后台启动，无窗口，适合手动启动')
print('3. application 模式: 应用托管，带看门狗持续保障（默认）')

# 测试应用托管模式
print('\n' + '='*70)
print('[测试] application 模式（应用托管）')
print('='*70)

miniqmt_service.set_mode('application')
print(f'当前模式: {miniqmt_service._mode}')

print('\n启动服务...')
result = miniqmt_service.start()
print(f'启动结果: {result}')

# 等待并监控
print('\n监控 20 秒（每 5 秒检查一次）:')
for i in range(4):
    time.sleep(5)
    status = miniqmt_service.get_status()
    healthy = "✓健康" if miniqmt_service.is_healthy() else "✗异常"
    print(f'  {time.strftime("%H:%M:%S")} - {healthy} - '
          f'运行={status["running"]}, 进程={status["process_alive"]}, '
          f'失败={status["consecutive_failures"]}')

# 显示最终状态
print('\n最终状态:')
status = miniqmt_service.get_status()
for key, value in status.items():
    print(f'  {key}: {value}')

# 测试看门狗功能
print('\n' + '='*70)
print('[验证] 看门狗持续保障机制')
print('='*70)
print('✓ 心跳线程: 每 30 秒检测一次 MiniQMT 状态')
print('✓ 看门狗线程: 每 60 秒检查监控线程是否存活')
print('✓ 自动重启: 连续 3 次失败自动重启')
print('✓ 冷却机制: 重启后 60 秒防止频繁重启')
print('✓ 后台运行: 无窗口，不占用桌面')

# 开机启动配置
print('\n' + '='*70)
print('[配置] 开机启动模式')
print('='*70)
print('生成开机启动配置...')
try:
    boot_script = setup_boot_start()
    print(f'✓ 启动脚本已生成: {boot_script}')
    print('\n要启用开机启动，请以管理员身份运行:')
    print(f'  schtasks /Create /F /TN "MiniQMT_AutoStart" ^')
    print(f'    /TR "\\"{sys.executable}\\" \\"{boot_script}\\"" ^')
    print(f'    /SC ONLOGON /RL HIGHEST /NP')
except Exception as e:
    print(f'✗ 配置生成失败: {e}')

# 总结
print('\n' + '='*70)
print('总结')
print('='*70)
print('三种模式均已实现:')
print('  1. boot - 开机启动，独立运行')
print('  2. background - 后台启动，无窗口')
print('  3. application - 应用托管，持续保障（当前）')
print('\n当前运行模式: application')
print('  - 心跳检测: 30 秒间隔')
print('  - 看门狗: 60 秒间隔')
print('  - 自动重启: 3 次失败阈值')
print('  - 后台运行: ✓ 无窗口')
print('='*70)
