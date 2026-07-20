"""
MCP工具参数校验模型
使用Pydantic v2进行参数校验和类型转换
"""

from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime


# ============================================================
# 基础参数模型
# ============================================================

class SetSymbolParams(BaseModel):
    """切换标的参数"""
    symbol: str = Field(
        ...,
        min_length=3,
        max_length=20,
        description="标的代码，如 '600519' 或 'sh000001'"
    )
    symbol_type: Literal["stock", "index", "board"] = Field(
        default="stock",
        description="标的类型"
    )
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """校验标的代码格式"""
        v = v.strip()
        if not v:
            raise ValueError('标的代码不能为空')
        # 允许字母、数字、点、下划线
        if not all(c.isalnum() or c in '._' for c in v):
            raise ValueError('标的代码包含非法字符')
        return v


class SetPeriodParams(BaseModel):
    """切换周期参数"""
    period: Literal["1m", "5m", "15m", "30m", "60m", "daily", "weekly", "monthly"] = Field(
        ...,
        description="K线周期"
    )


class Point(BaseModel):
    """画线点位"""
    timestamp: int = Field(..., description="时间戳（毫秒）")
    value: float = Field(..., description="价格")


class CreateOverlayParams(BaseModel):
    """创建画线参数"""
    type: Literal[
        "horizontalLine", "verticalLine", "trendLine",
        "rayLine", "segmentLine", "rect", "fibonacci", "text", "icon"
    ] = Field(..., description="画线类型")
    
    points: List[Point] = Field(
        ...,
        min_length=1,
        description="点位数组"
    )
    
    styles: Optional[Dict[str, Any]] = Field(
        default=None,
        description="样式配置（颜色、线宽等）"
    )
    
    extend_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="扩展数据（语义标记等）"
    )
    
    @field_validator('points')
    @classmethod
    def validate_points(cls, v: List[Point], info) -> List[Point]:
        """根据画线类型校验点位数量"""
        overlay_type = info.data.get('type')
        
        if overlay_type in ['trendLine', 'segmentLine', 'rayLine']:
            if len(v) != 2:
                raise ValueError(f'{overlay_type} 需要2个点')
        elif overlay_type in ['horizontalLine', 'verticalLine', 'text', 'icon']:
            if len(v) != 1:
                raise ValueError(f'{overlay_type} 需要1个点')
        elif overlay_type in ['rect', 'fibonacci']:
            if len(v) < 2:
                raise ValueError(f'{overlay_type} 至少需要2个点')
        
        return v


class RemoveOverlayParams(BaseModel):
    """删除画线参数"""
    overlay_id: str = Field(
        ...,
        min_length=1,
        description="画线ID"
    )
    symbol: Optional[str] = Field(
        default=None,
        description="标的代码（可选，用于精确定位）"
    )


class GetOverlaysParams(BaseModel):
    """获取画线参数"""
    symbol: Optional[str] = Field(
        default=None,
        description="标的代码（可选，默认使用当前标的）"
    )


class ScrollToTimestampParams(BaseModel):
    """滚动到指定时间参数"""
    timestamp: int = Field(
        ...,
        gt=0,
        description="目标时间戳（毫秒）"
    )


class GetKlineDataParams(BaseModel):
    """获取K线数据参数"""
    symbol: str = Field(..., min_length=3, description="标的代码")
    period: str = Field(default="daily", description="周期")
    start_date: Optional[str] = Field(default=None, description="开始日期（YYYY-MM-DD）")
    end_date: Optional[str] = Field(default=None, description="结束日期（YYYY-MM-DD）")
    count: Optional[int] = Field(default=None, ge=1, le=5000, description="获取条数")
    
    @field_validator('start_date', 'end_date')
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        """校验日期格式"""
        if v is None:
            return v
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            raise ValueError('日期格式应为 YYYY-MM-DD')


class RunBacktestParams(BaseModel):
    """运行回测参数"""
    symbol: str = Field(..., min_length=3, description="标的代码")
    start_date: str = Field(..., description="开始日期（YYYY-MM-DD）")
    end_date: str = Field(..., description="结束日期（YYYY-MM-DD）")
    strategy_code: str = Field(..., min_length=1, description="策略代码")
    params: Optional[Dict[str, Any]] = Field(default=None, description="策略参数")
    period: str = Field(default="daily", description="数据周期")
    initial_capital: float = Field(default=100000.0, gt=0, description="初始资金")
    
    @field_validator('end_date')
    @classmethod
    def validate_date_range(cls, v: str, info) -> str:
        """校验日期范围"""
        start = info.data.get('start_date')
        if start and v:
            start_dt = datetime.strptime(start, '%Y-%m-%d')
            end_dt = datetime.strptime(v, '%Y-%m-%d')
            if end_dt < start_dt:
                raise ValueError('结束日期不能早于开始日期')
        return v


class GetCachedPricesParams(BaseModel):
    """获取缓存价格参数"""
    codes: List[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="股票代码列表"
    )
    
    @field_validator('codes')
    @classmethod
    def validate_codes(cls, v: List[str]) -> List[str]:
        """校验代码列表"""
        if not v:
            raise ValueError('代码列表不能为空')
        for code in v:
            if len(code) < 3:
                raise ValueError(f'代码 {code} 太短')
        return v


class SyncOverlaysParams(BaseModel):
    """同步画线参数"""
    symbol: str = Field(..., min_length=3, description="标的代码")
    overlays: List[Dict[str, Any]] = Field(..., description="画线数据列表")


class SearchCasesParams(BaseModel):
    """检索 Case"""
    q: Optional[str] = Field(default=None, description="关键词")
    symbol: Optional[str] = Field(default=None, description="标的")
    period: Optional[str] = Field(default=None, description="周期")
    type: Optional[str] = Field(default=None, description="case 类型")
    limit: int = Field(default=20, ge=1, le=200, description="条数")


class GetCaseParams(BaseModel):
    case_id: str = Field(..., min_length=1, description="Case ID")


class SearchRelationsParams(BaseModel):
    q: Optional[str] = Field(default=None, description="关键词")
    limit: int = Field(default=20, ge=1, le=200, description="条数")


class GetRelationParams(BaseModel):
    relation_id: str = Field(..., min_length=1, description="Relation ID")


class ListDueRemindersParams(BaseModel):
    """无参占位"""
    pass


# ============================================================
# 工具参数映射
# ============================================================

TOOL_SCHEMAS = {
    "set_symbol": SetSymbolParams,
    "set_period": SetPeriodParams,
    "create_overlay": CreateOverlayParams,
    "remove_overlay": RemoveOverlayParams,
    "get_overlays": GetOverlaysParams,
    "scroll_to_timestamp": ScrollToTimestampParams,
    "get_kline_data": GetKlineDataParams,
    "run_backtest": RunBacktestParams,
    "get_cached_prices": GetCachedPricesParams,
    "sync_overlays": SyncOverlaysParams,
    "search_cases": SearchCasesParams,
    "get_case": GetCaseParams,
    "search_relations": SearchRelationsParams,
    "get_relation": GetRelationParams,
    "list_due_reminders": ListDueRemindersParams,
}


def validate_tool_params(tool_name: str, params: dict) -> tuple[bool, Any, str]:
    """
    校验工具参数
    
    Returns:
        (success, validated_params, error_message)
    """
    schema_class = TOOL_SCHEMAS.get(tool_name)
    if not schema_class:
        return False, None, f"未知工具: {tool_name}"
    
    try:
        validated = schema_class(**params)
        return True, validated, ""
    except ValidationError as e:
        # 格式化错误信息
        errors = []
        for error in e.errors():
            field = '.'.join(str(x) for x in error['loc'])
            msg = error['msg']
            errors.append(f"{field}: {msg}")
        return False, None, f"参数校验失败: {'; '.join(errors)}"
    except Exception as e:
        return False, None, f"校验异常: {str(e)}"


def get_tool_schema(tool_name: str) -> Optional[dict]:
    """获取工具的JSON Schema"""
    schema_class = TOOL_SCHEMAS.get(tool_name)
    if not schema_class:
        return None
    return schema_class.model_json_schema()


# ============================================================
# 响应模型
# ============================================================

class MCPResponse(BaseModel):
    """MCP标准响应"""
    success: bool = Field(..., description="是否成功")
    data: Optional[Dict[str, Any]] = Field(default=None, description="响应数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    message: Optional[str] = Field(default=None, description="提示信息")


class OverlayData(BaseModel):
    """画线数据模型"""
    id: str = Field(..., description="画线ID")
    type: str = Field(..., description="画线类型")
    points: List[Point] = Field(..., description="点位数组")
    styles: Optional[Dict[str, Any]] = Field(default=None, description="样式")
    extend_data: Optional[Dict[str, Any]] = Field(default=None, description="扩展数据")
    visible: bool = Field(default=True, description="是否可见")
    locked: bool = Field(default=False, description="是否锁定")
    created_at: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000), description="创建时间")
    updated_at: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000), description="更新时间")
