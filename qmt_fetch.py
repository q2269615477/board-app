# -*- coding: utf-8 -*-
"""QMT data fetcher via rpc_init to QMT client (port 58600)"""
import sys, json, time
sys.path.insert(0, r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\Lib\site-packages')

from xtquant import xtdata
import pandas as pd

xtdata.rpc_init('127.0.0.1:58600')

code = sys.argv[1] if len(sys.argv) > 1 else '000001.SH'
start = sys.argv[2] if len(sys.argv) > 2 else '20200101'
end = sys.argv[3] if len(sys.argv) > 3 else '20261231'

try:
    xtdata.download_history_data(code, '1d', start, end)
    data = xtdata.get_local_data(
        ['time','open','high','low','close','volume'],
        [code], '1d', start, end, count=0)
    
    if code in data and data[code] is not None and not data[code].empty:
        df = data[code]
        result = []
        for idx, row in df.iterrows():
            ts = int(idx) if isinstance(idx, (int, float)) and idx > 1e9 else 0
            if ts > 1e9:
                ds = pd.to_datetime(ts, unit='ms').strftime('%Y-%m-%d')
            else:
                ds = str(idx)[:10]
            result.append({
                'date': ds,
                'open': round(float(row['open']), 2),
                'high': round(float(row['high']), 2),
                'low': round(float(row['low']), 2),
                'close': round(float(row['close']), 2),
                'volume': int(float(row['volume']))
            })
        print(json.dumps({'status': 'ok', 'data': result, 'count': len(result)}))
    else:
        print(json.dumps({'status': 'empty', 'data': [], 'count': 0}))
except Exception as e:
    print(json.dumps({'status': 'error', 'error': str(e)}))
