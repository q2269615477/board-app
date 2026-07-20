"""
AI服务集成 - 接入LLM处理流程
将图表事件上报接入LLM进行智能分析
"""

import os
import json
import logging
import asyncio
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger('ai_integration')


@dataclass
class ChartEvent:
    """图表事件"""
    type: str
    timestamp: int
    data: Dict
    context: Dict
    
    def to_dict(self) -> Dict:
        return {
            'type': self.type,
            'timestamp': self.timestamp,
            'data': self.data,
            'context': self.context
        }


@dataclass
class AIAnalysisResult:
    """AI分析结果"""
    success: bool
    action: str
    reasoning: str
    confidence: float
    suggestions: List[Dict]
    error: str = ""


class AIIntegrationService:
    """AI集成服务"""
    
    def __init__(self):
        self.event_handlers: Dict[str, Callable] = {}
        self.analysis_history: List[Dict] = []
        self.max_history = 100
        self.llm_client = None
        self._init_llm_client()
    
    def _init_llm_client(self):
        """初始化LLM客户端 - 支持多种配置方式"""
        try:
            # 优先级1: WorkBuddy/ManAI8 自定义API (OPENAI_BASE_URL + OPENAI_API_KEY)
            base_url = os.environ.get('OPENAI_BASE_URL')
            openai_key = os.environ.get('OPENAI_API_KEY')
            if base_url and openai_key:
                import openai
                self.llm_client = ('openai_custom', {
                    'base_url': base_url.rstrip('/'),
                    'api_key': openai_key,
                    'model': os.environ.get('OPENAI_MODEL', 'gpt-4')
                })
                logger.info(f"WorkBuddy API已初始化: {base_url}")
                return
            
            # 优先级2: 标准OpenAI
            if openai_key and not base_url:
                import openai
                openai.api_key = openai_key
                self.llm_client = ('openai', openai)
                logger.info("OpenAI客户端已初始化")
                return
            
            # 优先级3: Anthropic Claude
            anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
            if anthropic_key:
                try:
                    import anthropic
                    self.llm_client = ('anthropic', anthropic.Anthropic(api_key=anthropic_key))
                    logger.info("Anthropic客户端已初始化")
                    return
                except ImportError:
                    pass
            
            # 优先级4: 本地Ollama
            try:
                import requests
                response = requests.get('http://localhost:11434/api/tags', timeout=2)
                if response.status_code == 200:
                    self.llm_client = ('ollama', 'http://localhost:11434')
                    logger.info("Ollama客户端已初始化")
                    return
            except:
                pass
            
            # 回退: 使用模拟模式（无 API key / 本地 Ollama 时属预期）
            self.llm_client = ('mock', None)
            logger.info("未配置 LLM（可选）。AI 分析走模拟/降级，不影响行情与面板。")
            
        except Exception as e:
            logger.error(f"初始化LLM客户端失败: {e}")
            self.llm_client = ('mock', None)
    
    async def process_event(self, event: ChartEvent) -> AIAnalysisResult:
        """
        处理图表事件
        
        Args:
            event: 图表事件
            
        Returns:
            AI分析结果
        """
        try:
            # 记录事件
            self._log_event(event)
            
            # 根据事件类型选择处理方式
            handler = self.event_handlers.get(event.type)
            if handler:
                return await handler(event)
            
            # 默认使用LLM分析
            return await self._llm_analyze(event)
            
        except Exception as e:
            logger.error(f"处理事件失败: {e}")
            return AIAnalysisResult(
                success=False,
                action="none",
                reasoning=f"处理失败: {str(e)}",
                confidence=0,
                suggestions=[]
            )
    
    async def _llm_analyze(self, event: ChartEvent) -> AIAnalysisResult:
        """使用LLM分析事件"""
        
        # 构建提示词
        prompt = self._build_prompt(event)
        
        # 调用LLM
        llm_type, llm_instance = self.llm_client
        
        if llm_type == 'openai':
            return await self._call_openai(prompt, event)
        elif llm_type == 'openai_custom':
            return await self._call_openai_custom(prompt, event, llm_instance)
        elif llm_type == 'anthropic':
            return await self._call_anthropic(prompt, event)
        elif llm_type == 'ollama':
            return await self._call_ollama(prompt, event)
        else:
            # 模拟模式
            return self._mock_analysis(event)
    
    def _build_prompt(self, event: ChartEvent) -> str:
        """构建LLM提示词"""
        
        base_prompt = f"""你是一位专业的股票技术分析AI助手。请分析以下图表事件并给出建议。

事件类型: {event.type}
时间: {datetime.fromtimestamp(event.timestamp/1000).strftime('%Y-%m-%d %H:%M:%S')}

上下文信息:
- 标的: {event.context.get('symbol', 'unknown')}
- 周期: {event.context.get('period', 'unknown')}
- 当前价格: {event.context.get('currentPrice', 'unknown')}

事件数据:
{json.dumps(event.data, indent=2, ensure_ascii=False)}

历史画线:
{json.dumps(event.context.get('overlays', []), indent=2, ensure_ascii=False)}

请分析:
1. 这个事件的技术含义
2. 可能的交易机会或风险
3. 建议的操作（如果有）
4. 置信度（0-1）

请以JSON格式返回:
{{
    "action": "buy|sell|hold|none",
    "reasoning": "分析理由",
    "confidence": 0.8,
    "suggestions": [
        {{"type": "create_overlay", "params": {{}}, "reason": ""}}
    ]
}}
"""
        return base_prompt
    
    async def _call_openai(self, prompt: str, event: ChartEvent) -> AIAnalysisResult:
        """调用OpenAI API（兼容v1.x和v0.x）"""
        try:
            import openai
            
            # v1.x API
            if hasattr(openai, 'OpenAI'):
                client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
                response = client.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是专业的股票技术分析AI助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1000
                )
                content = response.choices[0].message.content
            else:
                # v0.x fallback (deprecated)
                response = await openai.ChatCompletion.acreate(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "你是专业的股票技术分析AI助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=1000
                )
                content = response.choices[0].message.content
            
            return self._parse_llm_response(content)
            
        except Exception as e:
            logger.error(f"OpenAI调用失败: {e}")
            return self._mock_analysis(event)
    
    async def _call_openai_custom(self, prompt: str, event: ChartEvent, config: dict) -> AIAnalysisResult:
        """调用自定义OpenAI兼容API（如WorkBuddy/ManAI8）"""
        try:
            import openai
            
            client = openai.OpenAI(
                base_url=config['base_url'],
                api_key=config['api_key']
            )
            
            response = client.chat.completions.create(
                model=config['model'],
                messages=[
                    {"role": "system", "content": "你是专业的股票技术分析AI助手。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            content = response.choices[0].message.content
            return self._parse_llm_response(content)
            
        except Exception as e:
            logger.error(f"自定义API调用失败: {e}")
            return self._mock_analysis(event)
    
    async def _call_anthropic(self, prompt: str, event: ChartEvent) -> AIAnalysisResult:
        """调用Anthropic Claude API（使用asyncio.to_thread避免阻塞事件循环）"""
        try:
            _, client = self.llm_client
            
            # 使用asyncio.to_thread包装同步调用，避免阻塞事件循环
            def _sync_call():
                return client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=1000,
                    temperature=0.3,
                    system="你是专业的股票技术分析AI助手。",
                    messages=[{"role": "user", "content": prompt}]
                )
            
            response = await asyncio.to_thread(_sync_call)
            content = response.content[0].text
            return self._parse_llm_response(content)
            
        except Exception as e:
            logger.error(f"Anthropic调用失败: {e}")
            return self._mock_analysis(event)
    
    async def _call_ollama(self, prompt: str, event: ChartEvent) -> AIAnalysisResult:
        """调用本地Ollama"""
        try:
            import requests
            
            _, base_url = self.llm_client
            response = requests.post(
                f"{base_url}/api/generate",
                json={
                    "model": "llama3",
                    "prompt": prompt,
                    "stream": False
                },
                timeout=30
            )
            
            if response.status_code == 200:
                content = response.json().get('response', '')
                return self._parse_llm_response(content)
            else:
                raise Exception(f"Ollama返回错误: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Ollama调用失败: {e}")
            return self._mock_analysis(event)
    
    def _parse_llm_response(self, content: str) -> AIAnalysisResult:
        """解析LLM响应"""
        try:
            # 提取JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
                
                return AIAnalysisResult(
                    success=True,
                    action=data.get('action', 'none'),
                    reasoning=data.get('reasoning', ''),
                    confidence=data.get('confidence', 0.5),
                    suggestions=data.get('suggestions', [])
                )
            else:
                raise Exception("未找到JSON响应")
                
        except Exception as e:
            logger.error(f"解析LLM响应失败: {e}")
            return AIAnalysisResult(
                success=False,
                action="none",
                reasoning="解析失败",
                confidence=0,
                suggestions=[]
            )
    
    def _mock_analysis(self, event: ChartEvent) -> AIAnalysisResult:
        """模拟分析（当LLM不可用时）"""
        
        # 基于事件类型的简单规则
        if event.type == 'candle_clicked':
            return AIAnalysisResult(
                success=True,
                action="none",
                reasoning="用户点击了K线，等待进一步操作",
                confidence=0.5,
                suggestions=[
                    {"type": "info", "message": "可在此位置画线标记"}
                ]
            )
        
        elif event.type == 'overlay_created':
            overlay_type = event.data.get('type', '')
            if overlay_type == 'horizontalLine':
                price = event.data.get('points', [{}])[0].get('value', 0)
                return AIAnalysisResult(
                    success=True,
                    action="none",
                    reasoning=f"创建了水平线支撑位/阻力位在 {price}",
                    confidence=0.7,
                    suggestions=[
                        {"type": "watch", "message": f"关注价格 {price} 的突破情况"}
                    ]
                )
        
        elif event.type == 'visible_range_changed':
            return AIAnalysisResult(
                success=True,
                action="none",
                reasoning="图表视图已更新",
                confidence=0.3,
                suggestions=[]
            )
        
        return AIAnalysisResult(
            success=True,
            action="none",
            reasoning="收到事件",
            confidence=0.5,
            suggestions=[]
        )
    
    def _log_event(self, event: ChartEvent):
        """记录事件到历史"""
        self.analysis_history.append({
            'timestamp': datetime.now().isoformat(),
            'event': event.to_dict()
        })
        
        # 限制历史记录大小
        if len(self.analysis_history) > self.max_history:
            self.analysis_history = self.analysis_history[-self.max_history:]
    
    def register_handler(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        self.event_handlers[event_type] = handler
        logger.info(f"注册事件处理器: {event_type}")
    
    def get_analysis_history(self, limit: int = 20) -> List[Dict]:
        """获取分析历史"""
        return self.analysis_history[-limit:]
    
    async def analyze_chart_context(self, context: Dict) -> AIAnalysisResult:
        """
        分析图表整体上下文
        
        Args:
            context: 图表上下文
            
        Returns:
            AI分析结果
        """
        prompt = f"""请对当前图表进行综合分析：

标的: {context.get('symbol')}
周期: {context.get('period')}

画线分析:
{json.dumps(context.get('overlays', []), indent=2, ensure_ascii=False)}

请给出：
1. 当前技术形态判断
2. 关键支撑阻力位
3. 操作建议
4. 风险提示

以JSON格式返回分析结果。
"""
        
        event = ChartEvent(
            type='context_analysis',
            timestamp=int(datetime.now().timestamp() * 1000),
            data=context,
            context=context
        )
        
        return await self._llm_analyze(event)


# 全局实例
ai_service = AIIntegrationService()
