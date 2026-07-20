"""
回测服务
基于vectorbt的策略回测
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from data import sqlite_repo

logger = logging.getLogger('backtest')


class BacktestService:
    """回测服务"""
    
    def __init__(self):
        self._strategies = {
            'sma_crossover': self._sma_crossover_strategy,
            'ema_crossover': self._ema_crossover_strategy,
            'macd': self._macd_strategy,
        }
    
    def run_backtest(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strategy_code: str,
        params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        运行回测
        
        Args:
            symbol: 标的代码
            start_date: 开始日期 '2024-01-01'
            end_date: 结束日期 '2024-06-01'
            strategy_code: 策略代码
            params: 策略参数
        
        Returns:
            回测结果
        """
        # 诚实下线：未对齐真实 K 线 schema / vectorbt 前，禁止返回模拟 metrics
        logger.warning(
            "backtest unavailable: symbol=%s strategy=%s", symbol, strategy_code
        )
        return {
            'success': False,
            'ok': False,
            'code': 'BACKTEST_UNAVAILABLE',
            'error': (
                '回测引擎尚未对齐真实 K 线数据，已停用模拟结果。'
                '请等待 vectorbt 与 kline 表打通后再用。'
            ),
            'http_status': 503,
        }

    
    def _sma_crossover_strategy(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """双均线交叉策略"""
        fast_period = params.get('fastPeriod', 10)
        slow_period = params.get('slowPeriod', 50)
        
        # TODO: 使用vectorbt实现
        # 模拟信号
        signals = [
            {
                'timestamp': 1717200000000,
                'type': 'buy',
                'price': 3050.25,
                'reason': f'SMA{fast_period}上穿SMA{slow_period}'
            },
            {
                'timestamp': 1719800000000,
                'type': 'sell',
                'price': 3100.50,
                'reason': f'SMA{fast_period}下穿SMA{slow_period}'
            }
        ]
        
        return signals
    
    def _ema_crossover_strategy(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """EMA交叉策略"""
        fast_period = params.get('fastPeriod', 12)
        slow_period = params.get('slowPeriod', 26)
        
        # TODO: 使用vectorbt实现
        return []
    
    def _macd_strategy(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """MACD策略"""
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal = params.get('signal', 9)
        
        # TODO: 使用vectorbt实现
        return []
    
    def _calculate_metrics(self, signals: List[Dict[str, Any]]) -> Dict[str, float]:
        """计算回测指标"""
        # TODO: 使用vectorbt计算真实指标
        return {
            'totalReturn': 15.5,  # 总收益率 %
            'annualizedReturn': 31.0,  # 年化收益率 %
            'sharpeRatio': 1.2,  # 夏普比率
            'maxDrawdown': -8.5,  # 最大回撤 %
            'winRate': 65.0,  # 胜率 %
            'tradeCount': len(signals) // 2  # 交易次数
        }
    
    def get_available_strategies(self) -> List[Dict[str, str]]:
        """获取可用策略列表"""
        return [
            {'code': 'sma_crossover', 'name': '双均线交叉', 'description': '基于SMA金叉/死叉'},
            {'code': 'ema_crossover', 'name': 'EMA交叉', 'description': '基于EMA金叉/死叉'},
            {'code': 'macd', 'name': 'MACD', 'description': '基于MACD柱状图'},
        ]


# 全局回测服务实例
backtest_service = BacktestService()
