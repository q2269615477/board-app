from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nav_rerender_reapplies_cached_board_changes_after_inner_html_replace():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    replace_at = source.index("p.innerHTML = h;")
    apply_at = source.index("applyBoardChgToDOM();", replace_at)
    assert apply_at > replace_at


def test_nav_filter_controls_keep_the_board_change_contract():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    for filter_type in ("industry", "concept", "index"):
        assert f"setNavFilter(\\'{filter_type}\\')" in source
    assert "data-bchg=" in source
    assert "_boardChgData[key]" in source


def test_index_filter_has_a_lazy_index_quote_fallback():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "loadIndexBoardChanges();" in source
    assert "/api/spot/indices?tickers=" in source
    assert "_boardChgData['index:' + code] = value;" in source


def test_all_filter_rerender_also_fills_missing_index_quotes_without_repeat_requests():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "loadIndexBoardChanges();" in source
    assert "span.textContent.trim() === '--'" in source
    assert "_indexChgRequested = new Set();" in source
    assert "!_indexChgRequested.has(code)" in source
    assert "_indexChgRequested.add(code)" in source
    assert "if (_navFilterType === 'index') loadIndexBoardChanges();" not in source


def test_unavailable_index_is_rendered_as_stopped_and_not_selectable():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "raw.unavailable === true" in source
    assert "span.textContent = '停更';" in source
    assert "该指数已停更，暂无可用行情" in source
    assert "if (type === 'index' && _isIndexBoardUnavailable(code))" in source
    assert "return;" in source[source.index("if (type === 'index' && _isIndexBoardUnavailable(code))"):]


def test_failed_index_quote_request_clears_requested_codes_for_retry():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "_indexChgRequested.delete(code)" in source
    assert "throw new Error('empty index quote response')" in source


def test_board_snapshot_merges_without_erasing_index_quotes():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "Object.assign({}, _boardChgData || {})" in source
    assert "merged['index:' + code] !== undefined" in source
    assert "_boardChgData = merged;" in source
