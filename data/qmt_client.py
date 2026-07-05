"""
qmt_client.py — QMT统一调用客户端（单端口架构：58600 RPC）
所有数据通过 QMT 标准客户端 RPC 获取，不依赖 MiniQMT
"""
import os
import sys
import json
import time
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import Optional

import pandas as pd

from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_ENABLED, DATA_DIR

logger = logging.getLogger('qmt_client')

# 单端口：58600 RPC（标准客户端）
QMT_RPC_PORT = 58600


class QMTClient:
    """QMT标准客户端数据访问（线程安全）"""

    def __init__(self):
        self._python = QMT_PYTHON_PATH
        self._cwd = QMT_DIR
        self._rpc_port = QMT_RPC_PORT
        if not QMT_ENABLED:
            logger.info("[QMT] 已禁用")

    def get_constituents_batch(self, codes: list) -> dict:
        """QMT批量获取成分股全量数据（单次子进程，<1s）
        返回 {code: {close, change_pct, mkt_cap, volume}} 字典
        — close: 最新交易日收盘价
        — change_pct: 涨跌幅(%), QMT原生preClose字段计算
        — mkt_cap: 总市值(亿), TotalVolume×close/1e8
        — volume: 成交量(手)
        """
        if not QMT_ENABLED or not os.path.exists(self._python) or not codes:
            return {}
        
        qmt_pairs = []
        for cd in codes:
            qc = self.to_qmt_code(cd, 'stock')
            if qc:
                qmt_pairs.append((cd, qc))
        if not qmt_pairs:
            return {}

        code_list = json.dumps([p[1] for p in qmt_pairs])
        import datetime
        today = datetime.date.today().strftime('%Y%m%d')
        start = (datetime.date.today() - datetime.timedelta(days=7)).strftime('%Y%m%d')

        script = (
            "from xtquant import xtdata\n"
            "import json, math\n"
            f"xtdata.rpc_init('127.0.0.1:{self._rpc_port}')\n"
            f"codes = {code_list}\n"
            f"s,e = '{start}','{today}'\n"
            # Phase 1: 直接从本地缓存读取（极快，<1s）\n"
            "kl = xtdata.get_local_data(['time','close','preClose','volume'], codes, '1d', s, e, count=2)\n"
            "result = {}\n"
            "stale_codes = []\n"
            "for qc in codes:\n"
            "    try:\n"
            "        df = kl.get(qc) if isinstance(kl, dict) else None\n"
            "        if df is None or df.empty: continue\n"
            "        last = df.iloc[-1]\n"
            "        close = float(last['close'])\n"
            "        pre_close = float(last.get('preClose', close))\n"
            "        if math.isnan(close) or math.isnan(pre_close) or close <= 0: continue\n"
            "        volume = int(float(last.get('volume', 0) or 0))\n"
            "        # 检测 stale: volume=0 或 close==preClose（可能未下载或停牌）\n"
            "        if volume == 0 and close == pre_close:\n"
            "            stale_codes.append(qc)\n"
            "            continue\n"
            "        chg = round((close - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0\n"
            "        detail = xtdata.get_instrument_detail(qc)\n"
            "        tv = detail.get('TotalVolume', 0) or 0 if detail else 0\n"
            "        cap = round(tv * close / 1e8, 2) if tv > 0 else 0\n"
            "        result[qc.split('.')[0]] = {'close': round(close, 4), 'change_pct': chg, 'mkt_cap': cap, 'volume': volume}\n"
            "    except:\n"
            "        pass\n"
            # Phase 2: 仅对 stale 数据补下载后再查（仅需处理少量未缓存股票）\n"
            "if stale_codes:\n"
            "    for qc in stale_codes:\n"
            "        try:\n"
            "            xtdata.download_history_data(qc, '1d', s, e)\n"
            "        except Exception:\n"
            "            pass\n"
            "    kl2 = xtdata.get_local_data(['time','close','preClose','volume'], stale_codes, '1d', s, e, count=2)\n"
            "    for qc in stale_codes:\n"
            "        try:\n"
            "            df = kl2.get(qc) if isinstance(kl2, dict) else None\n"
            "            if df is None or df.empty: continue\n"
            "            last = df.iloc[-1]\n"
            "            close = float(last['close'])\n"
            "            pre_close = float(last.get('preClose', close))\n"
            "            if math.isnan(close) or math.isnan(pre_close) or close <= 0: continue\n"
            "            chg = round((close - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0\n"
            "            detail = xtdata.get_instrument_detail(qc)\n"
            "            tv = detail.get('TotalVolume', 0) or 0 if detail else 0\n"
            "            cap = round(tv * close / 1e8, 2) if tv > 0 else 0\n"
            "            volume = int(float(last.get('volume', 0) or 0))\n"
            "            result[qc.split('.')[0]] = {'close': round(close, 4), 'change_pct': chg, 'mkt_cap': cap, 'volume': volume}\n"
            "        except:\n"
            "            pass\n"
            "print(json.dumps(result))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script],
                capture_output=True, timeout=15, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            if out:
                raw = json.loads(out)
                result = {}
                for cd, qc in qmt_pairs:
                    qcode = qc.split('.')[0]
                    if qcode in raw:
                        result[cd] = raw[qcode]
                return result
        except Exception as e:
            logger.warning(f"[QMT] get_constituents_batch: {e}")
        return {}

    def get_constituents_live(self, codes: list) -> dict:
        """QMT实时盘中数据（仅用于刷新按钮，不走日线缓存）
        返回 {code: {close(=最新成交价), change_pct, mkt_cap, volume}} 字典
        — close: get_instrument_detail.LastPrice (盘中实时)
        — change_pct: get_instrument_detail.ChangeRatio (QMT原生涨跌幅)
        — mkt_cap: TotalVolume × LastPrice / 1e8
        — volume: get_instrument_detail.Volume (当日累计)
        
        ⚠️ 盘中数据为临时缓存，不写入本地 SQLite
        """
        if not QMT_ENABLED or not os.path.exists(self._python) or not codes:
            return {}

        qmt_pairs = []
        for cd in codes:
            qc = self.to_qmt_code(cd, 'stock')
            if qc:
                qmt_pairs.append((cd, qc))
        if not qmt_pairs:
            return {}

        code_list = json.dumps([p[1] for p in qmt_pairs])

        script = (
            "from xtquant import xtdata\n"
            "import json, math\n"
            f"xtdata.rpc_init('127.0.0.1:{self._rpc_port}')\n"
            f"codes = {code_list}\n"
            "result = {}\n"
            "for qc in codes:\n"
            "    try:\n"
            "        detail = xtdata.get_instrument_detail(qc)\n"
            "        if not detail: continue\n"
            "        lp = detail.get('LastPrice', 0) or 0  # 最新成交价\n"
            "        if lp <= 0: continue\n"
            "        pre = detail.get('PreClose', lp) or 0\n"
            "        chg = detail.get('ChangeRatio', 0) or 0  # QMT原生涨跌幅(%)\n"
            "        vol = int(detail.get('Volume', 0) or 0)  # 当日累计成交量\n"
            "        tv = detail.get('TotalVolume', 0) or 0    # 总股本\n"
            "        cap = round(tv * lp / 1e8, 2) if tv > 0 else 0\n"
            "        result[qc.split('.')[0]] = {\n"
            "            'close': round(lp, 4),\n"
            "            'change_pct': round(chg, 2),\n"
            "            'mkt_cap': cap,\n"
            "            'volume': vol\n"
            "        }\n"
            "    except:\n"
            "        pass\n"
            "print(json.dumps(result))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script],
                capture_output=True, timeout=20, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            if out:
                raw = json.loads(out)
                result = {}
                for cd, qc in qmt_pairs:
                    qcode = qc.split('.')[0]
                    if qcode in raw:
                        result[cd] = raw[qcode]
                return result
        except Exception as e:
            logger.warning(f"[QMT] get_constituents_live: {e}")
        return {}

    # ---- 代码转换 ----

    @staticmethod
    def to_qmt_code(code: str, data_type: str = '') -> str:
        """面板代码 → QMT代码（sh000001 → 000001.SH）"""
        idx_map = {
            'sh000001': '000001.SH', 'sz399006': '399006.SZ',
            'sh000688': '000688.SH', 'sh000300': '000300.SH',
            'sh000016': '000016.SH', 'sh000852': '000852.SH',
            'sh000853': '000853.SH', 'sh000985': '000985.SH',
            'HSI': 'HSI.HK', 'HSTECH': 'HSTECH.HK',
        }
        if code in idx_map:
            return idx_map[code]
        if len(code) == 8 and code[2:].isdigit():
            prefix, num = code[:2], code[2:]
            if prefix == 'sh': return f'{num}.SH'
            if prefix == 'sz': return f'{num}.SZ'
            if prefix == 'bj': return f'{num}.BJ'
        if data_type == 'stock' and len(code) == 6:
            # 北交所：4xxxxx/8xxxxx/920xxx；其余：上海6/9开头，深圳0/2/3开头
            if code.startswith(('4', '8')) or code.startswith('920'):
                return f'{code}.BJ'
            return f'{code}.SH' if code.startswith(('6', '9')) else f'{code}.SZ'
        if data_type in ('hk', 'hk_index') and len(code) <= 5:
            return f'{code}.HK'
        return code

    # ---- 日线获取 ----

    def get_daily_local(self, qmt_code: str, start: str = '20200101', end: str = '') -> Optional[pd.DataFrame]:
        """用QMT自带Python获取日线"""
        if not QMT_ENABLED or not os.path.exists(self._python):
            return None
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')

        script = (
            f"from xtquant import xtdata; import pandas as pd, json\n"
            f"xtdata.rpc_init('127.0.0.1:{self._rpc_port}')\n"
            f"xtdata.download_history_data('{qmt_code}','1d','{start}','{end}')\n"
            f"d=xtdata.get_local_data(['time','open','high','low','close','volume'],['{qmt_code}'],'1d','{start}','{end}',count=0)\n"
            f"if '{qmt_code}' in d and d['{qmt_code}'] is not None and not d['{qmt_code}'].empty:\n"
            f"  rows=[]\n"
            f"  for idx,row in d['{qmt_code}'].iterrows():\n"
            f"    t=int(idx) if isinstance(idx,(int,float)) and idx>1e9 else 0\n"
            f"    ds=str(pd.to_datetime(t,unit='ms').date()) if t>1e9 else str(idx)[:10]\n"
            f"    vol=int(float(row['volume'])) or 0\n"
            f"    if vol==0: continue\n"
            f"    rows.append({{'date':ds,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol}})\n"
            f"  print(json.dumps(rows))\n"
            f"else: print('[]')\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script],
                capture_output=True, timeout=15, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            if out and out != '[]':
                return pd.DataFrame(json.loads(out))
        except Exception as e:
            logger.debug(f"[QMT] get_daily_local {qmt_code}: {e}")
        return None

    # ---- 分钟线获取（独立脚本文件模式，避免shell转义）----

    def get_minute_kline(self, code: str, data_type: str, period: str) -> pd.DataFrame:
        """获取分钟级K线（1m/5m/15m/30m/60m/120m/240m）"""
        minute_map = {'1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                      '60m': '60m', '120m': '120m', '240m': '240m'}
        if period not in minute_map:
            return pd.DataFrame()

        qmt_code = self.to_qmt_code(code, data_type)
        today = pd.Timestamp.now().strftime('%Y%m%d')
        start = (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime('%Y%m%d')

        # QMT只支持1m/5m直读，更大周期需要从1m resample
        resample_periods = {'15m', '30m', '60m', '120m', '240m'}

        if period in resample_periods:
            # 先获取原始1m数据，resample由调用方执行
            script = self._build_raw_1m_script(qmt_code, start, today)
        else:
            script = self._build_minute_script(qmt_code, minute_map[period], start, today)

        suffix = f'_{period}' if period not in resample_periods else '_1m_raw'
        script_path = Path(tempfile.gettempdir()) / f'_qk_{code}{suffix}.py'
        script_path.write_text(script, encoding='utf-8')

        try:
            proc = subprocess.run(
                [self._python, str(script_path), qmt_code],
                capture_output=True, timeout=30, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            if not out:
                out = '[]'
            data = json.loads(out)
            if data:
                actual_period = '1m' if period in resample_periods else period
                logger.info(f"[QMT] {code}({qmt_code}) {actual_period}: {len(data)}条")
                return pd.DataFrame(data)
        except Exception as e:
            logger.warning(f"[QMT] {code} {period} 获取失败: {e}")
        return pd.DataFrame()

    def _build_raw_1m_script(self, qmt_code: str, start: str, end: str) -> str:
        """构建脚本：获取原始1m数据（用于后续resample）"""
        return (
            "from xtquant import xtdata\n"
            "import json, sys, os\n"
            f"xtdata.rpc_init('127.0.0.1:{self._rpc_port}')\n"
            f"c=sys.argv[1]; p='1m'; s='{start}'; e='{end}'\n"
            "xtdata.download_history_data(c, p, s, e)\n"
            "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],p,s,e,count=-1)\n"
            "if isinstance(raw,dict) and c in raw and raw[c] is not None and not raw[c].empty:\n"
            "    df=raw[c]; r=[]\n"
            "    for idx,row in df.iterrows():\n"
            "        idx=int(idx) if isinstance(idx,(int,float)) else 0\n"
            "        if idx>1e13:\n"
            "            si=str(idx); dt=f'{si[:4]}-{si[4:6]}-{si[6:8]} {si[8:10]}:{si[10:12]}'\n"
            "        else:\n"
            "            dt=str(idx)\n"
            "        vol=int(float(row['volume']))\n"
            "        r.append({'date':dt,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol})\n"
            "    print(json.dumps(r,ensure_ascii=False))\n"
            "else:\n"
            "    print('[]')\n"
        )

    def _build_minute_script(self, qmt_code: str, qmt_period: str, start: str, end: str) -> str:
        """构建QMT分钟线获取脚本（仅用于1m/5m，QMT原生支持）"""
        return (
            "from xtquant import xtdata\n"
            "import json, sys, os\n"
            f"xtdata.rpc_init('127.0.0.1:{self._rpc_port}')\n"
            f"c=sys.argv[1]; p='{qmt_period}'; s='{start}'; e='{end}'\n"
            "xtdata.download_history_data(c, p, s, e)\n"
            "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],p,s,e,count=-1)\n"
            "if isinstance(raw,dict) and c in raw and raw[c] is not None and not raw[c].empty:\n"
            "    df=raw[c]; r=[]\n"
            "    for idx,row in df.iterrows():\n"
            "        idx=int(idx) if isinstance(idx,(int,float)) else 0\n"
            "        if idx>1e13:\n"
            "            si=str(idx); dt=f'{si[:4]}-{si[4:6]}-{si[6:8]} {si[8:10]}:{si[10:12]}'\n"
            "        elif idx>1e12:\n"
            "            from pandas import to_datetime; dt=str(to_datetime(idx,unit='ms').date())\n"
            "        else:\n"
            "            dt=str(idx)\n"
            "        vol=int(float(row['volume']))\n"
            "        if vol==0: continue\n"
            "        r.append({'date':dt,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol})\n"
            "    print(json.dumps(r,ensure_ascii=False))\n"
            "else:\n"
            "    print('[]')\n"
        )


# 全局单例

_qmt_client: Optional[QMTClient] = None


def get_qmt_client() -> QMTClient:
    global _qmt_client
    if _qmt_client is None:
        _qmt_client = QMTClient()
    return _qmt_client
