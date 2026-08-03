"""Focused contracts for the chart light/dark theme switch."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
CONTROLLER = ROOT / "static" / "js" / "app-init.js"
CHART = ROOT / "static" / "js" / "chart-core.js"
CSS = ROOT / "static" / "css" / "app.css"


def test_theme_controller_lives_in_production_app_init():
    html = INDEX.read_text(encoding="utf-8")
    assert "app-init.js" in html
    assert html.index("chart-core.js") < html.index("app-init.js")


def test_theme_controller_persists_and_uses_pro_theme_api():
    source = CONTROLLER.read_text(encoding="utf-8")
    assert "board-app.chart-theme" in source
    assert "localStorage.getItem" in source
    assert "localStorage.setItem" in source
    assert "chart.setTheme(next)" in source
    assert "chart.getTheme()" in source
    assert "return 'dark'" in source


def test_theme_toggle_is_mounted_in_klinecharts_toolbar():
    source = CONTROLLER.read_text(encoding="utf-8")
    assert "getElementById('toolbar')" in source
    assert "board-theme-toggle" in source
    assert "切换到浅色主题" in source
    assert "切换到深色主题" in source


def test_chart_core_reads_persisted_theme_and_keeps_red_green_candles():
    source = CHART.read_text(encoding="utf-8")
    assert "getBoardChartTheme()" in source
    assert "theme: typeof getBoardChartTheme === 'function'" in source
    assert "upColor: '#ef5350'" in source
    assert "downColor: '#26a69a'" in source


def test_light_theme_covers_chart_shell_without_changing_quote_colors():
    source = CSS.read_text(encoding="utf-8")
    assert 'body[data-board-theme="light"] #pro-container' in source
    assert 'body[data-board-theme="light"] #toolbar' in source
    assert '#board-theme-toggle' in source


def test_light_chart_canvas_uses_crisp_high_contrast_palette():
    source = CONTROLLER.read_text(encoding="utf-8")
    assert "getChartStyles(next)" in source
    assert "'#f23645'" in source
    assert "'#089981'" in source
    assert "horizontal: { color: '#f0f3fa', size: 1 }" in source
    assert "tickText: { color: '#434651' }" in source
    assert "activeBackgroundColor: '#c7ccd6'" in source


def test_light_theme_covers_both_workspaces_with_contrast_overrides():
    source = CSS.read_text(encoding="utf-8")
    assert 'body[data-board-theme="light"] #nav-panel .board-item .board-name' in source
    assert 'body[data-board-theme="light"] #nav-panel .nav-header-title' in source
    assert 'color:#26384d!important' in source
    assert 'body[data-board-theme="light"] #sess-side' in source
    assert 'body[data-board-theme="light"] #sess-side .card' in source
    assert 'body[data-board-theme="light"] #sess-side textarea' in source


def test_light_theme_normalizes_handles_and_count_badges():
    source = CSS.read_text(encoding="utf-8")
    assert 'body[data-board-theme="light"] #nav-panel-toggle' in source
    assert 'body[data-board-theme="light"] #sess-toggle-btn' in source
    assert 'body[data-board-theme="light"] #nav-panel .count' in source
    assert 'background:#e4ecf5!important' in source


def test_chart_assets_bust_stale_theme_and_watermark_cache():
    html = INDEX.read_text(encoding="utf-8")
    app_css = re.search(r'app\.css\?v=([^"&]+)', html)
    assert app_css, "app.css must have a cache-busting version"
    assert "range-labels" in app_css.group(1)
    assert "comparison-percent" in html
    chart_core = re.search(r'chart-core\.js\?v=([^"&]+)', html)
    assert chart_core, "chart-core.js must have a cache-busting version"
    assert "period-context" in chart_core.group(1)
    assert "app-init.js?v=20260801-tradingview-light-pan" in html


def test_light_theme_index_rail_has_readable_cards_and_light_dividers():
    source = CSS.read_text(encoding="utf-8")
    assert 'body[data-board-theme="light"] #toolbar' in source
    assert 'body[data-board-theme="light"] #index-bar' in source
    assert 'background:#e8eef5!important' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-group' in source
    assert 'border-right-color:#cbd7e4!important' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-label' in source
    assert 'color:#1f3b5b!important' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-item .name' in source
    assert 'color:#173b68!important' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-item .price.flat' in source
    assert 'color:#334e68!important' in source
    assert '.idx-item .price.up{color:#ef5350}' in source
    assert '.idx-item .price.down{color:#26a69a}' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-item:hover' in source
    assert 'body[data-board-theme="light"] #index-bar .idx-item.active' in source
    assert 'body[data-board-theme="light"] #idx-add-btn' in source
    assert 'body[data-board-theme="light"] #main' in source
