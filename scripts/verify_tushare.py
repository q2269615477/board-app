#!/usr/bin/env python
"""验证 Tushare 东财板块数据获取"""
import os
import sys
from pathlib import Path

# 设置 Tushare Token
os.environ['TUSHARE_TOKEN'] = 'cbd6784d7c5e87d4e5c935983a17a56367bb1a4b589bbc7768c25590'

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.board_api import get_industry_boards, get_concept_boards

print('='*60)
print('验证东财板块数据获取 (Tushare)')
print('='*60)

# 获取行业板块
print('\n[1] 行业板块:')
industry = get_industry_boards()
if industry is not None:
    print(f'✓ 成功获取 {len(industry)} 个行业板块')
    print(industry.head(3).to_string())
else:
    print('✗ 行业板块获取失败')

# 获取概念板块
print('\n[2] 概念板块:')
concept = get_concept_boards()
if concept is not None:
    print(f'✓ 成功获取 {len(concept)} 个概念板块')
    print(concept.head(3).to_string())
else:
    print('✗ 概念板块获取失败')

# 汇总
print('\n' + '='*60)
print('汇总')
print('='*60)
if industry is not None and concept is not None:
    total = len(industry) + len(concept)
    print(f'✓ 总计: {total} 个板块 (行业 {len(industry)} + 概念 {len(concept)})')
    print('✓ Tushare 东财板块数据获取正常')
else:
    print('✗ 部分数据获取失败')
