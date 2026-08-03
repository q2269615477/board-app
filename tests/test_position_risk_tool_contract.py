from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
TOOL = ROOT / "static" / "js" / "position-risk-tool.js"
CSS = ROOT / "static" / "css" / "app.css"
SESSION_UI = ROOT / "static" / "js" / "session-ui.js"


def test_position_risk_scripts_load_before_chart_initialization():
    html = INDEX.read_text(encoding="utf-8")
    model = html.index("position-risk-model.js")
    range_model = html.index("price-range-model.js")
    tool = html.index("position-risk-tool.js")
    chart = html.index("chart-core.js")
    assert model < range_model < tool < chart


def test_position_risk_tool_uses_chart_overlays_only():
    source = TOOL.read_text(encoding="utf-8")
    assert "registerOverlay" in source
    assert "createOverlay" in source
    assert "overrideOverlay" in source
    assert "removeOverlay" in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "WebSocket" not in source


def test_price_range_is_registered_as_native_overlay():
    source = TOOL.read_text(encoding="utf-8")
    assert "boardPriceRange" in source
    assert "priceRangeTemplate" in source
    assert "buildPriceRangeFigures" in source
    assert "createPriceRange" in source


def test_position_risk_ui_has_light_and_dark_theme_contracts():
    css = CSS.read_text(encoding="utf-8")
    source = TOOL.read_text(encoding="utf-8")
    assert ".board-position-tool-item" in css
    assert "#position-risk-modal" in css
    assert 'body[data-board-theme="light"] .position-risk-dialog' in css
    assert "position-price-range-tool" in source
    assert "position-long-tool" in source
    assert "position-short-tool" in source
    assert "position-risk-launcher" not in source
    assert "position-risk-menu" not in source


def test_position_risk_uses_profit_loss_ratio_and_clickable_entry_settings():
    source = TOOL.read_text(encoding="utf-8")
    assert "盈亏比" in source
    assert "盈亏比 1:" not in source
    assert "profitLossRatio" in source
    assert "investmentAmount" in source
    assert "data-position-amount=\"100000\"" in source
    assert "data-position-amount=\"1000000\"" in source
    assert "onSelected" in source


def test_session_overlay_snapshot_preserves_position_calculator_settings():
    source = SESSION_UI.read_text(encoding="utf-8")
    assert "normalized.extendData = o.extendData" in source
