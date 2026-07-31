"""
MCP 请求处理器
处理智能体发来的工具调用请求

支持Pydantic模型参数和原始dict参数
"""

import json
import time
import logging
from typing import Any, Dict, Union

# 尝试导入Pydantic模型
try:
    from mcp.schemas import (
        SetSymbolParams,
        SetPeriodParams,
        CreateOverlayParams,
        RemoveOverlayParams,
        GetOverlaysParams,
        ScrollToTimestampParams,
        GetKlineDataParams,
        RunBacktestParams,
        GetCachedPricesParams,
    )
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

logger = logging.getLogger('mcp_handlers')


class MCPHandler:
    """MCP请求处理器（支持多会话隔离）"""
    
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._overlay_counters: dict[str, int] = {}

    def _get_session(self, session_id: str) -> dict:
        """获取或创建会话状态"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                'symbol': None,
                'period': 'daily',
                'visibleRange': None,
                'overlays': []
            }
            self._overlay_counters[session_id] = 0
        return self._sessions[session_id]

    @property
    def chart_state(self) -> dict:
        """向后兼容：返回 default 会话状态"""
        return self._get_session('default')
    
    def handle(self, tool_name: str, params: Union[Dict[str, Any], Any], session_id: str = 'default') -> Dict[str, Any]:
        """
        分发处理请求
        
        Args:
            tool_name: 工具名称
            params: 参数（可以是Pydantic模型实例或原始dict）
            session_id: 会话ID（用于多会话隔离）
        """
        handlers = {
            'set_symbol': self.handle_set_symbol,
            'set_period': self.handle_set_period,
            'get_chart_context': self.handle_get_chart_context,
            'create_overlay': self.handle_create_overlay,
            'get_overlays': self.handle_get_overlays,
            'remove_overlay': self.handle_remove_overlay,
            'scroll_to_timestamp': self.handle_scroll_to_timestamp,
            'get_kline_data': self.handle_get_kline_data,
            'run_backtest': self.handle_run_backtest,
            'get_cached_prices': self.handle_get_cached_prices,
            'search_cases': self.handle_search_cases,
            'get_case': self.handle_get_case,
            'search_relations': self.handle_search_relations,
            'get_relation': self.handle_get_relation,
            'list_due_reminders': self.handle_list_due_reminders,
            'list_sessions': self.handle_list_sessions,
            'get_session': self.handle_get_session,
        }
        
        handler = handlers.get(tool_name)
        if not handler:
            return {'success': False, 'error': f'未知工具: {tool_name}'}
        
        try:
            return handler(params, session_id)
        except Exception as e:
            logger.error(f"[{tool_name}] 处理失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _get_param(self, params: Union[Dict, Any], key: str, default=None):
        """从参数中获取值（支持Pydantic模型和dict）"""
        if hasattr(params, key):
            return getattr(params, key, default)
        if isinstance(params, dict):
            # 尝试驼峰和下划线两种格式
            return params.get(key, params.get(self._to_camel(key), default))
        return default
    
    def _to_camel(self, snake: str) -> str:
        """下划线转驼峰"""
        parts = snake.split('_')
        return parts[0] + ''.join(p.capitalize() for p in parts[1:])
    
    # ============================================================
    # 工具处理器
    # ============================================================
    
    def handle_set_symbol(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """处理切换标的"""
        symbol = self._get_param(params, 'symbol')
        symbol_type = self._get_param(params, 'symbol_type', 'stock')
        
        if not symbol:
            return {'success': False, 'error': '缺少symbol参数'}
        
        state = self._get_session(session_id)
        state['symbol'] = symbol
        state['type'] = symbol_type
        
        logger.info(f"[MCP] 切换标的: {symbol} ({symbol_type}) [session={session_id}]")
        
        return {
            'success': True,
            'symbol': symbol,
            'type': symbol_type,
            'message': f'已切换到: {symbol}'
        }
    
    def handle_set_period(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """处理切换周期"""
        period = self._get_param(params, 'period')
        
        if not period:
            return {'success': False, 'error': '缺少period参数'}
        
        state = self._get_session(session_id)
        state['period'] = period
        
        logger.info(f"[MCP] 切换周期: {period} [session={session_id}]")
        
        return {
            'success': True,
            'period': period,
            'message': f'已切换到: {period}'
        }
    
    def handle_get_chart_context(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """获取图表上下文"""
        state = self._get_session(session_id)
        return {
            'success': True,
            'context': state.copy()
        }
    
    def handle_create_overlay(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """处理创建画线"""
        overlay_type = self._get_param(params, 'type')
        points = self._get_param(params, 'points', [])
        styles = self._get_param(params, 'styles') or self._get_param(params, 'styles', {})
        extend_data = self._get_param(params, 'extend_data') or self._get_param(params, 'extendData', {})
        
        if not overlay_type:
            return {'success': False, 'error': '缺少type参数'}
        
        if not points:
            return {'success': False, 'error': '缺少points参数'}
        
        # 转换Pydantic模型为字典
        points_data = []
        for p in points:
            if hasattr(p, 'model_dump'):
                points_data.append(p.model_dump())
            elif hasattr(p, 'dict'):
                points_data.append(p.dict())
            else:
                points_data.append(dict(p) if not isinstance(p, dict) else p)
        
        state = self._get_session(session_id)
        self._overlay_counters[session_id] = self._overlay_counters.get(session_id, 0) + 1
        overlay_id = f"overlay_{self._overlay_counters[session_id]}_{int(time.time())}"
        
        overlay = {
            'id': overlay_id,
            'type': overlay_type,
            'points': points_data,
            'styles': dict(styles) if hasattr(styles, 'dict') else styles,
            'extendData': dict(extend_data) if hasattr(extend_data, 'dict') else extend_data,
            'createdAt': int(time.time() * 1000)
        }
        
        state['overlays'].append(overlay)
        
        logger.info(f"[MCP] 创建画线: {overlay_type} ({overlay_id}) [session={session_id}]")
        
        return {
            'success': True,
            'overlayId': overlay_id,
            'overlay': overlay,
            'message': f'已创建画线: {overlay_type}'
        }
    
    def handle_get_overlays(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """获取所有画线"""
        symbol = self._get_param(params, 'symbol')
        state = self._get_session(session_id)
        
        # 如果指定了symbol且与当前不同，提示
        if symbol and symbol != state.get('symbol'):
            return {
                'success': True,
                'overlays': [],
                'symbol': symbol,
                'note': '当前未加载该标的，画线列表为空',
                'current_symbol': state.get('symbol')
            }
        
        return {
            'success': True,
            'overlays': state['overlays'].copy(),
            'count': len(state['overlays']),
            'symbol': state.get('symbol')
        }
    
    def handle_remove_overlay(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """删除画线"""
        overlay_id = self._get_param(params, 'overlay_id') or self._get_param(params, 'overlayId')
        
        if not overlay_id:
            return {'success': False, 'error': '缺少overlayId参数'}
        
        state = self._get_session(session_id)
        original_count = len(state['overlays'])
        state['overlays'] = [
            o for o in state['overlays'] 
            if o['id'] != overlay_id
        ]
        removed = len(state['overlays']) < original_count
        
        logger.info(f"[MCP] 删除画线: {overlay_id} ({'成功' if removed else '未找到'}) [session={session_id}]")
        
        return {
            'success': True,
            'removed': removed,
            'removedId': overlay_id,
            'message': f'已删除画线: {overlay_id}' if removed else f'画线不存在: {overlay_id}'
        }
    
    def handle_scroll_to_timestamp(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """滚动到指定时间"""
        timestamp = self._get_param(params, 'timestamp')
        
        if not timestamp:
            return {'success': False, 'error': '缺少timestamp参数'}
        
        return {
            'success': True,
            'timestamp': timestamp,
            'message': f'已滚动到: {timestamp}'
        }
    
    def handle_get_kline_data(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """获取K线数据 — 从KLineService获取真实数据"""
        symbol = self._get_param(params, 'symbol') or self._get_param(params, 'code', '')
        period = self._get_param(params, 'period', 'daily')
        start_date = self._get_param(params, 'start_date') or self._get_param(params, 'startDate')
        end_date = self._get_param(params, 'end_date') or self._get_param(params, 'endDate')
        count = self._get_param(params, 'count')
        
        if not symbol:
            return {'success': False, 'error': '缺少symbol/code参数'}
        
        try:
            from services.kline_service import get_kline_service
            ks = get_kline_service()
            
            # 推断type
            dtype = self._get_param(params, 'type', '')
            if not dtype:
                if symbol.startswith('sh') or symbol.startswith('sz'):
                    dtype = 'index'
                elif symbol.startswith('BK'):
                    dtype = 'industry'
                else:
                    dtype = 'stock'
            
            result, _ = ks.get_kline(dtype, symbol, period)
            data = result.get('data', [])
            
            # 如果指定了日期范围，进行过滤
            if start_date or end_date:
                from datetime import datetime
                filtered = []
                for bar in data:
                    ts = bar.get('timestamp', 0)
                    date_str = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                    if start_date and date_str < start_date:
                        continue
                    if end_date and date_str > end_date:
                        continue
                    filtered.append(bar)
                data = filtered
            
            # 如果指定了count，限制返回条数
            if count and count > 0:
                data = data[-count:]
            
            logger.info(f"[MCP] 获取K线: {symbol} {period}, {len(data)}条")
            
            return {
                'success': True,
                'symbol': symbol,
                'period': period,
                'data': data,
                'count': len(data),
                'message': f'K线数据获取成功: {len(data)}条'
            }
        except Exception as e:
            logger.error(f"[MCP] 获取K线失败: {e}")
            return {
                'success': False,
                'symbol': symbol,
                'error': str(e),
                'data': []
            }
    
    def handle_run_backtest(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """运行回测 — 诚实下线（未对齐 schema 前禁止假 metrics）"""
        symbol = self._get_param(params, 'symbol', '')
        strategy_code = self._get_param(params, 'strategy_code') or self._get_param(params, 'strategyCode', '')
        try:
            from services.backtest_service import backtest_service
            start_date = self._get_param(params, 'start_date') or self._get_param(params, 'startDate', '')
            end_date = self._get_param(params, 'end_date') or self._get_param(params, 'endDate', '')
            strategy_params = self._get_param(params, 'params', {}) or {}
            result = backtest_service.run_backtest(
                symbol=symbol or '',
                start_date=start_date or '',
                end_date=end_date or '',
                strategy_code=strategy_code or '',
                params=strategy_params if isinstance(strategy_params, dict) else {},
            )
            return result
        except Exception as e:
            logger.error(f"[MCP] 回测失败: {e}")
            return {
                'success': False,
                'ok': False,
                'code': 'BACKTEST_UNAVAILABLE',
                'error': str(e),
                'signals': [],
                'metrics': {},
            }

    def handle_search_cases(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """检索 Case — 仅返回用户原文，不自动判定（Agent 学习入口）"""
        from services.annotation_service import get_annotation_service
        svc = get_annotation_service()
        q = self._get_param(params, 'q')
        symbol = self._get_param(params, 'symbol')
        period = self._get_param(params, 'period')
        type_ = self._get_param(params, 'type')
        limit = int(self._get_param(params, 'limit', 20) or 20)
        if q:
            items = svc.search_cases(q, limit=limit)
        else:
            items = svc.list_cases(symbol=symbol, period=period, type_=type_, limit=limit)
        # 列表项附带 vault 路径摘要，便于 Agent 再 get_case 拉全文
        slim = []
        for it in items:
            slim.append({
                'id': it.get('id'),
                'type': it.get('type'),
                'symbol': it.get('symbol'),
                'symbol_name': it.get('symbol_name'),
                'period': it.get('period'),
                'notes': it.get('notes'),
                'level': it.get('level'),
                'price_element': it.get('price_element') or (it.get('source_bar') or {}).get('price_element'),
                'source_bar': it.get('source_bar'),
                'overlay_count': len(it.get('overlays') or []),
                'vault': it.get('vault'),
                'updated_at': it.get('updated_at'),
            })
        return {
            'success': True,
            'data': slim,
            'count': len(slim),
            'agent_hint': (
                '复述用户 notes/源K/水平位/反应点原文；禁止自动生成共振或反向结论。'
                '深入学习请对命中 id 调用 get_case 读取 vault_md。'
            ),
            'learning_mode': 'retrieval_augmented_quote_only',
        }

    def handle_get_case(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.annotation_service import get_annotation_service
        case_id = self._get_param(params, 'case_id') or self._get_param(params, 'caseId')
        if not case_id:
            return {'success': False, 'error': '缺少 case_id'}
        case = get_annotation_service().get_case(case_id)
        if not case:
            return {'success': False, 'error': f'case 不存在: {case_id}'}
        # 附带 vault md 正文，供 Agent 检索增强学习（只复述，不改写）
        data = dict(case)
        md_body = None
        try:
            abs_md = (case.get('vault') or {}).get('abs_md')
            if abs_md:
                from pathlib import Path
                p = Path(abs_md)
                if p.is_file():
                    md_body = p.read_text(encoding='utf-8')
            if md_body is None:
                from services import vault_writer
                rel = (case.get('vault') or {}).get('md_relpath')
                if rel:
                    p2 = vault_writer.vault_root() / rel
                    if p2.is_file():
                        md_body = p2.read_text(encoding='utf-8')
                        data.setdefault('vault', {})['abs_md'] = str(p2)
        except Exception:
            md_body = None
        if md_body is not None:
            data['vault_md'] = md_body
        return {
            'success': True,
            'data': data,
            'agent_hint': '仅复述用户字段与 vault_md 原文；禁止自动判定对错或生成共振结论',
            'learning_mode': 'retrieval_augmented_quote_only',
        }

    def handle_search_relations(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.annotation_service import get_annotation_service
        svc = get_annotation_service()
        q = self._get_param(params, 'q')
        limit = int(self._get_param(params, 'limit', 20) or 20)
        items = svc.search_relations(q or '', limit=limit)
        return {
            'success': True,
            'data': items,
            'count': len(items),
            'agent_hint': 'relation_note 为用户声明结论，系统未验证、未自动判定',
        }

    def handle_get_relation(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.annotation_service import get_annotation_service
        rid = self._get_param(params, 'relation_id') or self._get_param(params, 'relationId')
        if not rid:
            return {'success': False, 'error': '缺少 relation_id'}
        rel = get_annotation_service().get_relation(rid)
        if not rel:
            return {'success': False, 'error': f'relation 不存在: {rid}'}
        return {
            'success': True,
            'data': rel,
            'agent_hint': '只引用 relation_note 原文',
        }

    def handle_list_due_reminders(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.annotation_service import get_annotation_service
        items = get_annotation_service().list_due_reminders()
        return {'success': True, 'data': items, 'count': len(items)}

    def handle_list_sessions(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.session_service import get_session_service
        limit = int(self._get_param(params, 'limit', 30) or 30)
        items = get_session_service().list_sessions(limit=limit)
        return {
            'success': True,
            'data': items,
            'count': len(items),
            'agent_hint': '会话彼此独立；深入请 get_session',
            'learning_mode': 'retrieval_augmented_quote_only',
        }

    def handle_get_session(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        from services.session_service import get_session_service
        sid = self._get_param(params, 'session_id') or self._get_param(params, 'sessionId')
        if not sid:
            return {'success': False, 'error': '缺少 session_id'}
        sess = get_session_service().get_session(sid)
        if not sess:
            return {'success': False, 'error': f'会话不存在: {sid}'}
        data = dict(sess)
        # 附带 vault session.md
        try:
            abs_md = (sess.get('vault') or {}).get('abs_md')
            if abs_md:
                from pathlib import Path
                p = Path(abs_md)
                if p.is_file():
                    data['vault_md'] = p.read_text(encoding='utf-8')
        except Exception:
            pass
        return {
            'success': True,
            'data': data,
            'agent_hint': '仅复述会话内用户 notes/选K/因果箭头/几何，禁止自动判定',
            'learning_mode': 'retrieval_augmented_quote_only',
        }
    
    def handle_get_cached_prices(self, params: Union[Dict, Any], session_id: str = 'default') -> Dict[str, Any]:
        """获取缓存价格 — 从QMT缓存服务获取真实数据"""
        codes = self._get_param(params, 'codes', [])
        
        if not codes:
            return {'success': True, 'prices': {}}
        
        try:
            from services.qmt_cache_service import qmt_cache_service
            prices = qmt_cache_service.get_cached_prices(codes)
            
            # 如果缓存中没有数据，尝试直接从QMT获取
            missing = [c for c in codes if prices.get(c) is None]
            if missing:
                try:
                    from data.qmt_client import get_qmt_client
                    from core.lifecycle import is_qmt_available
                    if is_qmt_available():
                        client = get_qmt_client()
                        raw = client.get_constituents_batch(missing)
                        if not raw:
                            raw = client.get_constituents_live(missing)
                        import time as _t
                        now_ms = int(_t.time() * 1000)
                        for code, info in raw.items():
                            prices[code] = {
                                'price': round(info.get('close', 0), 2),
                                'changePct': round(info.get('change_pct', 0), 2),
                                'volume': info.get('volume', 0),
                                'timestamp': now_ms
                            }
                except Exception:
                    pass  # QMT不可用时返回空
            
            return {
                'success': True,
                'prices': prices,
                'count': len(prices)
            }
        except Exception as e:
            logger.error(f"[MCP] 获取缓存价格失败: {e}")
            return {
                'success': False,
                'error': str(e),
                'prices': {code: None for code in codes}
            }
