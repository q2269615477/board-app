#!/usr/bin/env python
"""
每周板块成分股全量更新脚本
从 Tushare dc_member 拉取所有板块最新成分股，更新本地 JSON 缓存
支持断点续传、进度追踪、差异报告

用法:
  python scripts/update_constituents.py                    # 全量更新
  python scripts/update_constituents.py --dry-run          # 仅检查差异，不写入
  python scripts/update_constituents.py --resume           # 从上次中断处继续
  python scripts/update_constituents.py --board=BK1170     # 仅更新指定板块
"""
import sys
import os
import json
import time
import re
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tushare as ts

TOKEN = os.environ.get('TUSHARE_TOKEN')
try:
    ts.set_token(TOKEN)
except PermissionError:
    pass

pro = ts.pro_api()

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'
STATE_FILE = DATA_DIR / 'constituents_update_state.json'


def _clean_stock_name(name: str) -> str:
    """清洗股票名称：去掉除权除息临时前缀 XD/DR（保留 ST/*ST）"""
    if not name:
        return name
    return re.sub(r'^(XD|DR)', '', name)


def load_cache(board_type: str) -> dict:
    fname = DATA_DIR / f'{board_type}_constituents.json'
    if fname.exists():
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(board_type: str, data: dict):
    fname = DATA_DIR / f'{board_type}_constituents.json'
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_constituents(sector_code: str) -> Optional[list]:
    """从 Tushare dc_member 获取最新成分股列表"""
    ts_code = f'{sector_code}.DC'
    try:
        time.sleep(0.35)  # Tushare 每分钟限流 200 次
        df = pro.dc_member(ts_code=ts_code)
        if df is None or df.empty:
            return None
        latest_date = df['trade_date'].max()
        df = df[df['trade_date'] == latest_date]
        stocks = []
        for _, row in df.iterrows():
            con_code = str(row.get('con_code', ''))
            if con_code:
                scode = con_code.split('.')[0]
                stocks.append({
                    'code': scode,
                    'name': _clean_stock_name(str(row.get('name', ''))),
                })
        return stocks
    except Exception as e:
        print(f'    [错误] {sector_code}: {e}')
        return None


def compare_stocks(old: list, new: list) -> dict:
    old_set = {(s['code'], s.get('name', '')) for s in old}
    new_set = {(s['code'], s.get('name', '')) for s in new}
    added = new_set - old_set
    removed = old_set - new_set
    return {
        'added': [{'code': c, 'name': n} for c, n in sorted(added)],
        'removed': [{'code': c, 'name': n} for c, n in sorted(removed)],
    }


def update_all(dry_run: bool = False, resume: bool = False,
               target_board: Optional[str] = None):
    """主流程：更新所有板块成分股"""
    
    state = load_state() if resume else {}
    
    # 加载各数据源的板块列表
    industry_cache = load_cache('industry')
    concept_cache = load_cache('concept')
    
    all_boards = []
    for key, entry in industry_cache.items():
        code = key.split(':')[1] if ':' in key else key
        all_boards.append(('industry', key, code, entry.get('name', '?')))
    for key, entry in concept_cache.items():
        code = key.split(':')[1] if ':' in key else key
        all_boards.append(('concept', key, code, entry.get('name', '?')))
    
    if target_board:
        all_boards = [b for b in all_boards if b[2] == target_board]
        if not all_boards:
            print(f'未找到板块: {target_board}')
            return
    
    total = len(all_boards)
    stats = {
        'started_at': datetime.now().isoformat(),
        'total': total,
        'updated': 0, 'unchanged': 0, 'skipped': 0, 'errors': 0,
        'changes': [],
    }
    
    start_idx = state.get('last_idx', 0) if resume else 0
    
    print(f'{"="*60}')
    print(f'板块成分股全量更新')
    print(f'  {"预览模式" if dry_run else "正式模式"} | '
          f'板块总数: {total} | '
          f'{"从第" + str(start_idx+1) + "个继续" if start_idx > 0 else "全新开始"}')
    print(f'  开始时间: {stats["started_at"]}')
    print(f'{"="*60}\n')
    
    for idx in range(start_idx, total):
        board_type, key, code, name = all_boards[idx]
        progress = f'[{idx+1}/{total}]'
        percent = (idx + 1) / total * 100
        
        # 取最新成分股
        stocks = fetch_constituents(code)
        
        if stocks is None:
            stats['errors'] += 1
            print(f'{progress} {key} ({name}): 获取失败')
            continue
        
        # 对比差异
        cache_entry = (industry_cache if board_type == 'industry' else concept_cache)[key]
        old_stocks = cache_entry.get('cons', [])
        diff = compare_stocks(old_stocks, stocks)
        
        if diff['added'] or diff['removed']:
            status = '✗'
            stats['updated'] += 1
            detail = f'{progress} {status} {name}({code}): '
            detail += f'{len(old_stocks)}→{len(stocks)}只'
            if diff['added']:
                detail += f' +{len(diff["added"])}'
            if diff['removed']:
                detail += f' -{len(diff["removed"])}'
            print(detail)
            
            for s in diff['removed']:
                print(f'        - {s["code"]} {s["name"]}')
            for s in diff['added']:
                print(f'        + {s["code"]} {s["name"]}')
            
            stats['changes'].append({
                'board': key, 'name': name, 'old_count': len(old_stocks),
                'new_count': len(stocks), **diff
            })
            
            # 更新缓存
            if not dry_run:
                cache_entry['cons'] = stocks
                cache_entry['updated_at'] = datetime.now().isoformat()
        else:
            stats['unchanged'] += 1
            if idx % 20 == 0:
                print(f'{progress} ✓ {name}({code}): {len(stocks)}只 无变化')
        
        # 每50个板块保存一次（断点续传）
        if not dry_run and (idx + 1) % 50 == 0:
            save_cache('industry', industry_cache)
            save_cache('concept', concept_cache)
            state['last_idx'] = idx + 1
            state['last_at'] = datetime.now().isoformat()
            save_state(state)
            print(f'  [保存进度] {idx+1}/{total} ({percent:.0f}%)\n')
    
    # 最终保存
    if not dry_run:
        save_cache('industry', industry_cache)
        save_cache('concept', concept_cache)
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        stats['finished_at'] = datetime.now().isoformat()
    
    # ===== 汇总报告 =====
    stats['finished_at'] = datetime.now().isoformat()
    print(f'\n{"="*60}')
    print(f'更新汇总')
    print(f'{"="*60}')
    print(f'  总数: {stats["total"]}')
    print(f'  ✓ 无变化: {stats["unchanged"]}')
    print(f'  ✗ 有更新: {stats["updated"]}')
    print(f'  ✕ 错误: {stats["errors"]}')
    print(f'  完成时间: {stats["finished_at"]}')
    
    if stats['changes']:
        print(f'\n变更详情:')
        for ch in stats['changes']:
            name, old_n, new_n = ch['name'], ch['old_count'], ch['new_count']
            added_n, removed_n = len(ch['added']), len(ch['removed'])
            details = []
            if added_n:
                details.append(f'+{added_n}新增')
            if removed_n:
                details.append(f'-{removed_n}移除')
            print(f'  {ch["board"]} ({name}): {old_n}→{new_n}只 ({", ".join(details)})')
    
    # 保存变更记录
    report_file = DATA_DIR / f'constituents_update_{datetime.now().strftime("%Y%m%d")}.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f'\n变更报告已保存: {report_file}')
    
    return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='每周板块成分股全量更新')
    parser.add_argument('--dry-run', action='store_true', help='仅检查差异，不写入')
    parser.add_argument('--resume', action='store_true', help='从上次中断处继续')
    parser.add_argument('--board', type=str, help='仅更新指定板块代码（如 BK1170）')
    args = parser.parse_args()
    
    update_all(dry_run=args.dry_run, resume=args.resume, target_board=args.board)
