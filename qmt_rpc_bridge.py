"""
QMT RPC 桥接服务
通过 QMT 客户端内置 Python 启动 HTTP 服务，读取实时数据
用法: 由 app.py 启动，端口 58609
"""
import sys, os, json, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# 添加 QMT Python 路径
QMT_LIB = r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\Lib\site-packages'
sys.path.insert(0, QMT_LIB)

from xtquant import xtdata
import pandas as pd


class QMTHandler(BaseHTTPRequestHandler):
    """轻量HTTP API处理器"""
    
    def log_message(self, format, *args):
        pass  # 静默日志
    
    def _respond(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode())
    
    def _error(self, msg):
        self.send_response(500)
        self.end_headers()
        self.wfile.write(json.dumps({'error': msg}).encode())
    
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            path = parsed.path
            
            if path == '/ping':
                self._respond({'status': 'ok'})
            
            elif path == '/kline':
                code = params.get('code', '')
                period = params.get('period', '1d')
                start = params.get('start', '20200101')
                end = params.get('end', '20261231')
                count = int(params.get('count', 0))
                
                field_map = {'daily':'1d','weekly':'1w','monthly':'1mo','1m':'1m','5m':'5m','15m':'15m','30m':'30m','60m':'60m'}
                qmt_period = field_map.get(period, period)
                
                xtdata.download_history_data(code, qmt_period, start, end)
                data = xtdata.get_local_data(
                    ['time','open','high','low','close','volume'],
                    [code], qmt_period, start, end, count=count)
                
                if code in data and data[code] is not None and not data[code].empty:
                    df = data[code]
                    result = []
                    for _, row in df.iterrows():
                        ts = row.name
                        d = pd.to_datetime(ts, unit='ms').strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts,(int,float)) and ts>1e9 else str(ts)[:10]
                        result.append({
                            'date': d,
                            'open': float(row['open']),
                            'high': float(row['high']),
                            'low': float(row['low']),
                            'close': float(row['close']),
                            'volume': float(row['volume'])
                        })
                    self._respond({'data': result, 'count': len(result)})
                else:
                    self._respond({'data': [], 'count': 0})
            
            elif path == '/sectors':
                sectors = xtdata.get_sector_list()
                self._respond({'data': [s for s in sectors if '加权' not in s]})
            
            elif path == '/sector_stocks':
                sector = params.get('sector', '')
                stocks = xtdata.get_stock_list_in_sector(sector)
                self._respond({'data': list(stocks) if stocks else []})
            
            elif path == '/detail':
                code = params.get('code', '')
                detail = xtdata.get_instrument_detail(code)
                self._respond(detail if detail else {})
            
            elif path == '/trading_dates':
                market = params.get('market', 'SH')
                start = params.get('start', '20200101')
                end = params.get('end', '20261231')
                dates = xtdata.get_trading_dates(market, start, end)
                self._respond({'data': [pd.to_datetime(d, unit='ms').strftime('%Y-%m-%d') for d in dates]})
            
            else:
                self._error('unknown endpoint')
        except Exception as e:
            self._error(str(e))


def start_server(port=58609):
    """启动HTTP服务"""
    server = HTTPServer(('127.0.0.1', port), QMTHandler)
    print(f'[QMT-RPC] HTTP服务启动: http://127.0.0.1:{port}')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == '__main__':
    # 初始化RPC连接
    print('正在连接QMT客户端...', flush=True)
    xtdata.rpc_init('127.0.0.1:58600')
    print('[OK] RPC已连接', flush=True)
    server = start_server(58609)
    print('桥接运行中 (端口58609)', flush=True)
    while True:
        time.sleep(60)
