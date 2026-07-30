"""Tests for services/update_pipeline.py.

Verifies the pipeline enforces the correct stage ordering,
handles failures correctly, and triggers search rebuild after
constituent update.
"""
import pytest
from unittest.mock import MagicMock, patch

from services.update_pipeline import (
    PipelineResult,
    Stage,
    StageResult,
    STANDARD_PIPELINE,
    on_constituents_updated,
    register_search_rebuild,
    register_stage_runner,
    run_partial,
    run_pipeline,
    trigger_search_rebuild,
)


@pytest.fixture(autouse=True)
def _reset_pipeline():
    """Reset global pipeline state before each test."""
    from services import update_pipeline
    update_pipeline._stage_runners.clear()
    update_pipeline._search_rebuild_fn = None
    yield
    update_pipeline._stage_runners.clear()
    update_pipeline._search_rebuild_fn = None


def test_standard_pipeline_order():
    """Pipeline must enforce fixed order."""
    assert STANDARD_PIPELINE == [
        Stage.CLASSIFICATION,
        Stage.CONSTITUENTS,
        Stage.KLINE_CACHE,
        Stage.SEARCH_INDEX,
        Stage.CONTRACT_TEST,
    ]


def test_run_pipeline_all_success():
    """All stages succeed → pipeline succeeds."""
    for stage in STANDARD_PIPELINE:
        register_stage_runner(stage, lambda: True)

    result = run_pipeline()
    assert result.success is True
    assert len(result.stages) == 5
    assert all(s.success for s in result.stages)


def test_run_pipeline_failure_aborts():
    """A stage failure with abort_on_failure=True aborts the pipeline."""
    register_stage_runner(Stage.CLASSIFICATION, lambda: True)
    register_stage_runner(Stage.CONSTITUENTS, lambda: True)
    register_stage_runner(Stage.KLINE_CACHE, lambda: False)  # 失败
    register_stage_runner(Stage.SEARCH_INDEX, lambda: True)
    register_stage_runner(Stage.CONTRACT_TEST, lambda: True)

    result = run_pipeline(abort_on_failure=True)
    assert result.success is False
    assert len(result.stages) == 3  # 前两个成功 + 第三个失败
    assert result.aborted_at == Stage.KLINE_CACHE


def test_run_pipeline_failure_continue():
    """A stage failure with abort_on_failure=False continues."""
    register_stage_runner(Stage.CLASSIFICATION, lambda: True)
    register_stage_runner(Stage.CONSTITUENTS, lambda: False)  # 失败
    register_stage_runner(Stage.KLINE_CACHE, lambda: True)
    register_stage_runner(Stage.SEARCH_INDEX, lambda: True)
    register_stage_runner(Stage.CONTRACT_TEST, lambda: True)

    result = run_pipeline(abort_on_failure=False)
    assert result.success is True  # 整体仍返回 True（无论失败）
    assert len(result.stages) == 5
    stages = {s.stage: s for s in result.stages}
    assert stages[Stage.CONSTITUENTS].success is False


def test_run_pipeline_stage_exception():
    """A stage that raises exception is handled gracefully."""
    def bad_runner():
        raise RuntimeError("boom")

    register_stage_runner(Stage.CLASSIFICATION, lambda: True)
    register_stage_runner(Stage.CONSTITUENTS, bad_runner)
    register_stage_runner(Stage.KLINE_CACHE, lambda: True)
    register_stage_runner(Stage.SEARCH_INDEX, lambda: True)
    register_stage_runner(Stage.CONTRACT_TEST, lambda: True)

    result = run_pipeline(abort_on_failure=True)
    assert result.success is False
    stages = {s.stage: s for s in result.stages}
    assert stages[Stage.CONSTITUENTS].success is False
    assert "boom" in (stages[Stage.CONSTITUENTS].error or "")


def test_run_partial():
    """run_partial starts from a specific stage."""
    for stage in STANDARD_PIPELINE:
        register_stage_runner(stage, lambda: True)

    result = run_partial(start_from=Stage.KLINE_CACHE)
    assert result.success is True
    assert len(result.stages) == 3  # kline_cache, search_index, contract_test
    stages = [s.stage for s in result.stages]
    assert stages == [Stage.KLINE_CACHE, Stage.SEARCH_INDEX, Stage.CONTRACT_TEST]


def test_run_partial_with_end():
    """run_partial respects end_at."""
    for stage in STANDARD_PIPELINE:
        register_stage_runner(stage, lambda: True)

    result = run_partial(start_from=Stage.CLASSIFICATION, end_at=Stage.CONSTITUENTS)
    assert result.success is True
    assert len(result.stages) == 2
    stages = [s.stage for s in result.stages]
    assert stages == [Stage.CLASSIFICATION, Stage.CONSTITUENTS]


def test_search_rebuild_trigger():
    """Search rebuild is triggered when registered."""
    mock_fn = MagicMock(return_value=True)
    register_search_rebuild(mock_fn)

    result = trigger_search_rebuild()
    assert result is True
    mock_fn.assert_called_once()


def test_search_rebuild_not_registered():
    """If no rebuild fn registered, returns False."""
    result = trigger_search_rebuild()
    assert result is False


def test_search_rebuild_exception_handled():
    """If rebuild fn raises, handled gracefully."""
    def bad_rebuild():
        raise RuntimeError("index rebuild failed")

    register_search_rebuild(bad_rebuild)
    result = trigger_search_rebuild()
    assert result is False


def test_on_constituents_updated_triggers_rebuild():
    """on_constituents_updated() calls search rebuild."""
    mock_fn = MagicMock(return_value=True)
    register_search_rebuild(mock_fn)

    result = on_constituents_updated()
    assert result is True
    mock_fn.assert_called_once()


def test_register_invalid_runner():
    """Registering non-callable raises ValueError."""
    with pytest.raises(ValueError):
        register_stage_runner(Stage.CLASSIFICATION, "not a callable")


def test_pipeline_timestamps():
    """Pipeline result includes start/finish timestamps."""
    for stage in STANDARD_PIPELINE:
        register_stage_runner(stage, lambda: True)

    result = run_pipeline()
    assert result.started_at
    assert result.finished_at
    for s in result.stages:
        assert s.started_at
        assert s.finished_at


def test_missing_runner_returns_error():
    """Stage with no registered runner fails with clear error."""
    # Only register 2 of 5 stages
    register_stage_runner(Stage.CLASSIFICATION, lambda: True)
    register_stage_runner(Stage.CONSTITUENTS, lambda: True)

    result = run_pipeline(abort_on_failure=True)
    assert result.success is False
    stages = {s.stage: s for s in result.stages}
    assert "未注册" in stages[Stage.KLINE_CACHE].message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
