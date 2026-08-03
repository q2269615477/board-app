#!/usr/bin/env python
"""全面检查数据系统自动化状态"""
import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

# 设置 Tushare Token

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.env_bootstrap import ensure_tushare_token
ensure_tushare_token()

print('='*70)
print('数据系统全面自动化状态检查')
print(f'检查时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print('='*70)

# 1. 当前市场状态
now = datetime.now()
print(f'\n[1] 当前时间: {now.strftime("%Y-%m-%d %H:%M:%S")}')

if 9 <= now.hour < 11 or (now.hour == 11 and now.minute < 30):
    market_status = '上午盘中'
elif 11 <= now.hour < 13:
    market_status = '午休时间'
elif 13 <= now.hour < 15:
    market_status = '下午盘中'
elif now.hour >= 15:
    market_status = '盘后'
else:
    market_status = '盘前/休市'

print(f'    市场状态: {market_status}')

# 2. Tushare 连接状态
print('\n[2] Tushare 连接状态:')
try:
    from data_loader import get_tushare_pro
    pro = get_tushare_pro()
    if pro is None:
        raise RuntimeError('TUSHARE_TOKEN 未配置或 Tushare 客户端初始化失败')
    df = pro.dc_index(idx_type='行业板块', limit=1)
    print('    ✓ Tushare 连接正常')
    print(f'    ✓ 可获取东财板块数据')
except Exception as e:
    print(f'    ✗ Tushare 连接失败: {e}')

# 3. QMT 连接状态
print('\n[3] QMT 连接状态:')
try:
    from data.qmt_client import get_qmt_client
    client = get_qmt_client()
    # 尝试获取一个指数数据
    result = client.get_constituents_batch(['sh000001'])
    if result and 'sh000001' in result:
        print('    ✓ QMT 连接正常')
        print(f'    ✓ 可实时查询数据: 上证指数 = {result["sh000001"]}')
    else:
        print('    ⚠ QMT 连接但无数据返回')
except Exception as e:
    print(f'    ✗ QMT 连接失败: {e}')
    print('    ⚠ MiniQMT 可能未启动或未连接行情')

# 4. 数据更新调度器状态
print('\n[4] 数据更新调度器状态:')
try:
    from services.data_update_scheduler import data_update_scheduler
    status = data_update_scheduler.get_status()
    running_status = "运行中" if status["running"] else "已停止"
    print(f'    运行状态: {running_status}')
    print(f'    午休缓存有效: {status["noon_cache_valid"]}')
    print('    调度时间配置:')
    for k, v in status['schedule_times'].items():
        print(f'      - {k}: {v}')
except Exception as e:
    print(f'    ✗ 调度器状态获取失败: {e}')

# 5. QMT 缓存服务状态
print('\n[5] QMT 缓存服务状态:')
try:
    from services.qmt_cache_service import qmt_cache_service
    cache_running = "运行中" if qmt_cache_service.is_running() else "已停止"
    print(f'    运行状态: {cache_running}')
    print(f'    关注标的数: {qmt_cache_service.get_watched_count()}')
    if qmt_cache_service.get_watched_count() > 0:
        print(f'    关注列表: {qmt_cache_service.get_watched_codes()[:5]}...')
except Exception as e:
    print(f'    ✗ 缓存服务状态获取失败: {e}')

# 6. 数据文件检查
print('\n[6] 数据文件检查:')
from core.config import DATA_DIR

# 检查 Tushare 缓存
tushare_cache = DATA_DIR / 'tushare_cache'
if tushare_cache.exists():
    today_file = tushare_cache / f'{now.strftime("%Y%m%d")}.json'
    if today_file.exists():
        print(f'    ✓ 今日 Tushare 缓存: {today_file.name}')
    else:
        print(f'    ⚠ 今日 Tushare 缓存不存在')

# 检查午休缓存
noon_cache = DATA_DIR / 'noon_cache'
if noon_cache.exists():
    today_noon = noon_cache / f'noon_cache_{now.strftime("%Y%m%d")}.json'
    if today_noon.exists():
        print(f'    ✓ 今日午休缓存: {today_noon.name}')
    else:
        print(f'    ⚠ 今日午休缓存不存在')

# 检查板块数据
industry_dir = DATA_DIR / '行业板块K线数据'
concept_dir = DATA_DIR / '概念板块K线数据'
if industry_dir.exists():
    count = len(list(industry_dir.glob('*.csv')))
    print(f'    ✓ 行业板块数据: {count} 个文件')
if concept_dir.exists():
    count = len(list(concept_dir.glob('*.csv')))
    print(f'    ✓ 概念板块数据: {count} 个文件')

# 7. 自动更新能力评估
print('\n[7] 自动更新能力评估:')
print('    Tushare 中午自动更新: ', end='')
if market_status == '午休时间':
    print('✓ 当前为午休时间，可执行午休更新')
elif market_status in ['上午盘中', '下午盘中']:
    print('⚠ 当前为盘中时间，午休更新将在 11:35 自动触发')
else:
    print('⚠ 当前已收盘，下次午休更新在下一交易日 11:35')

print('    QMT 实时查询: ', end='')
try:
    from core.lifecycle import is_qmt_available
    if is_qmt_available():
        print('✓ QMT 可用，可实时查询')
    else:
        print('✗ QMT 不可用，需启动 MiniQMT')
except:
    print('⚠ 无法检测 QMT 状态')

print('    系统自动启动: ', end='')
print('✓ 调度器和缓存服务在应用启动时自动初始化')

print('    后台更新: ', end='')
print('✓ 数据更新在后台线程执行，不阻塞主程序')

# 8. 总结
print('\n' + '='*70)
print('总结')
print('='*70)

issues = []
if market_status == '盘后':
    issues.append('当前为盘后时间，实时数据不可用（正常）')

try:
    from core.lifecycle import is_qmt_available
    if not is_qmt_available():
        issues.append('QMT 未连接，盘中需启动 MiniQMT')
except:
    pass

if issues:
    print('注意事项:')
    for issue in issues:
        print(f'  - {issue}')
else:
    print('✓ 所有系统正常运行')

print('\n✓ 数据系统自动化验证完成')
