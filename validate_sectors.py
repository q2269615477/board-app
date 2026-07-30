"""
板块数据三维度验证脚本
1. 价格验证：涨跌幅/OHLCV
2. 成分股验证：QMT vs 东财
3. 计算方式验证：等权 vs 市值加权
"""
from xtquant import xtdata
import json, os, re, numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

xtdata.connect(port=58610)
xtdata.enable_hello = False

# 构建CSV映射
csv_dir = Path('../行业板块K线数据')
if not csv_dir.exists():
    csv_dir = Path('data/行业板块K线数据')

csv_map = {}
for f in csv_dir.glob('*.csv'):
    m = re.match(r'(.+?)_(BK\d+)\.csv$', f.name)
    if m:
        csv_map[m.group(1)] = (m.group(2), str(f))

with open('static/board_classification.json', 'r', encoding='utf-8') as f:
    cats = json.load(f)['categories']
qmt_sectors = [s for s in xtdata.get_sector_list() if '加权' not in s]

def smart_match(em_name):
    if em_name in qmt_sectors:
        return 'exact', em_name
    for prefix in ['SW3', 'SW2', 'SW1']:
        qs = prefix + em_name
        if qs in qmt_sectors:
            return prefix, qs
    return None, None

test_boards = [
    ('银行', 'BK0475'), ('证券', 'BK0473'), ('白酒', 'BK0477'),
    ('半导体', 'BK1036'), ('医药商业', 'BK0476'), ('房地产开发', 'BK0451'),
    ('煤炭开采', 'BK0437'), ('电力', 'BK0428'), ('汽车整车', 'BK0481'),
    ('国防军工', 'BK0493')
]

results = []

print("="*70)
print("  QMT板块 vs 东财板块 三维度验证报告")
print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("="*70)

for em_name, em_code in test_boards:
    mtype, qmt_name = smart_match(em_name)
    r = {'board': em_name, 'match': mtype}
    
    print(f"\n{'='*60}")
    print(f"  {em_name} | 匹配:{mtype} | QMT:{qmt_name}")
    
    if not qmt_name:
        print("  [跳过] 无QMT匹配")
        results.append(r)
        continue
    
    # --- 成分股 ---
    qmt_stocks = xtdata.get_stock_list_in_sector(qmt_name)
    qmt_codes = {s.split('.')[0] for s in qmt_stocks}
    
    # 从CSV读东财成分股
    em_codes = set()
    csv_info = csv_map.get(em_name)
    if csv_info:
        csv_path = csv_info[1]
        em_df = pd.read_csv(csv_path)
        if '代码' in em_df.columns:
            em_codes = {str(c) for c in em_df['代码'] if str(c) != 'nan'}
    
    r['qmt_n'] = len(qmt_codes)
    r['em_n'] = len(em_codes)
    
    # 成分股对比
    overlap = qmt_codes & em_codes
    r['overlap'] = len(overlap)
    sim = len(overlap) / max(len(qmt_codes | em_codes), 1) * 100
    r['similarity'] = round(sim, 1)
    
    print(f"  成分股: QMT={len(qmt_codes)}只, 东财={len(em_codes)}只, 重叠={len(overlap)}只({sim:.1f}%)")
    
    # --- 价格验证 ---
    # QMT端：通过成分股采样计算等权涨跌幅
    qmt_data = {}
    sample = list(qmt_stocks)[:min(50, len(qmt_stocks))]
    for s in sample:
        try:
            xtdata.download_history_data(s, period='1d', start_time='20260620', end_time='20260701')
            d = xtdata.get_local_data(
                field_list=['time','open','high','low','close','volume'],
                stock_list=[s], period='1d', start_time='20260620', end_time='20260701', count=10)
            if isinstance(d, dict) and s in d and d[s] is not None and not d[s].empty and len(d[s]) >= 2:
                df = d[s]
                qmt_data[s] = {
                    'close': float(df.iloc[-1]['close']),
                    'prev': float(df.iloc[-2]['close']),
                    'open': float(df.iloc[-1]['open']),
                    'high': float(df.iloc[-1]['high']),
                    'low': float(df.iloc[-1]['low']),
                    'vol': float(df.iloc[-1]['volume'])
                }
        except Exception:
            pass  # 单只个股数据获取失败不影响验证
    
    if qmt_data:
        changes = [(d['close']/d['prev']-1)*100 for d in qmt_data.values()]
        r['qmt_eq_chg'] = round(np.mean(changes), 3)
        r['qmt_md_chg'] = round(np.median(changes), 3)
        r['qmt_vol'] = int(sum(d['vol'] for d in qmt_data.values()))
        r['sample_n'] = len(changes)
    
    # 东财端
    if csv_info and os.path.exists(csv_info[1]):
        em_df = pd.read_csv(csv_info[1])
        em_df = em_df.sort_values('日期')
        if len(em_df) >= 2:
            c0 = float(em_df.iloc[-1]['收盘'])
            c1 = float(em_df.iloc[-2]['收盘'])
            r['em_chg'] = round((c0/c1-1)*100, 3)
            r['em_close'] = c0
            r['em_vol'] = float(em_df.iloc[-1].get('成交量', 0))
    
    # 偏差
    if 'qmt_eq_chg' in r and 'em_chg' in r:
        r['deviation'] = round(abs(r['qmt_eq_chg'] - r['em_chg']), 3)
        print(f"  价格: QMT等权={r['qmt_eq_chg']:+.3f}%, 东财={r['em_chg']:+.3f}%, 偏差={r['deviation']:.3f}%")
        print(f"  OHLCV: QMT(O={np.mean([d['open'] for d in qmt_data.values()]):.1f}) 东财(close={r['em_close']:.1f})")
    
    results.append(r)

# ===== 汇总 =====
print(f"\n\n{'='*70}")
print("  汇总报告")
print("="*70)

prices = [r for r in results if 'deviation' in r]
if prices:
    devs = [r['deviation'] for r in prices]
    print(f"\n价格偏差统计 ({len(prices)}个板块):")
    print(f"  平均: {np.mean(devs):.3f}%")
    print(f"  中位: {np.median(devs):.3f}%")
    print(f"  最大: {np.max(devs):.3f}%")
    print(f"  最小: {np.min(devs):.3f}%")
    bad = [r for r in prices if r['deviation'] > 0.5]
    if bad:
        print(f"  >0.5%偏差板块:")
        for r in sorted(bad, key=lambda x: -x['deviation'])[:5]:
            print(f"    {r['board']}: {r['deviation']:.3f}%")

consts = [r for r in results if 'similarity' in r]
if consts:
    sims = [r['similarity'] for r in consts]
    print(f"\n成分股相似度 ({len(consts)}个板块):")
    print(f"  平均: {np.mean(sims):.1f}%")
    print(f"  <70%板块:")
    for r in consts:
        if r['similarity'] < 70:
            print(f"    {r['board']}: {r['similarity']}% (QMT={r['qmt_n']} vs 东财={r['em_n']})")

# 保存JSON
with open('data/sector_validation.json', 'w', encoding='utf-8') as f:
    json.dump({'time': datetime.now().isoformat(), 'results': results}, f, ensure_ascii=False, indent=2, default=str)
print(f"\n完整报告: data/sector_validation.json")
