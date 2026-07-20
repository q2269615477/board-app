"""
QMT实际对接 - 接入xtquant
"""

import os
import sys
import time
import logging
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger('qmt_xtquant')

# 从集中配置导入QMT路径
from core.config import QMT_DIR

# 尝试导入xtquant
try:
    # 添加QMT路径（从集中配置读取）
    qmt_path = QMT_DIR
    if os.path.exists(qmt_path) and qmt_path not in sys.path:
        sys.path.insert(0, qmt_path)
    
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAsset
    XTQUANT_AVAILABLE = True
    logger.info("xtquant导入成功")
except ImportError as e:
    # 预期行为：Flask venv 通常无 xtquant；日线走 data/qmt_client
    # 子进程调用 QMT 自带 pythonw + qmt_api/xtdata，不必装进 venv。
    XTQUANT_AVAILABLE = False
    logger.info(
        "xtquant 未装入 Flask venv（预期）。行情经 qmt_client 子进程取数；"
        f"细节: {e}"
    )


@dataclass
class QMTPriceData:
    """QMT价格数据"""
    code: str
    name: str
    price: float
    open: float
    high: float
    low: float
    pre_close: float
    volume: int
    amount: float
    change_pct: float
    timestamp: int


class QMTXtquantService:
    """QMT xtquant服务"""
    
    def __init__(self):
        self.xt_trader = None
        self.session_id = 0
        self.connected = False
        self.price_cache: Dict[str, QMTPriceData] = {}
        self.subscribed_codes = set()
        self._callback_registered = False
        
    def connect(self) -> bool:
        """连接QMT"""
        if not XTQUANT_AVAILABLE:
            logger.error("xtquant不可用")
            return False
        
        try:
            # 创建交易对象
            self.session_id = int(time.time())
            self.xt_trader = XtQuantTrader(
                path=r'D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata',
                session_id=self.session_id
            )
            
            # 启动交易连接
            self.xt_trader.start()
            connect_result = self.xt_trader.connect()
            
            if connect_result == 0:
                self.connected = True
                logger.info(f"QMT连接成功, session_id: {self.session_id}")
                
                # 注册回调
                if not self._callback_registered:
                    self._register_callbacks()
                
                return True
            else:
                logger.error(f"QMT连接失败, 错误码: {connect_result}")
                return False
                
        except Exception as e:
            logger.error(f"QMT连接异常: {e}")
            return False
    
    def disconnect(self):
        """断开QMT连接"""
        if self.xt_trader:
            try:
                self.xt_trader.stop()
                logger.info("QMT已断开")
            except Exception as e:
                logger.error(f"QMT断开异常: {e}")
        self.connected = False
    
    def _register_callbacks(self):
        """注册行情回调"""
        if not XTQUANT_AVAILABLE:
            return
        
        try:
            # 注册行情推送回调
            xtdata.subscribe_quote(self._on_quote_changed)
            self._callback_registered = True
            logger.info("行情回调已注册")
        except Exception as e:
            logger.error(f"注册回调失败: {e}")
    
    def _on_quote_changed(self, data):
        """行情推送回调"""
        try:
            for code, quote in data.items():
                self.price_cache[code] = QMTPriceData(
                    code=code,
                    name=quote.get('name', ''),
                    price=round(quote.get('lastPrice', 0), 2),
                    open=round(quote.get('open', 0), 2),
                    high=round(quote.get('high', 0), 2),
                    low=round(quote.get('low', 0), 2),
                    pre_close=round(quote.get('lastClose', 0), 2),
                    volume=int(quote.get('volume', 0)),
                    amount=round(quote.get('amount', 0), 2),
                    change_pct=round(quote.get('changePct', 0), 2),
                    timestamp=int(time.time() * 1000)
                )
        except Exception as e:
            logger.error(f"处理行情推送失败: {e}")
    
    def subscribe(self, codes: List[str]) -> bool:
        """订阅行情"""
        if not XTQUANT_AVAILABLE:
            logger.error("xtquant不可用")
            return False
        
        try:
            # 转换代码格式
            xt_codes = []
            for code in codes:
                if code.startswith('6'):
                    xt_code = f"{code}.SH"
                elif code.startswith('0') or code.startswith('3'):
                    xt_code = f"{code}.SZ"
                elif code.startswith('sh'):
                    xt_code = code.replace('sh', '') + '.SH'
                elif code.startswith('sz'):
                    xt_code = code.replace('sz', '') + '.SZ'
                else:
                    xt_code = code
                xt_codes.append(xt_code)
            
            # 订阅全推行情
            for xt_code in xt_codes:
                xtdata.subscribe_quote(xt_code)
                self.subscribed_codes.add(xt_code)
            
            logger.info(f"已订阅 {len(xt_codes)} 个标的")
            return True
            
        except Exception as e:
            logger.error(f"订阅行情失败: {e}")
            return False
    
    def unsubscribe(self, codes: List[str]):
        """取消订阅"""
        if not XTQUANT_AVAILABLE:
            return
        
        try:
            for code in codes:
                xtdata.unsubscribe_quote(code)
                self.subscribed_codes.discard(code)
                self.price_cache.pop(code, None)
            
            logger.info(f"已取消订阅 {len(codes)} 个标的")
        except Exception as e:
            logger.error(f"取消订阅失败: {e}")
    
    def get_full_tick(self, codes: List[str]) -> Dict[str, QMTPriceData]:
        """获取全推行情"""
        if not XTQUANT_AVAILABLE:
            return {}
        
        try:
            # 转换代码格式
            xt_codes = []
            for code in codes:
                if code.startswith('6'):
                    xt_code = f"{code}.SH"
                elif code.startswith('0') or code.startswith('3'):
                    xt_code = f"{code}.SZ"
                elif code.startswith('sh'):
                    xt_code = code.replace('sh', '') + '.SH'
                elif code.startswith('sz'):
                    xt_code = code.replace('sz', '') + '.SZ'
                else:
                    xt_code = code
                xt_codes.append(xt_code)
            
            # 获取行情
            result = {}
            for xt_code in xt_codes:
                # 先从缓存获取
                if xt_code in self.price_cache:
                    result[xt_code] = self.price_cache[xt_code]
                    continue
                
                # 从xtdata获取
                quote = xtdata.get_full_tick([xt_code])
                if quote and xt_code in quote:
                    data = quote[xt_code]
                    price_data = QMTPriceData(
                        code=xt_code,
                        name=data.get('name', ''),
                        price=round(data.get('lastPrice', 0), 2),
                        open=round(data.get('open', 0), 2),
                        high=round(data.get('high', 0), 2),
                        low=round(data.get('low', 0), 2),
                        pre_close=round(data.get('lastClose', 0), 2),
                        volume=int(data.get('volume', 0)),
                        amount=round(data.get('amount', 0), 2),
                        change_pct=round(
                            ((data.get('lastPrice', 0) - data.get('lastClose', 0)) / 
                             data.get('lastClose', 1) * 100), 2
                        ) if data.get('lastClose', 0) > 0 else 0,
                        timestamp=int(time.time() * 1000)
                    )
                    result[xt_code] = price_data
                    self.price_cache[xt_code] = price_data
            
            return result
            
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            return {}
    
    def get_kline_data(
        self,
        code: str,
        period: str = '1d',
        count: int = 100
    ) -> List[Dict]:
        """
        获取K线数据
        
        Args:
            code: 标的代码
            period: 周期 '1m','5m','15m','30m','1h','1d','1w'
            count: 获取条数
        """
        if not XTQUANT_AVAILABLE:
            return []
        
        try:
            # 转换代码格式
            if code.startswith('6'):
                xt_code = f"{code}.SH"
            elif code.startswith('0') or code.startswith('3'):
                xt_code = f"{code}.SZ"
            else:
                xt_code = code
            
            # 周期映射
            period_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '60m': '1h',
                'daily': '1d',
                'weekly': '1w',
            }
            xt_period = period_map.get(period, '1d')
            
            # 下载历史数据
            xtdata.download_history_data(xt_code, xt_period, count=count)
            
            # 获取本地数据
            klines = xtdata.get_local_data(
                field_list=['time', 'open', 'high', 'low', 'close', 'volume', 'amount'],
                stock_code=xt_code,
                period=xt_period,
                count=count
            )
            
            if not klines or xt_code not in klines:
                return []
            
            # 转换为标准格式
            df = klines[xt_code]
            result = []
            for _, row in df.iterrows():
                result.append({
                    'timestamp': int(row['time']),
                    'open': round(row['open'], 2),
                    'high': round(row['high'], 2),
                    'low': round(row['low'], 2),
                    'close': round(row['close'], 2),
                    'volume': int(row['volume']),
                    'amount': round(row['amount'], 2) if 'amount' in row else 0
                })
            
            return result
            
        except Exception as e:
            logger.error(f"获取K线数据失败: {e}")
            return []
    
    def get_stock_list(self, market: str = 'all') -> List[Dict]:
        """获取股票列表"""
        if not XTQUANT_AVAILABLE:
            return []
        
        try:
            if market == 'sh':
                stocks = xtdata.get_stock_list_in_sector('沪深A股')
                stocks = [s for s in stocks if s.endswith('.SH')]
            elif market == 'sz':
                stocks = xtdata.get_stock_list_in_sector('沪深A股')
                stocks = [s for s in stocks if s.endswith('.SZ')]
            else:
                stocks = xtdata.get_stock_list_in_sector('沪深A股')
            
            result = []
            for stock in stocks:
                code = stock.split('.')[0]
                result.append({
                    'code': code,
                    'xt_code': stock,
                    'market': 'SH' if stock.endswith('.SH') else 'SZ'
                })
            
            return result
            
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []


# 全局实例
qmt_service = QMTXtquantService()
