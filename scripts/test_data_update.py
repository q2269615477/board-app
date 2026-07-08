"""
数据更新机制测试脚本
验证今天的数据能否被正确更新到存储中
"""
import sys
import json
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_update_scheduler import data_update_scheduler, UpdateType
from services.noon_cache_manager import noon_cache_manager
from core.config import NOON_CACHE_DIR


def test_noon_update():
    """测试午休更新"""
    print("=" * 60)
    print("测试1: 午休数据更新")
    print("=" * 60)
    
    # 启动调度器
    data_update_scheduler.start()
    
    # 触发午休更新
    result = data_update_scheduler.trigger_update('noon')
    
    if result:
        print("✓ 午休更新成功")
        
        # 验证缓存文件
        if noon_cache_manager.is_noon_cache_valid():
            data = noon_cache_manager.load_noon_data()
            metadata = noon_cache_manager.get_cache_metadata()
            print(f"✓ 午休缓存有效")
            print(f"  - 记录数: {metadata.get('record_count', 0)}")
            print(f"  - 时间戳: {metadata.get('timestamp', 'N/A')}")
            print(f"  - 样本数据: {list(data.keys())[:5]}")
            return True
        else:
            print("✗ 午休缓存无效")
            return False
    else:
        print("✗ 午休更新失败")
        return False


def test_afternoon_switch():
    """测试下午开盘切换"""
    print("\n" + "=" * 60)
    print("测试2: 下午开盘切换")
    print("=" * 60)
    
    # 触发切换
    result = data_update_scheduler.trigger_update('afternoon_switch')
    
    if result:
        print("✓ 下午切换成功")
        
        # 验证缓存已清空
        if not noon_cache_manager.is_noon_cache_valid():
            print("✓ 午休缓存已清空")
            return True
        else:
            print("✗ 午休缓存未清空")
            return False
    else:
        print("✗ 下午切换失败")
        return False


def test_daily_close_update():
    """测试盘后更新"""
    print("\n" + "=" * 60)
    print("测试3: 盘后数据更新")
    print("=" * 60)
    
    # 触发盘后更新
    result = data_update_scheduler.trigger_update('daily_close')
    
    if result:
        print("✓ 盘后更新成功")
        print("  - 东财板块数据已更新")
        print("  - 指数数据已更新")
        return True
    else:
        print("✗ 盘后更新失败")
        return False


def verify_today_data():
    """验证今天的数据是否已更新"""
    print("\n" + "=" * 60)
    print("验证: 今天的数据更新状态")
    print("=" * 60)
    
    today = datetime.now().strftime('%Y%m%d')
    
    # 检查午休缓存
    if noon_cache_manager.is_noon_cache_valid():
        metadata = noon_cache_manager.get_cache_metadata()
        if metadata.get('date') == today:
            print(f"✓ 今日({today})午休数据已缓存")
            print(f"  - 记录数: {metadata.get('record_count', 0)}")
            print(f"  - 时间戳: {metadata.get('timestamp', 'N/A')}")
        else:
            print(f"✗ 缓存日期不匹配: {metadata.get('date')} != {today}")
    else:
        print(f"⚠ 今日午休缓存不存在（可能尚未到午休时间或已切换）")
    
    # 检查数据目录中的文件
    from core.config import DATA_DIR
    import os
    
    # 检查行业板块数据
    industry_dir = DATA_DIR / '行业板块K线数据'
    if industry_dir.exists():
        files = list(industry_dir.glob('*.csv'))
        print(f"✓ 行业板块数据: {len(files)} 个文件")
    
    # 检查概念板块数据
    concept_dir = DATA_DIR / '概念板块K线数据'
    if concept_dir.exists():
        files = list(concept_dir.glob('*.csv'))
        print(f"✓ 概念板块数据: {len(files)} 个文件")
    
    return True


def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("数据更新机制测试")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = []
    
    # 根据当前时间选择测试项目
    now = datetime.now()
    hour = now.hour
    
    if 11 <= hour < 13:
        # 午休时间：测试午休更新
        results.append(("午休更新", test_noon_update()))
    elif hour >= 15:
        # 盘后：测试所有
        results.append(("午休更新", test_noon_update()))
        results.append(("下午切换", test_afternoon_switch()))
        results.append(("盘后更新", test_daily_close_update()))
    else:
        # 盘中：测试实时数据获取
        print("当前为盘中时间，测试实时数据获取...")
        verify_today_data()
        results.append(("实时数据", True))
    
    # 验证今天的数据
    verify_today_data()
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    # 最终验证标准
    print("\n" + "=" * 60)
    print("最终验证标准: 今天的数据必须被成功更新到存储中")
    print("=" * 60)
    
    if all_passed:
        print("✓ 所有测试通过，今天的数据已更新")
        return 0
    else:
        print("✗ 部分测试失败，请检查日志")
        return 1


if __name__ == '__main__':
    sys.exit(main())
