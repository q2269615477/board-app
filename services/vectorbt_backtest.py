"""
vectorbt深度集成 - 真实K线数据回测
"""

import logging
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger('vectorbt_backtest')

# 尝试导入 vectorbt（可选依赖；未装时回测接口诚实 503，非错误）
try:
    import vectorbt as vbt
    VBT_AVAILABLE = True
except ImportError:
    vbt = None  # type: ignore
    VBT_AVAILABLE = False
    logger.info("vectorbt 未安装（可选）。回测 API 保持 503/不可用，不影响面板与行情。")


@dataclass
class BacktestResult:
    """回测结果"""
    success: bool
    signals: List[Dict]
    metrics: Dict[str, float]
    equity_curve: List[Dict]
    trades: List[Dict]
    error: str = ""


class VectorBTBacktestService:
    """基于vectorbt的回测服务"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            base_dir = Path(__file__).resolve().parent.parent
            db_path = base_dir / 'data' / 'kline.db'
        self.db_path = str(db_path)
        self._strategies = {
            'sma_crossover': self._sma_crossover_strategy,
            'ema_crossover': self._ema_crossover_strategy,
            'macd': self._macd_strategy,
            'rsi': self._rsi_strategy,
            'bollinger': self._bollinger_strategy,
        }
    
    def _load_kline_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = 'daily'
    ) -> pd.DataFrame:
        """从SQLite加载K线数据"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            # 构建查询
            table_map = {
                '1m': 'kline_1m',
                '5m': 'kline_5m',
                '15m': 'kline_15m',
                '30m': 'kline_30m',
                '60m': 'kline_60m',
                'daily': 'kline_daily',
                'weekly': 'kline_weekly',
            }
            table = table_map.get(period, 'kline_daily')
            
            query = f"""
                SELECT timestamp, open, high, low, close, volume
                FROM {table}
                WHERE code = ? 
                AND date(timestamp/1000, 'unixepoch') BETWEEN ? AND ?
                ORDER BY timestamp ASC
            """
            
            df = pd.read_sql_query(
                query, 
                conn, 
                params=(symbol, start_date, end_date)
            )
            conn.close()
            
            if df.empty:
                logger.warning(f"未找到数据: {symbol} {start_date}~{end_date}")
                return pd.DataFrame()
            
            # 转换时间戳
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            # 确保数值类型
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df.dropna(inplace=True)
            
            logger.info(f"加载数据: {symbol}, {len(df)}条记录")
            return df
            
        except Exception as e:
            logger.error(f"加载K线数据失败: {e}")
            return pd.DataFrame()
    
    def run_backtest(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_code: str,
        params: Dict = None,
        period: str = 'daily',
        initial_capital: float = 100000.0
    ) -> BacktestResult:
        """
        运行vectorbt回测
        
        Args:
            symbol: 标的代码
            start_date: 开始日期 '2024-01-01'
            end_date: 结束日期 '2024-06-01'
            strategy_code: 策略代码
            params: 策略参数
            period: K线周期
            initial_capital: 初始资金
        """
        # 诚实下线：表名 kline_* 与真实单表 kline schema 未对齐前，禁止假/半真 metrics
        return BacktestResult(
            success=False,
            signals=[],
            metrics={},
            equity_curve=[],
            trades=[],
            error=(
                "BACKTEST_UNAVAILABLE: 回测引擎尚未对齐真实 K 线 schema，"
                "已停用模拟结果。请等待 vectorbt 与 kline 表打通后再用。"
            ),
        )

        # --- 以下为原实现，schema 对齐后恢复 ---
        if not VBT_AVAILABLE:  # pragma: no cover
            return BacktestResult(
                success=False,
                signals=[],
                metrics={},
                equity_curve=[],
                trades=[],
                error="vectorbt未安装"
            )
        
        try:
            # 加载数据
            df = self._load_kline_data(symbol, start_date, end_date, period)
            if df.empty:
                return BacktestResult(
                    success=False,
                    signals=[],
                    metrics={},
                    equity_curve=[],
                    trades=[],
                    error="无K线数据"
                )
            
            # 获取策略函数
            strategy_fn = self._strategies.get(strategy_code)
            if not strategy_fn:
                return BacktestResult(
                    success=False,
                    signals=[],
                    metrics={},
                    equity_curve=[],
                    trades=[],
                    error=f"未知策略: {strategy_code}"
                )
            
            # 执行策略
            entries, exits = strategy_fn(df, params or {})
            
            # 运行回测
            pf = vbt.Portfolio.from_signals(
                close=df['close'],
                entries=entries,
                exits=exits,
                init_cash=initial_capital,
                fees=0.001,  # 0.1%手续费
                slippage=0.001,  # 0.1%滑点
                freq='1D' if period == 'daily' else '1H'
            )
            
            # 提取结果
            signals = self._extract_signals(df, entries, exits)
            metrics = self._extract_metrics(pf)
            equity_curve = self._extract_equity_curve(pf)
            trades = self._extract_trades(pf)
            
            return BacktestResult(
                success=True,
                signals=signals,
                metrics=metrics,
                equity_curve=equity_curve,
                trades=trades
            )
            
        except Exception as e:
            logger.error(f"回测执行失败: {e}")
            return BacktestResult(
                success=False,
                signals=[],
                metrics={},
                equity_curve=[],
                trades=[],
                error=str(e)
            )
    
    def _sma_crossover_strategy(
        self,
        df: pd.DataFrame,
        params: Dict
    ) -> Tuple[pd.Series, pd.Series]:
        """双均线交叉策略"""
        fast_period = params.get('fastPeriod', 10)
        slow_period = params.get('slowPeriod', 50)
        
        fast_sma = df['close'].rolling(window=fast_period).mean()
        slow_sma = df['close'].rolling(window=slow_period).mean()
        
        entries = fast_sma > slow_sma
        exits = fast_sma < slow_sma
        
        return entries, exits
    
    def _ema_crossover_strategy(
        self,
        df: pd.DataFrame,
        params: Dict
    ) -> Tuple[pd.Series, pd.Series]:
        """EMA交叉策略"""
        fast_period = params.get('fastPeriod', 12)
        slow_period = params.get('slowPeriod', 26)
        
        fast_ema = df['close'].ewm(span=fast_period).mean()
        slow_ema = df['close'].ewm(span=slow_period).mean()
        
        entries = fast_ema > slow_ema
        exits = fast_ema < slow_ema
        
        return entries, exits
    
    def _macd_strategy(
        self,
        df: pd.DataFrame,
        params: Dict
    ) -> Tuple[pd.Series, pd.Series]:
        """MACD策略"""
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal = params.get('signal', 9)
        
        macd = df['close'].ewm(span=fast).mean() - df['close'].ewm(span=slow).mean()
        macd_signal = macd.ewm(span=signal).mean()
        
        entries = (macd > macd_signal) & (macd.shift(1) <= macd_signal.shift(1))
        exits = (macd < macd_signal) & (macd.shift(1) >= macd_signal.shift(1))
        
        return entries, exits
    
    def _rsi_strategy(
        self,
        df: pd.DataFrame,
        params: Dict
    ) -> Tuple[pd.Series, pd.Series]:
        """RSI策略"""
        period = params.get('period', 14)
        oversold = params.get('oversold', 30)
        overbought = params.get('overbought', 70)
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        entries = (rsi < oversold) & (rsi.shift(1) >= oversold)
        exits = (rsi > overbought) & (rsi.shift(1) <= overbought)
        
        return entries, exits
    
    def _bollinger_strategy(
        self,
        df: pd.DataFrame,
        params: Dict
    ) -> Tuple[pd.Series, pd.Series]:
        """布林带策略"""
        period = params.get('period', 20)
        std_dev = params.get('stdDev', 2)
        
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        entries = df['close'] < lower
        exits = df['close'] > upper
        
        return entries, exits
    
    def _extract_signals(
        self,
        df: pd.DataFrame,
        entries: pd.Series,
        exits: pd.Series
    ) -> List[Dict]:
        """提取买卖信号"""
        signals = []
        
        for timestamp in df.index[entries]:
            signals.append({
                'timestamp': int(timestamp.timestamp() * 1000),
                'date': timestamp.strftime('%Y-%m-%d'),
                'type': 'buy',
                'price': round(df.loc[timestamp, 'close'], 2)
            })
        
        for timestamp in df.index[exits]:
            signals.append({
                'timestamp': int(timestamp.timestamp() * 1000),
                'date': timestamp.strftime('%Y-%m-%d'),
                'type': 'sell',
                'price': round(df.loc[timestamp, 'close'], 2)
            })
        
        # 按时间排序
        signals.sort(key=lambda x: x['timestamp'])
        return signals
    
    def _extract_metrics(self, pf) -> Dict[str, float]:
        """提取回测指标"""
        try:
            return {
                'totalReturn': round(pf.total_return() * 100, 2),
                'annualizedReturn': round(pf.annualized_return() * 100, 2),
                'sharpeRatio': round(pf.sharpe_ratio(), 2),
                'maxDrawdown': round(pf.max_drawdown() * 100, 2),
                'winRate': round(pf.trades.win_rate() * 100, 2),
                'tradeCount': int(pf.trades.count()),
                'avgTradeReturn': round(pf.trades.returns.mean() * 100, 2) if pf.trades.count() > 0 else 0,
            }
        except:
            return {
                'totalReturn': 0,
                'annualizedReturn': 0,
                'sharpeRatio': 0,
                'maxDrawdown': 0,
                'winRate': 0,
                'tradeCount': 0,
                'avgTradeReturn': 0,
            }
    
    def _extract_equity_curve(self, pf) -> List[Dict]:
        """提取权益曲线"""
        try:
            equity = pf.value()
            return [
                {
                    'timestamp': int(idx.timestamp() * 1000),
                    'date': idx.strftime('%Y-%m-%d'),
                    'value': round(val, 2)
                }
                for idx, val in equity.items()
            ]
        except:
            return []
    
    def _extract_trades(self, pf) -> List[Dict]:
        """提取交易记录"""
        try:
            trades = pf.trades
            return [
                {
                    'entryTime': int(trades.entry_time[i].timestamp() * 1000),
                    'exitTime': int(trades.exit_time[i].timestamp() * 1000),
                    'entryPrice': round(trades.entry_price[i], 2),
                    'exitPrice': round(trades.exit_price[i], 2),
                    'size': round(trades.size[i], 2),
                    'pnl': round(trades.pnl[i], 2),
                    'return': round(trades.return_[i] * 100, 2),
                    'direction': 'long' if trades.direction[i] == 1 else 'short'
                }
                for i in range(len(trades))
            ]
        except:
            return []
    
    def get_available_strategies(self) -> List[Dict]:
        """获取可用策略列表"""
        return [
            {
                'code': 'sma_crossover',
                'name': '双均线交叉',
                'description': '基于SMA金叉/死叉',
                'params': {
                    'fastPeriod': {'type': 'int', 'default': 10, 'min': 5, 'max': 50},
                    'slowPeriod': {'type': 'int', 'default': 50, 'min': 20, 'max': 200}
                }
            },
            {
                'code': 'ema_crossover',
                'name': 'EMA交叉',
                'description': '基于EMA金叉/死叉',
                'params': {
                    'fastPeriod': {'type': 'int', 'default': 12, 'min': 5, 'max': 50},
                    'slowPeriod': {'type': 'int', 'default': 26, 'min': 20, 'max': 200}
                }
            },
            {
                'code': 'macd',
                'name': 'MACD',
                'description': '基于MACD柱状图',
                'params': {
                    'fast': {'type': 'int', 'default': 12, 'min': 5, 'max': 50},
                    'slow': {'type': 'int', 'default': 26, 'min': 20, 'max': 100},
                    'signal': {'type': 'int', 'default': 9, 'min': 5, 'max': 30}
                }
            },
            {
                'code': 'rsi',
                'name': 'RSI',
                'description': '基于RSI超买超卖',
                'params': {
                    'period': {'type': 'int', 'default': 14, 'min': 5, 'max': 50},
                    'oversold': {'type': 'int', 'default': 30, 'min': 10, 'max': 40},
                    'overbought': {'type': 'int', 'default': 70, 'min': 60, 'max': 90}
                }
            },
            {
                'code': 'bollinger',
                'name': '布林带',
                'description': '基于布林带通道',
                'params': {
                    'period': {'type': 'int', 'default': 20, 'min': 10, 'max': 50},
                    'stdDev': {'type': 'float', 'default': 2.0, 'min': 1.0, 'max': 3.0}
                }
            },
        ]


# 全局实例
vectorbt_service = VectorBTBacktestService()
