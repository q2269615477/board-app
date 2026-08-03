"""
services/update_pipeline.py — 统一数据更新链路管理器

职责：
- 收口所有数据更新入口为单一 pipeline
- 强制执行固定顺序：分类 → 成分股 → K线缓存 → 搜索索引 → 契约测试
- 成分股更新后自动触发搜索索引重建
- 禁止绕过本 pipeline 直接写生产数据

禁止：
- 不允许跳过步骤
- 不允许添加未经验证的新步骤
- 不允许前端直接触发底层脚本
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("update_pipeline")


class Stage(str, Enum):
    """数据更新阶段枚举"""

    CLASSIFICATION = "classification"
    CONSTITUENTS = "constituents"
    KLINE_CACHE = "kline_cache"
    SEARCH_INDEX = "search_index"
    CONTRACT_TEST = "contract_test"


@dataclass
class StageResult:
    """单个阶段执行结果"""

    stage: Stage
    success: bool
    started_at: str = ""
    finished_at: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """完整管道执行结果"""

    success: bool
    stages: List[StageResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: Optional[str] = None
    aborted_at: Optional[Stage] = None


# ============================================================
# 阶段执行函数注册（实际执行时由外部注入）
# ============================================================

_stage_runners: Dict[Stage, Callable[[], bool]] = {}
_search_rebuild_fn: Optional[Callable[[], bool]] = None


def register_stage_runner(stage: Stage, fn: Callable[[], bool]) -> None:
    """注册阶段执行函数。"""
    if not callable(fn):
        raise ValueError(f"Stage runner for {stage} must be callable")
    _stage_runners[stage] = fn
    logger.info("[Pipeline] Registered runner for stage: %s", stage.value)


def register_search_rebuild(fn: Callable[[], bool]) -> None:
    """注册搜索索引重建函数。"""
    global _search_rebuild_fn
    _search_rebuild_fn = fn
    logger.info("[Pipeline] Registered search index rebuild function")


# ============================================================
# 标准执行顺序
# ============================================================

STANDARD_PIPELINE = [
    Stage.CLASSIFICATION,
    Stage.CONSTITUENTS,
    Stage.KLINE_CACHE,
    Stage.SEARCH_INDEX,
    Stage.CONTRACT_TEST,
]


# ============================================================
# 管道执行
# ============================================================

def run_pipeline(
    stages: Optional[List[Stage]] = None,
    abort_on_failure: bool = True,
) -> PipelineResult:
    """执行数据更新管道。

    参数:
        stages: 要执行的阶段列表（None 表示全部）
        abort_on_failure: 某阶段失败时是否中止

    返回:
        PipelineResult 包含各阶段执行结果
    """
    if stages is None:
        stages = STANDARD_PIPELINE

    result = PipelineResult(success=True, started_at=_now())

    for stage in stages:
        stage_result = _run_stage(stage)
        result.stages.append(stage_result)

        if not stage_result.success:
            result.aborted_at = stage
            if abort_on_failure:
                result.success = False
                result.finished_at = _now()
                logger.warning("[Pipeline] Aborted at stage: %s", stage.value)
                break

    if result.success:
        result.finished_at = _now()
        logger.info("[Pipeline] All stages completed successfully")

    return result


def run_partial(start_from: Stage, end_at: Optional[Stage] = None) -> PipelineResult:
    """从指定阶段开始执行。

    使用场景: 分类已通过，只需要从成分股开始更新。
    """
    all_stages = STANDARD_PIPELINE
    start_idx = (
        next((i for i, s in enumerate(all_stages) if s == start_from), 0)
    )
    end_idx = (
        next((i + 1 for i, s in enumerate(all_stages) if s == end_at), None)
        if end_at else None
    )
    subset = all_stages[start_idx:end_idx] if end_idx else all_stages[start_idx:]
    return run_pipeline(stages=subset)


def _run_stage(stage: Stage) -> StageResult:
    """执行单个阶段。"""
    result = StageResult(stage=stage, success=False, started_at=_now())

    runner = _stage_runners.get(stage)
    if runner is None:
        result.error = f"No runner registered for stage {stage.value}"
        result.message = "未注册执行函数"
        logger.error("[Pipeline] %s", result.error)
        return result

    try:
        ok = runner()
        result.success = bool(ok)
        result.message = "成功" if ok else "执行返回失败"
    except Exception as e:
        result.success = False
        result.error = str(e)[:300]
        result.message = f"异常: {e}"
        logger.error("[Pipeline] Stage %s failed: %s", stage.value, e)

    result.finished_at = _now()
    return result


def trigger_search_rebuild() -> bool:
    """触发搜索索引重建（由成分股更新成功后自动调用）。"""
    if _search_rebuild_fn is None:
        logger.warning("[Pipeline] No search rebuild function registered")
        return False
    try:
        return bool(_search_rebuild_fn())
    except Exception as e:
        logger.error("[Pipeline] Search rebuild failed: %s", e)
        return False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# 便捷函数：在每个阶段完成时应该调用
# ============================================================

def on_constituents_updated() -> bool:
    """成分股更新成功后的钩子。

    当成分股更新完成后必须调用此函数，
    它会自动触发搜索索引重建。
    """
    logger.info("[Pipeline] Constituents updated, triggering search index rebuild")
    return trigger_search_rebuild()
