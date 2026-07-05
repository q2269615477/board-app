#!/usr/bin/env python
"""
板块成分股核验脚本 — 用 Tushare dc_member 验证本地缓存的成分股是否正确
抽样检查 N 个板块，报告差异（新增/移除的股票）
用法: python verify_constituents.py [--sample=20] [--fix]
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tushare as ts

# Tushare Token
TOKEN = os.environ.get('TUSHARE_TOKEN', 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590')
try:
    ts.set_token(TOKEN)
except PermissionError:
    pass

pro = ts.pro_api()

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'

def load_cache(board_type):
    fname = DATA_DIR / f'{board_type}_constituents.json'
    if fname.exists():
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def fetch_tushare_constituents(board_type, code):
    """通过 Tushare dc_member 获取最新成分股列表"""
    ts_code = f'{code}.DC'
    try:
        time.sleep(0.35)  # 限流
        df = pro.dc_member(ts_code=ts_code)
        if df is None or df.empty:
            return None
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        stocks = []
        for _, row in df.iterrows():
            con_code = str(row.get('con_code', ''))
            if con_code:
                scode = con_code.split('.')[0] if '.' in con_code else con_code
                stocks.append({
                    'code': scode,
                    'name': str(row.get('name', '')),
                })
        return {'date': latest_date, 'stocks': stocks}
    except Exception as e:
        return {'error': str(e)}

def compare(cache_stocks, tushare_stocks):
    """比较两份成分股列表，返回差异"""
    cache_set = {s['code']: s.get('name', '') for s in cache_stocks}
    ts_set = {s['code']: s.get('name', '') for s in tushare_stocks}
    
    cache_codes = set(cache_set.keys())
    ts_codes = set(ts_set.keys())
    
    added = ts_codes - cache_codes   # Tushare有、缓存无 → 新增
    removed = cache_codes - ts_codes  # 缓存有、Tushare无 → 已移除
    
    diff = []
    for code in added:
        diff.append({'action': 'ADD', 'code': code, 'name': ts_set[code]})
    for code in removed:
        diff.append({'action': 'REMOVE', 'code': code, 'name': cache_set[code]})
    
    return diff

def main():
    parser = argparse.ArgumentParser(description='板块成分股核验')
    parser.add_argument('--sample', type=int, default=30, help='每类抽样板块数 (默认30)')
    parser.add_argument('--fix', action='store_true', help='自动修复差异（更新缓存）')
    args = parser.parse_args()
    
    industry_cache = load_cache('industry')
    concept_cache = load_cache('concept')
    
    results = {'OK': 0, 'DIFF': 0, 'ERROR': 0, 'details': []}
    
    for board_type, cache in [('industry', industry_cache), ('concept', concept_cache)]:
        keys = list(cache.keys())
        import random
        sample = random.sample(keys, min(args.sample, len(keys)))
        
        label = '行业' if board_type == 'industry' else '概念'
        print(f'\n{"="*60}')
        print(f'{label}板块成分股核验 (抽样 {len(sample)}/{len(keys)} 个)')
        print(f'{"="*60}')
        
        for key in sample:
            entry = cache[key]
            cache_cons = entry.get('cons', [])
            board_name = entry.get('name', '?')
            code = key.split(':')[1] if ':' in key else key
            
            ts_data = fetch_tushare_constituents(board_type, code)
            
            if ts_data is None:
                status = '⚠'
                results['ERROR'] += 1
                print(f'  {status} {key} ({board_name}): Tushare 无数据')
                results['details'].append({
                    'key': key, 'name': board_name, 'status': 'TUSHARE_EMPTY',
                    'cache_count': len(cache_cons), 'ts_count': 0
                })
                continue
            
            if 'error' in ts_data:
                status = '✗'
                results['ERROR'] += 1
                print(f'  {status} {key} ({board_name}): {ts_data["error"]}')
                results['details'].append({
                    'key': key, 'name': board_name, 'status': 'TUSHARE_ERROR',
                    'cache_count': len(cache_cons), 'error': ts_data['error']
                })
                continue
            
            ts_stocks = ts_data['stocks']
            diff = compare(cache_cons, ts_stocks)
            
            ts_date = ts_data.get('date', '?')
            
            if not diff:
                results['OK'] += 1
                print(f'  ✓ {key} ({board_name}): {len(ts_stocks)}只 一致 (Tushare日期:{ts_date})')
            else:
                results['DIFF'] += 1
                added = [d for d in diff if d['action'] == 'ADD']
                removed = [d for d in diff if d['action'] == 'REMOVE']
                print(f'  ✗ {key} ({board_name}): 缓存{len(cache_cons)}只 Tushare{len(ts_stocks)}只 (Tushare日期:{ts_date})')
                
                for d in removed:
                    print(f'      - 移除: {d["code"]} {d["name"]}')
                for d in added:
                    print(f'      + 新增: {d["code"]} {d["name"]}')
                
                results['details'].append({
                    'key': key, 'name': board_name, 'status': 'DIFF',
                    'cache_count': len(cache_cons), 'ts_count': len(ts_stocks),
                    'ts_date': ts_date,
                    'added': added, 'removed': removed
                })
                
                # 自动修复
                if args.fix:
                    entry['cons'] = ts_stocks
                    entry['updated_at'] = datetime.now().isoformat()
                    print(f'      → 已更新缓存')
    
    # 打印汇总
    total = args.sample * 2
    print(f'\n{"="*60}')
    print(f'核验汇总')
    print(f'{"="*60}')
    print(f'  ✓ 一致: {results["OK"]}/{total}')
    print(f'  ✗ 差异: {results["DIFF"]}/{total}')
    print(f'  ⚠ 错误: {results["ERROR"]}/{total}')
    
    if results['DIFF'] > 0:
        print(f'\n差异板块列表:')
        for d in results['details']:
            if d.get('status') == 'DIFF':
                print(f'  {d["key"]} ({d["name"]})')
                print(f'    缓存{d["cache_count"]}只 → Tushare{d["ts_count"]}只 (日期:{d.get("ts_date","?")})')
                for a in d.get('added', []):
                    print(f'      + {a["code"]} {a["name"]}')
                for r in d.get('removed', []):
                    print(f'      - {r["code"]} {r["name"]}')
    
    # 自动修复: 保存
    if args.fix and results['DIFF'] > 0:
        for board_type, cache in [('industry', industry_cache), ('concept', concept_cache)]:
            fname = DATA_DIR / f'{board_type}_constituents.json'
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f'\n→ 已保存修复后的缓存文件')
    
    return results

if __name__ == '__main__':
    main()
