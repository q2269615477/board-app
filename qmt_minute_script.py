from xtquant import xtdata
import pandas as pd, json, sys, os

os.chdir(r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64')

xtdata.rpc_init('127.0.0.1:58610')

c = sys.argv[1]; p = sys.argv[2]; s = sys.argv[3]; e = sys.argv[4]

xtdata.download_history_data(c, p, s, e)
raw = xtdata.get_local_data(['time','open','high','low','close','volume'], [c], p, s, e, count=-1)

if c in raw and raw[c] is not None and not raw[c].empty:
    df = raw[c]; r = []
    for idx, row in df.iterrows():
        ts = int(idx) if isinstance(idx, (int, float)) and idx > 1e12 else 0
        dt = pd.to_datetime(ts, unit='ms').strftime('%Y-%m-%d %H:%M')
        vol = int(float(row['volume']))
        if vol == 0:
            continue
        r.append({'date': dt, 'open': round(float(row['open']),2), 'high': round(float(row['high']),2), 'low': round(float(row['low']),2), 'close': round(float(row['close']),2), 'volume': vol})
    print(json.dumps({'status': 'ok' if r else 'empty', 'data': r, 'count': len(r)}))
else:
    print(json.dumps({'status': 'empty', 'data': [], 'count': 0}))

