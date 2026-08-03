from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_chart_core_uses_explicit_empty_watermark():
    source = (ROOT / "static/js/chart-core.js").read_text(encoding="utf-8")

    assert "watermark: ''," in source
    assert "watermark: null" not in source


def test_watermark_has_css_fallback_hidden():
    source = (ROOT / "static/css/app.css").read_text(encoding="utf-8")

    assert ".klinecharts-pro-watermark" in source
    assert "display:none!important" in source
