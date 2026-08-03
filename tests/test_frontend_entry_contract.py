"""Frontend entry contract tests.

These tests protect the modular panel layout.  The page shell must load the
real modules instead of reintroducing a large inline frontend in index.html.

Entry scripts are auto-parsed from ``static/index.html`` — no hardcoded list.
"""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_baseline.ps1"


REQUIRED_IDS = (
    'id="toolbar"',
    'id="index-bar"',
    'id="idx-add-btn"',
    'id="nav-panel"',
    'id="pro-container"',
    'id="search-wrap"',
)

# Regex to extract <script src="/static/js/..."> from index.html
_SCRIPT_SRC_RE = re.compile(r'<script\s+[^>]*src="(/static/js/[^"]+)"')


def _index_html():
    return INDEX.read_text(encoding="utf-8")


def _parse_entry_scripts():
    """Parse all local <script src="/static/js/..."> from index.html.

    Returns a list of relative file paths (e.g. ``static/js/app-init.js``)
    with query strings stripped and duplicates removed.
    """
    html = _index_html()
    seen = set()
    result = []
    for m in _SCRIPT_SRC_RE.finditer(html):
        src = m.group(1)
        # Strip query string (e.g. ?v=20260729)
        clean = src.split("?")[0]
        # Convert /static/js/foo.js → static/js/foo.js
        rel = clean.lstrip("/")
        if rel not in seen:
            seen.add(rel)
            result.append(rel)
    return result


def test_index_shell_keeps_required_layout_mount_points():
    html = _index_html()
    for marker in REQUIRED_IDS:
        assert marker in html, f"missing frontend mount point: {marker}"


def test_panels_start_collapsed_without_hiding_manual_expand_controls():
    html = _index_html()
    nav = (ROOT / "static" / "js" / "nav-panel.js").read_text(encoding="utf-8")
    session = (ROOT / "static" / "js" / "session-ui.js").read_text(encoding="utf-8")
    assert '<div id="app" class="nav-collapsed">' in html
    assert 'id="nav-expand-btn"' in html
    assert "const isCatExpanded = hasCatState ? store._expandedCats[cat.name] === true : navHasFilter;" in nav
    assert "const isSubExpanded = hasSubState ? store._expandedCats[subId] === true : navHasFilter;" in nav
    assert "side.classList.add('collapsed');" in session


def test_index_shell_loads_modular_frontend_scripts():
    """All auto-parsed entry scripts must be referenced in index.html."""
    scripts = _parse_entry_scripts()
    assert len(scripts) >= 10, (
        f"Expected at least 10 entry scripts, found {len(scripts)}: {scripts}"
    )
    # Sanity: the core modules must always be present
    must_have = (
        "static/js/api-client.js",
        "static/js/app-init.js",
        "static/js/chart-core.js",
    )
    for required in must_have:
        assert required in scripts, f"missing core entry script: {required}"


def test_session_modules_load_in_dependency_order_and_ui_guards_named_boundaries():
    """session API/state/render modules precede UI and guard named boundaries."""
    scripts = _parse_entry_scripts()
    api = "static/js/session-api.js"
    state = "static/js/session-state.js"
    render = "static/js/session-render.js"
    ui = "static/js/session-ui.js"
    assert api in scripts, f"missing session API entry script: {api}"
    assert state in scripts, f"missing session state entry script: {state}"
    assert render in scripts, f"missing session render entry script: {render}"
    assert ui in scripts, f"missing session UI entry script: {ui}"
    assert scripts.index(api) < scripts.index(state) < scripts.index(render) < scripts.index(ui), (
        "session-api.js, session-state.js and session-render.js must be loaded before session-ui.js"
    )
    source = (ROOT / ui).read_text(encoding="utf-8")
    assert "function requireSessionAPI()" in source
    assert "SESSION_API_METHODS" in source
    assert "function requireSessionState()" in source
    assert "SESSION_STATE_METHODS" in source
    assert "function requireSessionRender()" in source
    assert "SESSION_RENDER_METHODS" in source
    assert "renderSessionBody" in source
    assert "global.SessionAPI.api" not in source


def test_session_render_is_a_standalone_pure_projection_module():
    """SessionRender must not own browser state, I/O, timers, or event binding."""
    render = ROOT / "static/js/session-render.js"
    source = render.read_text(encoding="utf-8")
    assert "module.exports" in source
    assert "renderSessionBody" in source
    for forbidden in (
        "document",
        "window",
        "fetch(",
        "setTimeout",
        "setInterval",
        "addEventListener",
        "innerHTML",
        "SessionUI",
    ):
        assert forbidden not in source, f"session-render.js must stay pure: {forbidden}"


def test_session_state_is_a_standalone_pure_projection_module():
    """SessionState must stay independent from DOM/chart/network/session ownership."""
    scripts = _parse_entry_scripts()
    state = "static/js/session-state.js"
    ui = "static/js/session-ui.js"
    assert state in scripts
    source = (ROOT / state).read_text(encoding="utf-8")
    assert "module.exports" in source
    for export_name in (
        "projectPanelContext",
        "normalizeOverlayInstance",
        "snapPriceElement",
        "projectBarToKbar",
    ):
        assert f"{export_name}:" in source
    for forbidden in ("document", "fetch(", "setTimeout", "setInterval", "__kline_chart", "var S", "let S"):
        assert forbidden not in source, f"session-state.js must not own runtime state: {forbidden}"
    ui_source = (ROOT / ui).read_text(encoding="utf-8")
    assert "requireSessionState().projectPanelContext" in ui_source
    assert "requireSessionState().normalizeOverlayInstance" in ui_source
    assert "requireSessionState().snapPriceElement" in ui_source
    assert "requireSessionState().projectBarToKbar" in ui_source


def test_bar_replay_entry_exists_and_is_between_chart_core_and_app_init():
    """Bar replay must be a real module loaded after chart-core and before init."""
    html = _index_html()
    scripts = _parse_entry_scripts()
    replay = "static/js/bar-replay.js"
    assert replay in scripts, f"missing bar replay entry script: {replay}"
    assert (ROOT / replay).is_file(), f"bar replay script missing on disk: {replay}"
    assert scripts.index("static/js/chart-core.js") < scripts.index(replay) < scripts.index(
        "static/js/app-init.js"
    )
    source = (ROOT / replay).read_text(encoding="utf-8")
    assert re.search(r"(?:window|global)\.BarReplayController", source)
    for control_id in (
        "bar-replay-btn",
        "bar-replay-controls",
        "bar-replay-play",
        "bar-replay-step",
        "bar-replay-speed",
        "bar-replay-status",
        "bar-replay-exit",
    ):
        assert control_id in source, f"bar replay module missing DOM contract: {control_id}"
    assert "<script>" not in html


def test_replay_trading_modules_are_loaded_in_contract_order():
    scripts = _parse_entry_scripts()
    ordered = (
        "static/js/chart-core.js",
        "static/js/bar-replay-events.js",
        "static/js/bar-replay.js",
        "static/js/replay-trade-engine.js",
        "static/js/replay-trade-state-model.js",
        "static/js/replay-trade-geometry.js",
        "static/js/replay-trade-overlay-renderer.js",
        "static/js/replay-trade-interaction-controller.js",
        "static/js/chart-comparison-model.js",
        "static/js/chart-comparison.js",
        "static/js/replay-trade-ui.js",
        "static/js/app-init.js",
    )
    for entry in ordered:
        assert entry in scripts, f"missing replay trading entry script: {entry}"
    assert [scripts.index(entry) for entry in ordered] == sorted(
        scripts.index(entry) for entry in ordered
    )

    engine = (ROOT / "static/js/replay-trade-engine.js").read_text(encoding="utf-8")
    ui = (ROOT / "static/js/replay-trade-ui.js").read_text(encoding="utf-8")
    for method in ("openManual", "closeManual", "setPendingOrder", "cancelPending"):
        assert method in engine
        assert method in ui
    state_model = (ROOT / "static/js/replay-trade-state-model.js").read_text(encoding="utf-8")
    assert "value === null || value === undefined || value === ''" in state_model
    assert "ReplayTradeStateModel must load before ReplayTradeUI" in ui
    assert "ReplayTradeGeometry must load before ReplayTradeUI" in ui
    assert "ReplayTradeOverlayRenderer must load before ReplayTradeUI" in ui
    assert "ReplayTradeInteractionController must load before ReplayTradeUI" in ui
    assert "events.emit(name, detail); emittedByBus = true" in ui


def test_native_period_and_symbol_changes_exit_replay_before_loading_new_context():
    chart_core = (ROOT / "static" / "js" / "chart-core.js").read_text(encoding="utf-8")
    assert "function _exitBarReplayForContextChange(reason)" in chart_core
    assert "replay.exit({ restore: false, silent: true" in chart_core
    assert "_exitBarReplayForContextChange('symbol-change');" in chart_core
    assert "_exitBarReplayForContextChange('period-change');" in chart_core
    assert "new CustomEvent('period-change'" in chart_core
    assert "source: 'pro-setPeriod'" in chart_core


def test_vertical_pan_entry_uses_native_price_axis_and_resets_on_context_change():
    scripts = _parse_entry_scripts()
    entry = "static/js/chart-vertical-pan.js"
    assert entry in scripts
    assert scripts.index("static/js/chart-core.js") < scripts.index(entry) < scripts.index(
        "static/js/app-init.js"
    )
    source = (ROOT / entry).read_text(encoding="utf-8")
    controller = (ROOT / "static/js/chart-controller.js").read_text(encoding="utf-8")
    assert "ChartVerticalPanController" in source
    assert "getDrawPaneById('candle_pane')" in source
    assert "axis.setAutoCalcTickFlag(false)" in source
    assert "axis.setAutoCalcTickFlag(true)" in source
    assert "axisOptions: { scrollZoomEnabled: true }" in source
    assert "target.closest('.klinecharts-pro-period-bar .item.period')" in source
    assert "_resetVerticalPanForContextChange();" in controller
    chart_core = (ROOT / "static/js/chart-core.js").read_text(encoding="utf-8")
    assert chart_core.count("window.ChartVerticalPanController.reset({ silent: true });") >= 2


def test_chart_comparison_entry_rebases_to_visible_left_edge_and_uses_percent_returns():
    scripts = _parse_entry_scripts()
    model_entry = "static/js/chart-comparison-model.js"
    entry = "static/js/chart-comparison.js"
    assert model_entry in scripts
    assert entry in scripts
    assert scripts.index("static/js/chart-core.js") < scripts.index(model_entry) < scripts.index(entry) < scripts.index(
        "static/js/app-init.js"
    )
    source = (ROOT / entry).read_text(encoding="utf-8")
    assert "ChartComparisonModel must load before ChartComparisonController" in source
    assert "ChartComparisonController" in source
    assert "getVisibleRange" in source
    assert "OnVisibleRangeChange" in source or "onVisibleRangeChange" in source
    assert "/api/search?q=" in source
    assert "/api/kline/" in source
    assert "percentage" in source.lower() or "returnPct" in source
    assert "comparison-endpoint" in source
    assert "this.overlays" in source
    assert "addOverlay" in source
    assert "data-series', 'overlay'" in source
    assert "data-series', 'main'" not in source


def test_chart_comparison_range_selection_uses_close_to_close_contract():
    source = (ROOT / "static/js/chart-comparison.js").read_text(encoding="utf-8")
    for contract in (
        "computeRangeComparison",
        "startRangeSelection",
        "clearRangeSelection",
        "getRangeSelection",
        "setRangeSelectionIndices",
        "chart-comparison-range-select",
        "board-comparison-range-tool",
        "data-comparison-range",
        "computeMainRange",
    ):
        assert contract in source, f"missing comparison range contract: {contract}"
    assert "differencePct" in source
    assert "mainReturnPct" in source
    assert "overlayReturnPct" in source
    assert "mainEnd.close / mainStart.close" in source
    assert "overlayEnd.close / overlayStart.close" in source


def test_all_symbol_search_inputs_share_persistent_five_item_history():
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    store = (ROOT / "static/js/search-history-store.js").read_text(encoding="utf-8")
    bottom = (ROOT / "static/js/search-panel.js").read_text(encoding="utf-8")
    nav = (ROOT / "static/js/nav-panel.js").read_text(encoding="utf-8")
    comparison = (ROOT / "static/js/chart-comparison.js").read_text(encoding="utf-8")

    assert "search-history-store.js" in index
    assert "DEFAULT_MAX_ITEMS = 5" in store
    assert "board_app_search_history" in store
    assert "ensureBoardSearchHistory" in bottom
    assert "#nav-search-input" in nav
    assert "recordBoardSearchHistory" in nav
    assert "chart-comparison-input" in comparison
    assert "recordBoardSearchHistory" in comparison
    assert "/api/search/history" in bottom


def test_all_entry_scripts_exist_on_disk():
    """Every entry script referenced in index.html must exist on disk."""
    scripts = _parse_entry_scripts()
    missing = [s for s in scripts if not (ROOT / s).exists()]
    assert not missing, f"Entry scripts missing on disk: {missing}"


def test_all_entry_scripts_are_git_tracked():
    """Every entry script referenced in index.html must be tracked by Git."""
    scripts = _parse_entry_scripts()
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
    )
    tracked = set(
        p.decode("utf-8", errors="replace").strip().replace("\\", "/")
        for p in result.stdout.split(b"\0")
        if p.strip()
    )
    untracked = [s for s in scripts if s not in tracked]
    assert not untracked, (
        f"Entry scripts not tracked by Git: {untracked}"
    )


def test_verify_baseline_ps1_does_not_hardcode_file_list():
    """verify_baseline.ps1 must auto-parse index.html, not hardcode a file list."""
    content = VERIFY_SCRIPT.read_text(encoding="utf-8")
    # Must reference index.html for parsing
    assert "index.html" in content, (
        "verify_baseline.ps1 must read static/index.html to discover scripts"
    )
    # Must NOT contain a hardcoded array of JS file paths
    # (the old pattern was: $jsFiles = @("static/js/api-client.js", ...))
    hardcoded_pattern = re.compile(
        r'\$jsFiles\s*=\s*@\(\s*"static/js/'
    )
    assert not hardcoded_pattern.search(content), (
        "verify_baseline.ps1 must not hardcode a JS file list; "
        "it should auto-parse from index.html"
    )


def test_index_shell_does_not_reintroduce_large_inline_frontend():
    html = _index_html()
    assert "<script>" not in html
    assert "<script " in html
    assert html.count("<style") == 0
    assert len(html.splitlines()) < 140


def test_session_panel_keeps_p1_interaction_entries():
    js = (ROOT / "static" / "js" / "session-ui.js").read_text(encoding="utf-8")
    for action in (
        'data-act="level"',
        'data-act="reaction"',
        'data-act="levels"',
        'data-act="resonance"',
        'data-act="proposal"',
    ):
        assert action in js, f"missing session panel action: {action}"
    for fn in (
        "startDrawLevel",
        "startReactionMode",
        "openLevelManager",
        "openResonancePanel",
        "openProposalPanel",
        "bindHotkeys",
    ):
        assert fn in js, f"missing session panel function: {fn}"


def test_left_nav_does_not_render_board_tag_chips():
    js = (ROOT / "static" / "js" / "nav-panel.js").read_text(encoding="utf-8")
    assert 'class="board-tag-chip"' not in js
    assert "function renderBoardTagChips" in js
    assert "function _typeTag" in js


def test_chart_polling_treats_call_auction_as_live():
    source = (ROOT / "static" / "js" / "chart-core.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "else if (hhmm < 915) phase = 'preopen';" in source
    assert "else if (hhmm < 930) phase = 'preopen';" not in source


def test_index_bar_quote_refresh_is_singleflight_and_observable():
    source = (ROOT / "static" / "js" / "index-bar.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "var _idxRefreshPromise = null;" in source
    assert "if (_idxRefreshPromise) return _idxRefreshPromise;" in source
    assert "dedupeKey: force ? 'top-index-quotes-force' : 'top-index-quotes'" in source
    assert "if (force) params.set('force', '1');" in source
    assert "setTimeout(refreshIdxPrices, 50);" in source
    assert "timeout: 7000" in source
    assert "bar.dataset.quoteState = 'ready';" in source
    assert "window.addEventListener('rt-indices'" in source
    assert "window.addEventListener('rt-status'" in source
    assert "RealtimeBus.isConnected()" in source
    assert "var _idxVisibilityBound = false;" in source
    assert "var _idxRealtimeBound = false;" in source
    assert "if (_idxRealtimeBound) return;" in source
    assert "_startIdxFallback(false);" in source


def test_session_polls_are_visibility_and_panel_state_gated():
    source = (ROOT / "static" / "js" / "session-ui.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "function sessionPanelIsActive()" in source
    assert "if (document.hidden) return false;" in source
    assert "!panel.classList.contains('collapsed')" in source
    assert "if (!sessionPanelIsActive()) return;" in source
    assert "if (!S || !sessionPanelIsActive()) return;" in source
    assert "if (S && document.getElementById('sess-side')) render();" not in source
    assert "}, 2500);" not in source


def test_chart_history_is_windowed_and_startup_requests_are_not_duplicated():
    chart = (ROOT / "static" / "js" / "chart-core.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    api_client = (ROOT / "static" / "js" / "api-client.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    nav = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    init = (ROOT / "static" / "js" / "app-init.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    index_bar = (ROOT / "static" / "js" / "index-bar.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    sse = (ROOT / "static" / "js" / "sse-client.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "&from=" in chart and "&to=" in chart and "&limit=600" in chart
    assert "fetchedRanges.push" in chart
    assert "preloadFirstBoard" not in nav
    assert "renderIndexBar();" not in init
    assert "_startIdxFallback();" not in init
    assert "Promise.all([loadPinyinData(), loadClassification()])" in init
    assert "if (document.hidden && !force) return Promise.resolve(null);" in index_bar
    assert "if (document.hidden) return;" in chart
    assert "refreshAnnCounts(false).then(_patchAnnCountsInNav);" in nav
    assert "window.boardPollingLeader" in api_client
    assert "BOARD_APP_POLL_LEADER_V1" in api_client
    assert "boardPollingLeader.isLeader()" in chart
    assert "boardPollingLeader.isLeader()" in index_bar
    assert "BOARD_APP_SSE_RELAY_V1" in sse
    assert "_isSseLeader()" in sse
    assert "_bindSseHandlers(_sseRelayTarget);" in sse
    assert "'/api/events?client_id='" in sse


def test_api_client_does_not_patch_browser_timer_or_swallow_global_errors():
    source = (ROOT / "static" / "js" / "api-client.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "window.setInterval =" not in source
    assert "window.setTimeout =" not in source
    assert "window.clearInterval =" not in source
    assert "_allTimers" not in source
    error_handlers = source.split("let pro = null;", 1)[0]
    assert "preventDefault()" not in error_handlers


def test_chart_spot_subscription_is_singleflight_and_static_phase_deduped():
    source = (ROOT / "static" / "js" / "chart-core.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "this._spotInFlight = null;" in source
    assert "if (this._spotInFlight) return this._spotInFlight;" in source
    assert "Date.now() - this._spotLastAttemptAt < 1000" in source
    assert "this._spotStaticPhase === requestKey + '|' + phase" in source
    assert "this._spotCallback = callback;" in source


def test_selected_top_index_header_reuses_index_bar_quote():
    source = (ROOT / "static" / "js" / "nav-panel.js").read_text(
        encoding="utf-8", errors="ignore"
    )

    assert "var renderedFromIndexBar = false;" in source
    assert "renderedFromIndexBar = true;" in source
    assert "&& !renderedFromIndexBar" in source
