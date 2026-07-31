"""
MCP Tools 定义
定义智能体可调用的所有工具

使用Pydantic进行参数校验（推荐）
保留旧版手动校验作为fallback
"""

from typing import Any, Dict, Tuple, Optional
import logging

logger = logging.getLogger('mcp_tools')

# 尝试导入Pydantic校验
try:
    from mcp.schemas import (
        validate_tool_params as pydantic_validate,
        get_tool_schema as pydantic_get_schema,
        TOOL_SCHEMAS,
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
    logger.info("[MCP] Pydantic参数校验已启用")
except ImportError as e:
    PYDANTIC_AVAILABLE = False
    logger.warning(f"[MCP] Pydantic未安装，使用手动校验: {e}")


# ============================================================
# 工具定义（保留旧格式用于兼容性）
# ============================================================

TOOLS = {
    "set_symbol": {
        "name": "set_symbol",
        "description": "切换图表显示的标的（股票/指数/板块）",
        "parameters": {
            "symbol": {
                "type": "string",
                "description": "标的代码，如 '600519' 或 'sh000001'"
            },
            "symbolType": {
                "type": "string",
                "enum": ["stock", "index", "board"],
                "description": "标的类型"
            }
        },
        "required": ["symbol"]
    },
    
    "set_period": {
        "name": "set_period",
        "description": "切换K线周期",
        "parameters": {
            "period": {
                "type": "string",
                "enum": ["1m", "5m", "15m", "30m", "60m", "daily", "weekly", "monthly"],
                "description": "周期类型"
            }
        },
        "required": ["period"]
    },
    
    "get_chart_context": {
        "name": "get_chart_context",
        "description": "获取当前图表完整上下文",
        "parameters": {},
        "returns": {
            "symbol": "当前标的",
            "period": "当前周期",
            "visibleRange": "可见范围",
            "overlays": "所有画线",
            "klines": "K线数据"
        }
    },
    
    "create_overlay": {
        "name": "create_overlay",
        "description": "在图表上创建画线（水平线/趋势线/矩形等）",
        "parameters": {
            "type": {
                "type": "string",
                "enum": ["horizontalLine", "verticalLine", "trendLine", "rayLine", 
                        "segmentLine", "rect", "fibonacci", "text", "icon"],
                "description": "画线类型"
            },
            "points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "integer"},
                        "value": {"type": "number"}
                    }
                },
                "description": "点位数组，包含时间戳和价格"
            },
            "styles": {
                "type": "object",
                "description": "样式配置（颜色、线宽等）"
            },
            "extendData": {
                "type": "object",
                "description": "扩展数据（如语义标记）"
            }
        },
        "required": ["type", "points"]
    },
    
    "get_overlays": {
        "name": "get_overlays",
        "description": "获取图表上所有画线",
        "parameters": {
            "symbol": {
                "type": "string",
                "description": "标的代码（可选，默认使用当前标的）"
            }
        },
        "returns": {
            "overlays": "画线数组",
            "count": "画线数量",
            "symbol": "标的代码"
        }
    },
    
    "remove_overlay": {
        "name": "remove_overlay",
        "description": "删除指定画线",
        "parameters": {
            "overlayId": {
                "type": "string",
                "description": "画线ID"
            },
            "symbol": {
                "type": "string",
                "description": "标的代码（可选）"
            }
        },
        "required": ["overlayId"]
    },
    
    "scroll_to_timestamp": {
        "name": "scroll_to_timestamp",
        "description": "滚动图表到指定时间",
        "parameters": {
            "timestamp": {
                "type": "integer",
                "description": "目标时间戳（毫秒）"
            }
        },
        "required": ["timestamp"]
    },
    
    "get_kline_data": {
        "name": "get_kline_data",
        "description": "获取K线历史数据",
        "parameters": {
            "symbol": {"type": "string", "description": "标的代码"},
            "period": {"type": "string", "description": "周期", "default": "daily"},
            "startDate": {"type": "string", "format": "date", "description": "开始日期"},
            "endDate": {"type": "string", "format": "date", "description": "结束日期"},
            "count": {"type": "integer", "description": "获取条数", "minimum": 1, "maximum": 5000}
        },
        "required": ["symbol"]
    },
    
    "run_backtest": {
        "name": "run_backtest",
        "description": "运行策略回测",
        "parameters": {
            "symbol": {"type": "string", "description": "标的代码"},
            "startDate": {"type": "string", "description": "开始日期（YYYY-MM-DD）"},
            "endDate": {"type": "string", "description": "结束日期（YYYY-MM-DD）"},
            "strategyCode": {"type": "string", "description": "策略代码"},
            "params": {"type": "object", "description": "策略参数"},
            "period": {"type": "string", "description": "数据周期", "default": "daily"},
            "initialCapital": {"type": "number", "description": "初始资金", "default": 100000}
        },
        "required": ["symbol", "startDate", "endDate", "strategyCode"]
    },
    
    "get_cached_prices": {
        "name": "get_cached_prices",
        "description": "获取缓存的实时价格",
        "parameters": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "股票代码列表"
            }
        },
        "required": ["codes"]
    },
    
    "sync_overlays": {
        "name": "sync_overlays",
        "description": "同步画线状态（前端上报）",
        "parameters": {
            "symbol": {
                "type": "string",
                "description": "标的代码"
            },
            "overlays": {
                "type": "array",
                "description": "画线数据列表"
            }
        },
        "required": ["symbol", "overlays"]
    },

    # ---- 知识库：Case / Relation（只复述用户原文，不自动判定共振）----
    "search_cases": {
        "name": "search_cases",
        "description": (
            "检索图表标注 Case（含 level_origin）。"
            "返回用户写入的 notes/源K/水平位/反应点原文，不做共振或反向自动判定。"
        ),
        "parameters": {
            "q": {"type": "string", "description": "关键词（代码/名称/备注）"},
            "symbol": {"type": "string", "description": "按标的过滤"},
            "period": {"type": "string", "description": "按周期过滤"},
            "type": {"type": "string", "description": "case 类型，如 level_origin"},
            "limit": {"type": "integer", "description": "条数上限", "default": 20}
        },
        "required": []
    },
    "get_case": {
        "name": "get_case",
        "description": "按 id 获取单个 Case 全文（含 vault 路径与 overlays）",
        "parameters": {
            "case_id": {"type": "string", "description": "Case ID"}
        },
        "required": ["case_id"]
    },
    "search_relations": {
        "name": "search_relations",
        "description": (
            "检索用户声明的跨标的/跨周期关联 Relation。"
            "只返回 relation_note 等用户原文；系统从不自动生成关联结论。"
        ),
        "parameters": {
            "q": {"type": "string", "description": "关键词，匹配 relation_note"},
            "limit": {"type": "integer", "description": "条数上限", "default": 20}
        },
        "required": []
    },
    "get_relation": {
        "name": "get_relation",
        "description": "按 id 获取 Relation 全文（成员 + relation_note 原文）",
        "parameters": {
            "relation_id": {"type": "string", "description": "Relation ID"}
        },
        "required": ["relation_id"]
    },
    "list_due_reminders": {
        "name": "list_due_reminders",
        "description": "列出到期/过期仍 pending 的提醒（来自 Case 或 Relation）",
        "parameters": {},
        "required": []
    }
}

# 工具名称列表
TOOL_NAMES = list(TOOLS.keys())


# ============================================================
# 参数校验函数
# ============================================================

def get_tool_schema(tool_name: str) -> dict:
    """获取指定工具的schema"""
    # 优先使用Pydantic schema
    if PYDANTIC_AVAILABLE:
        schema = pydantic_get_schema(tool_name)
        if schema:
            return schema
    
    # 回退到旧格式
    return TOOLS.get(tool_name, {})


def validate_tool_params(tool_name: str, params: dict) -> Tuple[bool, str]:
    """
    验证工具参数
    
    优先使用Pydantic校验，如果失败则回退到手动校验
    
    Returns:
        (is_valid, error_message)
    """
    # 尝试Pydantic校验
    if PYDANTIC_AVAILABLE:
        try:
            success, validated, error = pydantic_validate(tool_name, params)
            if success:
                return True, ""
            # Pydantic校验失败，记录日志但继续尝试手动校验
            logger.debug(f"[MCP] Pydantic校验失败，尝试手动校验: {error}")
        except Exception as e:
            logger.debug(f"[MCP] Pydantic校验异常: {e}")
    
    # 手动校验（fallback）
    return _manual_validate(tool_name, params)


def validate_and_convert(tool_name: str, params: dict) -> Tuple[bool, Any, str]:
    """
    校验并转换参数
    
    Returns:
        (success, validated_params, error_message)
        validated_params: Pydantic模型实例或原始dict
    """
    if PYDANTIC_AVAILABLE:
        try:
            success, validated, error = pydantic_validate(tool_name, params)
            if success:
                return True, validated, ""
            return False, None, error
        except Exception as e:
            logger.error(f"[MCP] 校验异常: {e}")
    
    # 回退到手动校验
    is_valid, error = _manual_validate(tool_name, params)
    if is_valid:
        return True, params, ""
    return False, None, error


def _manual_validate(tool_name: str, params: dict) -> Tuple[bool, str]:
    """手动参数校验（fallback）"""
    tool = TOOLS.get(tool_name)
    if not tool:
        return False, f"未知工具: {tool_name}"
    
    required = tool.get("required", [])
    for param in required:
        # 支持驼峰和下划线两种格式
        if param not in params and param.replace("_", "") not in [p.replace("_", "") for p in params.keys()]:
            return False, f"缺少必需参数: {param}"
    
    return True, ""


# ============================================================
# 工具元数据获取
# ============================================================

def get_all_tools() -> Dict[str, dict]:
    """获取所有工具定义"""
    return TOOLS.copy()


def get_tool_names() -> list:
    """获取所有工具名称"""
    return TOOL_NAMES.copy()


def tool_exists(tool_name: str) -> bool:
    """检查工具是否存在"""
    return tool_name in TOOLS
