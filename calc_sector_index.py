"""
使用QMT成分股数据计算板块指数，与东财CSV对比验证
方案：东财成分股 → QMT拉取 → 市值加权计算 → 对比东财涨跌幅
"""
from xtquant import xtdata
import json, os, re, numpy as np, pandas as pd, time
from pathlib import Path
from core.config import QMT_PORTS

xtdata.connect(port=QMT_PORTS[1])
xtdata.enable_hello = False

# ===== 从东财CSV获取成分股 =====
# 方法：读取所有行业板块CSV，但CSV只有板块级别数据没有成分股
# 所以我们用QMT SW板块的成分股作为近似，但检查涨跌幅是否一致

# 测试板块：银行
test_sectors = {
    '银行': ('BK0475', 'SW3银行'),
    '证券': ('BK0473', 'SW3证券'),
    '保险': ('BK0474', 'SW2保险'),
    '白酒': ('BK0477', 'SW3白酒'),
}

# ===== 东财K线数据（CSV） =====
csv_dir = Path('../行业板块K线数据')
if not csv_dir.exists():
    csv_dir = Path('data/行业板块K线数据')

print("=" * 70)
print("  市值加权板块涨跌幅验证")
print("=" * 70)

for em_name, (em_code, qmt_sector) in test_sectors.items():
    print(f"\n--- {em_name} | QMT={qmt_sector} ---")
    
    # 1. 从QMT获取成分股
    stocks = xtdata.get_stock_list_in_sector(qmt_sector)
    print(f"  QMT成分股: {len(stocks)}只")
    
    # 2. 获取东财板块K线数据
    em_data = None
    for f in csv_dir.glob('*.csv'):
        if em_code in f.name and em_name in f.name:
            em_data = pd.read_csv(str(f))
            break
    
    if em_data is None or len(em_data) < 2:
        print(f"  ⚠️ 无东财对比数据")
        continue
    
    em_close0 = float(em_data.iloc[-1]['收盘'])
    em_close1 = float(em_data.iloc[-2]['收盘'])
    em_chg = (em_close0 / em_close1 - 1) * 100
    print(f"  东财涨跌: {em_chg:+.3f}% (从{em_close1:.1f}到{em_close0:.1f})")
    
    # 3. 从QMT拉取每只成分股的数据（采样）
    qmt_prices = {}
    for s in stocks:
        try:
            xtdata.download_history_data(s, period='1d', start_time='20260620', end_time='20260701')
            d = xtdata.get_local_data(
                field_list=['time','close','volume'],
                stock_list=[s], period='1d',
                start_time='20260620', end_time='20260701', count=10)
            if isinstance(d, dict) and s in d and d[s] is not None and not d[s].empty and len(d[s]) >= 2:
                df = d[s]
                qmt_prices[s] = {
                    'close': float(df.iloc[-1]['close']),
                    'prev_close': float(df.iloc[-2]['close']),
                    'volume': float(df.iloc[-1]['volume'])
                }
        except Exception:
            pass  # 单只个股行情获取失败不影响板块整体计算
    
    if not qmt_prices:
        print(f"  ❌ 无QMT价格数据")
        continue
    
    print(f"  成功拉取: {len(qmt_prices)}/{len(stocks)}只")
    
    # 4. 多种加权方式计算
    changes = [(d['close']/d['prev_close']-1)*100 for d in qmt_prices.values()]
    
    # 等权
    eq_chg = np.mean(changes)
    
    # 成交量加权（大成交→高权重）
    vols = np.array([d['volume'] for d in qmt_prices.values()])
    vol_weighted = np.average(changes, weights=vols) if vols.sum() > 0 else eq_chg
    
    # 中位数
    md_chg = np.median(changes)
    
    print(f"  涨跌幅:")
    print(f"    东财实际:     {em_chg:+.3f}%")
    print(f"    QMT等权均值:  {eq_chg:+.3f}% | 偏差={abs(eq_chg-em_chg):.3f}%")
    print(f"    QMT量加权:    {vol_weighted:+.3f}% | 偏差={abs(vol_weighted-em_chg):.3f}%")
    print(f"    QMT中位数:    {md_chg:+.3f}% | 偏差={abs(md_chg-em_chg):.3f}%")
    
    # 判断最佳匹配
    best = min([(abs(eq_chg-em_chg), '等权'), (abs(vol_weighted-em_chg), '量加权'), (abs(md_chg-em_chg), '中位数')])
    print(f"    最佳: {best[1]} (偏差{best[0]:.3f}%)")
