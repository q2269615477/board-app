"""
QMT分钟线补丁 - 替换 _load_qmt_kline 函数
直接通过QMT的python.exe运行脚本文件来获取分钟级K线
"""
import subprocess as _sp
import json as _js
import tempfile as _tf
import os as _os
import pandas as _pd
from core.config import QMT_PYTHON_PATH, QMT_DIR, QMT_PORTS

def _load_qmt_kline(code: str, data_type: str, period: str) -> _pd.DataFrame:
    """QMT subprocess - 支持日线和分钟线（xtquant仅QMT Python可用）"""
    qmt_code = _qmt_code(code, data_type)
    minute_map = {'1m':'1m','5m':'5m','15m':'15m','30m':'30m','60m':'60m','120m':'120m','240m':'240m'}
    today = _pd.Timestamp.now().strftime('%Y%m%d')
    
    if period == 'daily':
        qmt_period = '1d'
        start = '20200101'
    elif period in minute_map:
        qmt_period = minute_map[period]
        start = (_pd.Timestamp.now() - _pd.Timedelta(days=7)).strftime('%Y%m%d')
    else:
        return _pd.DataFrame()
    
    qmt_py = QMT_PYTHON_PATH
    qmt_dir = QMT_DIR
    date_fmt = '%Y-%m-%d %H:%M' if period in minute_map else '%Y-%m-%d'
    
    # 写脚本文件避免命令行转义问题
    script_path = _os.path.join(_tf.gettempdir(), f'_qmt_{code}_{period}.py')
    script_content = (
        "from xtquant import xtdata\n"
        "import pandas as pd, json, sys\n"
        f"xtdata.rpc_init('127.0.0.1:{QMT_PORTS[1]}')\n"
        "c=sys.argv[1];p=sys.argv[2];s=sys.argv[3];e=sys.argv[4]\n"
        "xtdata.download_history_data(c,p,s,e)\n"
        "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],p,s,e,count=-1)\n"
        "if c in raw and raw[c] is not None and not raw[c].empty:\n"
        " df=raw[c];r=[]\n"
        " for idx,row in df.iterrows():\n"
        "  t=int(idx) if isinstance(idx,(int,float)) and idx>1e12 else 0\n"
        "  dt=pd.to_datetime(t,unit='ms').strftime(sys.argv[5]) if t>1e12 else str(idx)\n"
        "  vol=int(float(row['volume']))\n"
        "  if vol==0: continue\n"
        "  r.append({'date':dt,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol})\n"
        " print(json.dumps({'status':'ok','data':r,'count':len(r)}))\n"
        "else:\n"
        " print(json.dumps({'status':'empty','data':[],'count':0}))\n"
    )
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)
    
    try:
        # 使用Popen+文件避免编码问题
        proc = _sp.Popen([qmt_py, script_path, qmt_code, qmt_period, start, today, date_fmt],
                        stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=qmt_dir)
        stdout, stderr = proc.communicate(timeout=30)
        # 尝试utf-8解码，失败用gbk
        out_str = stdout.decode('utf-8', errors='ignore').strip()
        err_str = stderr.decode('utf-8', errors='ignore').strip()
        if err_str:
            print(f"[QMT-stderr] {code}: {err_str[:200]}")
        for line in out_str.split('\n'):
            line = line.strip()
            if line.startswith('{'):
                try:
                    result = _js.loads(line)
                    if result.get('status') == 'ok' and result.get('count', 0) > 0:
                        return _pd.DataFrame(result['data'])
                except Exception:
                    pass  # JSON解析失败，跳过该行继续
    except Exception as e:
        print(f"[QMT] {code} {period} 获取失败: {e}")
    
    return _pd.DataFrame()


def _load_qmt_daily(code: str, data_type: str) -> _pd.DataFrame:
    """通过 QMT 获取日线数据并缓存到 SQLite"""
    qmt_code = _qmt_code(code, data_type)
    try:
        today = _pd.Timestamp.now().strftime('%Y%m%d')
        qmt_py = QMT_PYTHON_PATH
        qmt_dir = QMT_DIR
        
        script_path = _os.path.join(_tf.gettempdir(), f'_qmt_daily_{code}.py')
        # 使用QMT_DIR的统一路径，替换反斜杠为正斜杠以兼容Python字符串
        _qmt_dir_fwd = QMT_DIR.replace('\\', '/')
        script_content = (
            "from xtquant import xtdata\n"
            "import pandas as pd, json, sys\n"
            f"os_path='{_qmt_dir_fwd}'\n"
            "import os; os.chdir(os_path)\n"
            f"xtdata.rpc_init('127.0.0.1:{QMT_PORTS[1]}')\n"
            "c=sys.argv[1];s=sys.argv[2];e=sys.argv[3]\n"
            "xtdata.download_history_data(c,'1d',s,e)\n"
            "raw=xtdata.get_local_data(['time','open','high','low','close','volume'],[c],'1d',s,e,count=0)\n"
            "if c in raw and raw[c] is not None and not raw[c].empty:\n"
            " df=raw[c];r=[]\n"
            " for idx,row in df.iterrows():\n"
            "  t=int(idx) if isinstance(idx,(int,float)) and idx>1e12 else 0\n"
            "  dt=pd.to_datetime(t,unit='ms').strftime('%Y-%m-%d') if t>1e12 else str(idx)\n"
            "  vol=int(float(row['volume']))\n"
            "  r.append({'date':dt,'open':round(float(row['open']),2),'high':round(float(row['high']),2),'low':round(float(row['low']),2),'close':round(float(row['close']),2),'volume':vol})\n"
            " print(json.dumps({'status':'ok','data':r,'count':len(r)}))\n"
            "else:\n"
            " print(json.dumps({'status':'empty','data':[],'count':0}))\n"
        )
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        proc = _sp.Popen([qmt_py, script_path, qmt_code, '20200101', today],
                        stdout=_sp.PIPE, stderr=_sp.PIPE, cwd=qmt_dir)
        stdout, stderr = proc.communicate(timeout=30)
        out_str = stdout.decode('utf-8', errors='ignore').strip()
        for line in out_str.split('\n'):
            line = line.strip()
            if line.startswith('{'):
                try:
                    result = _js.loads(line)
                    if result.get('status') == 'ok' and result.get('count', 0) > 0:
                        return _pd.DataFrame(result['data'])
                except Exception:
                    pass  # JSON解析失败，跳过该行继续
    except Exception as e:
        print(f"[QMT-daily] {code} 获取失败: {e}")
    return _pd.DataFrame()


# 全局引用占位 - 避免重复声明
_qmt_code_ref = None

def install_qmt_patches():
    """将补丁安装到app模块"""
    import sys
    import importlib
    mod = importlib.import_module('app')
    mod._load_qmt_kline = _load_qmt_kline
    mod._load_qmt_daily = _load_qmt_daily
    print("[QMT-patch] 已安装分钟线补丁")
