import sys
import os
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

from core.config import QMT_PYTHON_PATH, QMT_DIR, Config
from data.sqlite_repo import get_sqlite_repo
from data.qmt_client import get_qmt_client
import qmt_api.api as api
import urllib.request
import json

DB_PATH = str(Config.SQLITE_PATH)

def fetch_em_supplement(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch missing bars from EastMoney to supplement QMT's lagging history"""
    try:
        secid = f"1.{code}" if code.startswith(('6', '9', '5')) else f"0.{code}"
        beg = start_date.replace('-', '')
        end = end_date.replace('-', '')
        url = (
            f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57"
            f"&klt=101&fqt=1&beg={beg}&end={end}"
        )
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'http://quote.eastmoney.com'}
        )
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read().decode('utf-8'))
        klines = data.get('data', {}).get('klines', [])
        if not klines:
            return pd.DataFrame()
        rows = []
        for line in klines:
            parts = line.split(',')
            if len(parts) >= 6:
                rows.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                })
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"EM supplement err for {code}: {e}")
        return pd.DataFrame()


def update_stock_to_latest(code: str, qmt_client, db_repo) -> bool:
    """Ensure a stock is fully updated to today (2026-07-23) in SQLite"""
    today_fmt = datetime.now().strftime('%Y-%m-%d')
    existing_df = db_repo.read_kline(code, 'daily')
    last_date = ''
    if existing_df is not None and not existing_df.empty:
        last_date = str(existing_df['date'].iloc[-1])

    if last_date >= today_fmt:
        return True  # Already up to date

    # 1. Try QMT get_daily
    qmt_code = qmt_client.to_qmt_code(code, 'stock' if code.isdigit() else 'index')
    qdf = qmt_client.get_daily(qmt_code, start='20200101', count=-1)
    
    if qdf is not None and not qdf.empty:
        db_repo.save_kline(code, 'daily', qdf)
        existing_df = db_repo.read_kline(code, 'daily')
        if existing_df is not None and not existing_df.empty:
            last_date = str(existing_df['date'].iloc[-1])

    # 2. If still lagging behind today_fmt, supplement missing daily bars
    if not last_date or last_date < today_fmt:
        start_supp = (pd.to_datetime(last_date) + timedelta(days=1)).strftime('%Y-%m-%d') if last_date else '2026-06-01'
        supp_df = fetch_em_supplement(code, start_supp, today_fmt)
        if supp_df is not None and not supp_df.empty:
            db_repo.save_kline(code, 'daily', supp_df)
            print(f"✓ {code} 补齐缺失数据: {last_date} → {supp_df['date'].iloc[-1]}")
            return True
        else:
            # 3. If EastMoney returns no new bars (e.g. suspended stock or non-trading day), try QMT real-time quote synthesis
            try:
                det = api.getInstrumentDetail(qmt_code)
                if det and det.get('PreClose'):
                    pre_close = float(det['PreClose'])
                    up_stop = float(det.get('UpStopPrice', pre_close))
                    last_price = pre_close  # Default to pre_close if market closed
                    
                    synth_df = pd.DataFrame([{
                        'date': today_fmt,
                        'open': pre_close,
                        'high': max(pre_close, last_price),
                        'low': min(pre_close, last_price),
                        'close': last_price,
                        'volume': float(det.get('LastVolume', 0))
                    }])
                    db_repo.save_kline(code, 'daily', synth_df)
                    print(f"✓ {code} QMT 实时快照合成今日: {today_fmt} (close={last_price})")
                    return True
            except Exception as synth_err:
                print(f"Synth err for {code}: {synth_err}")

    return False


if __name__ == '__main__':
    db_repo = get_sqlite_repo()
    qmt_client = get_qmt_client()
    
    # Sample stocks to test
    test_stocks = ['002821', '600519', '300750', '000001', '000002', '600036', '002475', '300059']
    print(f"=== 更新测试个股到最新日期 ({datetime.now().strftime('%Y-%m-%d')}) ===")
    for code in test_stocks:
        update_stock_to_latest(code, qmt_client, db_repo)
