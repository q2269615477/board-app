from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "js" / "session-ui.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_kline_tooltip_has_latest_bar_fallback():
    assert "function showLatestKlineTooltip()" in SOURCE
    assert "setTimeout(showLatestKlineTooltip, 0)" in SOURCE
    assert "showLatestKlineTooltip();" in SOURCE


def test_kline_tooltip_renders_change_and_volume_in_light_theme():
    assert "changePct == null" in SOURCE
    assert "涨跌" in SOURCE
    assert "fmtVol(k.volume)" in SOURCE
    assert "document.body && document.body.dataset.boardTheme === 'light'" in SOURCE
    assert "_tooltipEl.className = 'kline-tooltip'" in SOURCE


def test_kline_tooltip_repaints_when_theme_changes():
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "updateKlineTooltip({ kLineData: _tooltipLastKline })" in SOURCE
    assert 'body[data-board-theme="light"] #pro-container #kline-tooltip' in css


def test_kline_tooltip_does_not_auto_hide_after_update():
    assert "_tooltipTimer" not in SOURCE
    assert "el.style.display = 'flex';" in SOURCE
    assert "position:absolute;z-index:30" in SOURCE


def test_kline_tooltip_preserves_embedded_replay_controls_on_quote_updates():
    assert "_tooltipContentEl.id = 'kline-tooltip-content'" in SOURCE
    assert "content.innerHTML =" in SOURCE
    assert "el.innerHTML =" not in SOURCE
    assert "new CustomEvent('kline-tooltip-ready'" in SOURCE


def test_kline_tooltip_starts_after_drawing_rail_and_constrains_replay_controls():
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert '#pro-container #kline-tooltip{' in css
    assert 'left:52px!important' in css
    assert 'right:10px!important' in css
    assert 'max-width:calc(100% - 62px)' in css
    assert '#pro-container #bar-replay-controls[data-layout="price-bar"]{' in css
    assert 'flex:0 1 auto' in css
    assert '@media (max-width: 640px)' in css
    assert 'max-width:50%' in css


def test_kline_tooltip_rebinds_chart_after_delayed_pro_init():
    assert "if (chart && chart !== tooltipChartBound" in SOURCE
    assert "tooltipChartBound = chart;" in SOURCE


def test_kline_tooltip_handles_empty_chart_data():
    assert "const list = getDataList();" in SOURCE
    assert "if (!list.length) return;" in SOURCE


def test_kline_tooltip_uses_chinese_ohlc_labels():
    assert "<span>开盘</span>" in SOURCE
    assert "<span>最高</span>" in SOURCE
    assert "<span>最低</span>" in SOURCE
    assert "<span style=\"color:' + ch + '\">收盘</span>" in SOURCE
    assert "' <span>O</span>'" not in SOURCE
    assert "' <span>H</span>'" not in SOURCE
    assert "' <span>L</span>'" not in SOURCE
    assert "'>C</span>" not in SOURCE


def test_last_bar_anchor_only_removes_legacy_markers():
    assert "function markLastBarAnchor()" in SOURCE
    anchor_body = SOURCE.split("function markLastBarAnchor()", 1)[1].split(
        "let _anchorOvId = null;", 1
    )[0]
    assert "chart.removeOverlay" in anchor_body
    assert "baseId + '_lo'" in anchor_body
    assert "chart.createOverlay" not in anchor_body
    assert "simpleAnnotation" not in anchor_body
    assert "_anchorOvId = null" in anchor_body


def test_session_ui_cache_bust_covers_kline_tooltip_fix():
    assert "session-ui.js?v=20260802-price-bar-layout1" in INDEX
