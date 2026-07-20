"""
qmt_client.py — QMT统一调用客户端（单端口架构：58600 RPC）

取数优先级（日线）：
  1) qmt_api 公式口 getMarketData（58600 server_formula）— 本券商包已实证可取 OHLCV
  2) xtdata get_local_data — 需客户端注册行情服务（常为 Mini 58610），多数环境空壳

分钟线仍走 xtdata（公式口通常不提供分钟序列）。
"""
import os
import sys
import json
import time
import logging
import tempfile
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd

from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_ENABLED, QMT_DATA_DIR, DATA_DIR

logger = logging.getLogger('qmt_client')

# 单端口：58600 RPC（标准客户端公式/策略口）
QMT_RPC_PORT = 58600

# 子进程内把 QMT site-packages 加入 path（pythonw 的 sys.path 有时不含 lib）
_QMT_SITE_BOOT = (
    "import os, sys\n"
    "_root = os.path.dirname(os.path.dirname(sys.executable))\n"
    "_sp = os.path.join(_root, 'lib', 'site-packages')\n"
    "if _sp not in sys.path:\n"
    "    sys.path.insert(0, _sp)\n"
)


def _normalize_bar_date(raw) -> str:
    """统一为 YYYY-MM-DD。"""
    s = str(raw).strip().replace('/', '-')
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        return s[:10]
    digits = ''.join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f'{digits[:4]}-{digits[4:6]}-{digits[6:8]}'
    return s[:10]


def _rows_to_daily_df(rows: list) -> Optional[pd.DataFrame]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if df.empty or 'close' not in df.columns:
        return None
    if 'date' in df.columns:
        df['date'] = df['date'].map(_normalize_bar_date)
        df = df.drop_duplicates(subset=['date'], keep='last')
        df = df.sort_values('date').reset_index(drop=True)
    return df


class QMTClient:
    """QMT标准客户端数据访问（线程安全）

    主通道：完整 QMT 登录 + 58600 公式 RPC（qmt_api）；
    次通道：xtdata（需行情服务注册，常依赖 Mini 58610）。
    """

    def __init__(self):
        self._python = QMT_PYTHON_PATH
        self._cwd = QMT_DIR
        self._rpc_port = QMT_RPC_PORT
        self._data_dir = str(QMT_DATA_DIR)
        self._channel: Optional[str] = None  # 'formula' | 'xtdata' | None
        if not QMT_ENABLED:
            logger.info("[QMT] 已禁用")

    @property
    def _data_dir_lit(self) -> str:
        """子进程脚本内安全的 data_dir 字面量"""
        return repr(self._data_dir)

    @property
    def active_channel(self) -> Optional[str]:
        """最近一次成功取数通道：formula / xtdata / None"""
        return self._channel

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

        code_list_json = json.dumps([p[1] for p in qmt_pairs])
        import datetime
        today = datetime.date.today().strftime('%Y%m%d')
        start = (datetime.date.today() - datetime.timedelta(days=7)).strftime('%Y%m%d')

        script = (
            "from xtquant import xtdata\n"
            "import json, math, sys\n"
            f"DATA_DIR = {self._data_dir_lit}\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            "codes = json.loads(sys.argv[1])\n"
            f"s,e = '{start}','{today}'\n"
            # Phase 1: 直接从本地缓存读取（极快，<1s）；显式 data_dir
            "kl = xtdata.get_local_data(['time','close','preClose','volume'], codes, '1d', s, e, count=2, data_dir=DATA_DIR)\n"
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
            "    except Exception as _e:\n"
            "        import sys; sys.stderr.write(f'[QMT batch] {qc}: {_e}\\n')\n"
            # Phase 2: 仅对 stale 数据补下载后再查（仅需处理少量未缓存股票）
            "if stale_codes:\n"
            "    for qc in stale_codes:\n"
            "        try:\n"
            "            xtdata.download_history_data(qc, '1d', s, e)\n"
            "        except Exception:\n"
            "            pass\n"
            "    kl2 = xtdata.get_local_data(['time','close','preClose','volume'], stale_codes, '1d', s, e, count=2, data_dir=DATA_DIR)\n"
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
            "        except Exception:\n"
            "            pass\n"
            "print(json.dumps(result))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script, code_list_json],
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

        code_list_json = json.dumps([p[1] for p in qmt_pairs])

        script = (
            "from xtquant import xtdata\n"
            "import json, math, sys\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            "codes = json.loads(sys.argv[1])\n"
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
            "    except Exception:\n"
            "        pass\n"
            "print(json.dumps(result))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script, code_list_json],
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

    # ---- 个股列表 ----

    def get_stock_list(self, market: str = 'all') -> list:
        """获取全市场 A 股个股列表
        market: 'all'=全部, 'sh'=仅上海, 'sz'=仅深圳
        返回 [{code, name, market}]
        """
        if not QMT_ENABLED or not os.path.exists(self._python):
            return []

        filter_line = ''
        if market == 'sh':
            filter_line = "if not s.endswith('.SH'): continue\n"
        elif market == 'sz':
            filter_line = "if not s.endswith('.SZ'): continue\n"

        script = (
            "from xtquant import xtdata\n"
            "import json\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            "stocks = xtdata.get_stock_list_in_sector('沪深A股')\n"
            "result = []\n"
            "for s in stocks:\n"
            f"{filter_line}"
            "    code = s.split('.')[0]\n"
            "    mkt = 'SH' if s.endswith('.SH') else ('SZ' if s.endswith('.SZ') else 'BJ')\n"
            "    result.append({'code': code, 'name': '', 'market': mkt})\n"
            "print(json.dumps(result, ensure_ascii=False))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script],
                capture_output=True, timeout=30, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            if out:
                return json.loads(out)
        except Exception as e:
            logger.warning(f"[QMT] get_stock_list: {e}")
        return []

    # ---- 日线统一入口（公式口优先，xtdata 兜底）----

    def get_daily(
        self,
        qmt_code: str,
        start: str = '20200101',
        end: str = '',
        count: int = -1,
    ) -> Optional[pd.DataFrame]:
        """统一日线：优先 qmt_api 公式口，失败再走 xtdata。

        Returns:
            DataFrame columns: date(YYYY-MM-DD), open, high, low, close, volume
        """
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')
        start_n = str(start).replace('-', '')[:8]
        end_n = str(end).replace('-', '')[:8]

        df = self.get_daily_formula(qmt_code, start=start_n, end=end_n, count=count)
        if df is not None and not df.empty:
            self._channel = 'formula'
            return df

        df = self.get_daily_local(qmt_code, start=start_n, end=end_n)
        if df is not None and not df.empty:
            self._channel = 'xtdata'
            return df

        logger.debug(f"[QMT] get_daily 无数据 {qmt_code} {start_n}~{end_n}")
        return None

    def get_daily_batch(
        self,
        qmt_codes: List[str],
        start: str = '20200101',
        end: str = '',
        count: int = -1,
    ) -> Dict[str, pd.DataFrame]:
        """批量日线：单次子进程公式口；失败的代码再逐只 get_daily。"""
        if not qmt_codes:
            return {}
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')
        start_n = str(start).replace('-', '')[:8]
        end_n = str(end).replace('-', '')[:8]

        out: Dict[str, pd.DataFrame] = {}
        formula = self.get_daily_formula_batch(qmt_codes, start=start_n, end=end_n, count=count)
        for code, df in formula.items():
            if df is not None and not df.empty:
                out[code] = df
                self._channel = 'formula'

        missing = [c for c in qmt_codes if c not in out]
        for code in missing:
            df = self.get_daily(code, start=start_n, end=end_n, count=count)
            if df is not None and not df.empty:
                out[code] = df
        return out

    # ---- 日线获取（xtdata 路径，依赖 Mini 58610 行情服务）----

    def get_daily_local(self, qmt_code: str, start: str = '20200101', end: str = '') -> Optional[pd.DataFrame]:
        """用QMT自带Python获取日线（xtdata 路径）

        注意：xtdata.get_local_data 底层走 get_market_data3，要求对端
        已注册行情数据服务（通常为 Mini 58610）；58600 公式口不带此服务时返回空。
        """
        if not QMT_ENABLED or not os.path.exists(self._python):
            return None
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')

        script = (
            f"from xtquant import xtdata; import pandas as pd, json\n"
            f"DATA_DIR = {self._data_dir_lit}\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            f"xtdata.download_history_data('{qmt_code}','1d','{start}','{end}')\n"
            f"d=xtdata.get_local_data(['time','open','high','low','close','volume'],"
            f"['{qmt_code}'],'1d','{start}','{end}',count=0,data_dir=DATA_DIR)\n"
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
                return _rows_to_daily_df(json.loads(out))
        except Exception as e:
            logger.debug(f"[QMT] get_daily_local {qmt_code}: {e}")
        return None

    # ---- 日线获取（qmt_api 公式 RPC 路径，58600 即可工作）----

    def get_daily_formula(
        self,
        qmt_code: str,
        start: str = '20200101',
        end: str = '',
        count: int = -1,
    ) -> Optional[pd.DataFrame]:
        """用 qmt_api（公式 RPC 58600）获取日线

        qmt_api 走 formulaserver 的 server_formula 口（58600），
        通过 net.RPCClient.request('getMarketData', ...) 协议取数。
        实证：指数与个股均返回完整 OHLCV（不依赖 Mini 58610）。

        Args:
            qmt_code: QMT 代码格式（000001.SH）
            start: 起始日期 YYYYMMDD
            end: 截止日期 YYYYMMDD（空=今天）
            count: 返回条数（-1=全部）

        Returns:
            含 date(YYYY-MM-DD),open,high,low,close,volume 列的 DataFrame
        """
        if not QMT_ENABLED or not os.path.exists(self._python):
            return None
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')
        start = str(start).replace('-', '')[:8]
        end = str(end).replace('-', '')[:8]

        script = (
            _QMT_SITE_BOOT
            + "import json\n"
            "import qmt_api.api as api\n"
            "import pandas as pd\n"
            f"c = {qmt_code!r}\n"
            f"s = {start!r}\n"
            f"e = {end!r}\n"
            f"n = {int(count)}\n"
            "md = api.get_market_data(['open','high','low','close','volume'], [c], s, e, '1d', 'none', n)\n"
            "rows = []\n"
            "if md is None:\n"
            "    print('[]')\n"
            "elif isinstance(md, pd.DataFrame):\n"
            "    for idx, row in md.iterrows():\n"
            "        ds = str(idx)[:10]\n"
            "        vol_raw = row['volume'] if 'volume' in row.index else 0\n"
            "        vol = int(float(vol_raw)) if pd.notna(vol_raw) else 0\n"
            "        rows.append({\n"
            "            'date': ds,\n"
            "            'open': round(float(row['open']), 4),\n"
            "            'high': round(float(row['high']), 4),\n"
            "            'low': round(float(row['low']), 4),\n"
            "            'close': round(float(row['close']), 4),\n"
            "            'volume': vol\n"
            "        })\n"
            "    print(json.dumps(rows, ensure_ascii=False))\n"
            "else:\n"
            "    # Panel 单标的偶发\n"
            "    try:\n"
            "        df = md[c] if c in getattr(md, 'items', []) else None\n"
            "        if df is not None and hasattr(df, 'iterrows'):\n"
            "            for idx, row in df.iterrows():\n"
            "                ds = str(idx)[:10]\n"
            "                vol_raw = row['volume'] if 'volume' in row.index else 0\n"
            "                vol = int(float(vol_raw)) if pd.notna(vol_raw) else 0\n"
            "                rows.append({'date': ds, 'open': round(float(row['open']), 4),\n"
            "                    'high': round(float(row['high']), 4), 'low': round(float(row['low']), 4),\n"
            "                    'close': round(float(row['close']), 4), 'volume': vol})\n"
            "    except Exception:\n"
            "        pass\n"
            "    print(json.dumps(rows, ensure_ascii=False))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script],
                capture_output=True, timeout=60, cwd=self._cwd
            )
            out = proc.stdout.decode('utf-8', errors='ignore').strip()
            # 取最后一行 JSON（避免 QMT 杂讯日志）
            payload = None
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    try:
                        payload = json.loads(line)
                        break
                    except Exception:
                        continue
            if payload:
                df = _rows_to_daily_df(payload)
                if df is not None and not df.empty:
                    return df
            stderr = (proc.stderr or b'').decode('utf-8', errors='ignore').strip()
            if stderr:
                logger.debug(f"[QMT] get_daily_formula {qmt_code} stderr: {stderr[-200:]}")
        except Exception as e:
            logger.warning(f"[QMT] get_daily_formula {qmt_code}: {e}")
        return None

    def get_daily_formula_batch(
        self,
        qmt_codes: List[str],
        start: str = '20200101',
        end: str = '',
        count: int = -1,
    ) -> Dict[str, pd.DataFrame]:
        """批量公式口日线（单子进程）。多标的返回 Panel：items=代码, major=日期, minor=字段。"""
        if not QMT_ENABLED or not os.path.exists(self._python) or not qmt_codes:
            return {}
        if not end:
            end = pd.Timestamp.now().strftime('%Y%m%d')
        start = str(start).replace('-', '')[:8]
        end = str(end).replace('-', '')[:8]
        codes_json = json.dumps(list(qmt_codes))

        script = (
            _QMT_SITE_BOOT
            + "import json, sys\n"
            "import qmt_api.api as api\n"
            "import pandas as pd\n"
            "codes = json.loads(sys.argv[1])\n"
            f"s = {start!r}; e = {end!r}; n = {int(count)}\n"
            "md = api.get_market_data(['open','high','low','close','volume'], codes, s, e, '1d', 'none', n)\n"
            "out = {}\n"
            "if md is None:\n"
            "    print('{}')\n"
            "elif isinstance(md, pd.DataFrame):\n"
            "    # 单标的时可能是 DataFrame\n"
            "    c0 = codes[0] if codes else 'unknown'\n"
            "    rows = []\n"
            "    for idx, row in md.iterrows():\n"
            "        vol_raw = row['volume'] if 'volume' in row.index else 0\n"
            "        vol = int(float(vol_raw)) if pd.notna(vol_raw) else 0\n"
            "        rows.append({'date': str(idx)[:10], 'open': round(float(row['open']), 4),\n"
            "            'high': round(float(row['high']), 4), 'low': round(float(row['low']), 4),\n"
            "            'close': round(float(row['close']), 4), 'volume': vol})\n"
            "    out[c0] = rows\n"
            "    print(json.dumps(out, ensure_ascii=False))\n"
            "else:\n"
            "    # Panel: items=codes\n"
            "    try:\n"
            "        items = list(md.items)\n"
            "    except Exception:\n"
            "        items = []\n"
            "    for c in items:\n"
            "        try:\n"
            "            df = md[c]\n"
            "            rows = []\n"
            "            for idx, row in df.iterrows():\n"
            "                vol_raw = row['volume'] if 'volume' in row.index else 0\n"
            "                vol = int(float(vol_raw)) if pd.notna(vol_raw) else 0\n"
            "                rows.append({'date': str(idx)[:10], 'open': round(float(row['open']), 4),\n"
            "                    'high': round(float(row['high']), 4), 'low': round(float(row['low']), 4),\n"
            "                    'close': round(float(row['close']), 4), 'volume': vol})\n"
            "            if rows:\n"
            "                out[str(c)] = rows\n"
            "        except Exception:\n"
            "            pass\n"
            "    print(json.dumps(out, ensure_ascii=False))\n"
        )

        try:
            proc = subprocess.run(
                [self._python, '-c', script, codes_json],
                capture_output=True, timeout=120, cwd=self._cwd
            )
            out_txt = proc.stdout.decode('utf-8', errors='ignore').strip()
            payload = None
            for line in reversed(out_txt.splitlines()):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        payload = json.loads(line)
                        break
                    except Exception:
                        continue
            if not payload:
                return {}
            result: Dict[str, pd.DataFrame] = {}
            for code, rows in payload.items():
                df = _rows_to_daily_df(rows if isinstance(rows, list) else [])
                if df is not None and not df.empty:
                    result[code] = df
            return result
        except Exception as e:
            logger.warning(f"[QMT] get_daily_formula_batch: {e}")
            return {}

    def probe_formula_ready(self) -> dict:
        """探测公式口是否可取日线（用于 lifecycle / 诊断）。"""
        result = {'ok': False, 'rows': 0, 'channel': None, 'err': ''}
        try:
            df = self.get_daily_formula('000001.SH', start='20260601', end='', count=5)
            if df is not None and not df.empty:
                result['ok'] = True
                result['rows'] = int(len(df))
                result['channel'] = 'formula'
                result['last_date'] = str(df.iloc[-1]['date'])
                result['last_close'] = float(df.iloc[-1]['close'])
                self._channel = 'formula'
            else:
                result['err'] = 'empty'
        except Exception as e:
            result['err'] = str(e)[:200]
        return result

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
            f"DATA_DIR = {self._data_dir_lit}\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            f"c=sys.argv[1]; p='1m'; s='{start}'; e='{end}'\n"
            "xtdata.download_history_data(c, p, s, e)\n"
            "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],p,s,e,count=-1,data_dir=DATA_DIR)\n"
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
            f"DATA_DIR = {self._data_dir_lit}\n"
            f"xtdata.reconnect('127.0.0.1', {self._rpc_port})\n"
            f"c=sys.argv[1]; p='{qmt_period}'; s='{start}'; e='{end}'\n"
            "xtdata.download_history_data(c, p, s, e)\n"
            "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],p,s,e,count=-1,data_dir=DATA_DIR)\n"
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


# 全局单例（双重检查锁定）

_qmt_client: Optional[QMTClient] = None
_qmt_client_lock = threading.Lock()


def get_qmt_client() -> QMTClient:
    global _qmt_client
    if _qmt_client is None:
        with _qmt_client_lock:
            if _qmt_client is None:
                _qmt_client = QMTClient()
    return _qmt_client
