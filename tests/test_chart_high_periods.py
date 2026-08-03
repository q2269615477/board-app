import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHART_CORE = ROOT / "static" / "js" / "chart-core.js"
PRO_VENDOR = ROOT / "static" / "js" / "klinecharts-pro.umd.js"


def _run_node(source: str) -> str:
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _vendor_range_function() -> str:
    source = PRO_VENDOR.read_text(encoding="utf-8")
    start = source.index("F1=(d,f,v)=>") + len("F1=")
    end = source.index(";return Z0", start)
    return source[start:end]


def test_vendor_high_period_ranges_move_backwards_from_anchor():
    range_function = _vendor_range_function()
    output = _run_node(
        f"""
const periodRange = {range_function};
const anchor = new Date(2026, 7, 1).getTime();
const periods = [
  {{ multiplier: 1, timespan: 'week' }},
  {{ multiplier: 1, timespan: 'month' }},
  {{ multiplier: 3, timespan: 'month' }},
  {{ multiplier: 1, timespan: 'year' }},
];
const ranges = periods.map(p => periodRange(p, anchor, 500));
const firstMonthly = ranges[1][0];
const previousMonthEnd = periodRange(periods[1], firstMonthly, 1)[0];
const olderMonthly = periodRange(periods[1], previousMonthEnd, 500)[0];
process.stdout.write(JSON.stringify({{ ranges, firstMonthly, previousMonthEnd, olderMonthly }}));
"""
    )
    data = json.loads(output)
    for start, end in data["ranges"]:
        assert start < end <= 1_800_000_000_000
    assert data["olderMonthly"] < data["previousMonthEnd"] < data["firstMonthly"]


def test_datafeed_maps_native_year_and_dedupes_logical_periods():
    source = CHART_CORE.read_text(encoding="utf-8")
    datafeed_source = source.split("const _datafeed = new BoardDatafeed();", 1)[0]
    output = _run_node(
        datafeed_source
        + """
const feed = new BoardDatafeed();
const ts = value => Date.parse(value + 'T00:00:00Z');
const monthly = feed._dedupeSort([
  { timestamp: ts('2026-06-30'), close: 10 },
  { timestamp: ts('2026-07-30'), close: 11 },
  { timestamp: ts('2026-07-31'), close: 12 },
], 'monthly');
const quarterly = feed._dedupeSort([
  { timestamp: ts('2026-04-30'), close: 20 },
  { timestamp: ts('2026-06-30'), close: 21 },
], 'quarterly');
const yearly = feed._dedupeSort([
  { timestamp: ts('2026-06-30'), close: 30 },
  { timestamp: ts('2026-07-31'), close: 31 },
], 'yearly');
const daily = feed._dedupeSort([
  { timestamp: ts('2026-07-30'), close: 40 },
  { timestamp: ts('2026-07-31'), close: 41 },
], 'daily');
const expandedQuarter = feed._expandHistoryWindow(
  'quarterly', ts('2025-01-01'), ts('2026-08-01')
);
const expandedYear = feed._expandHistoryWindow(
  'yearly', ts('2020-01-01'), ts('2026-01-01')
);
const shanghaiYearStart = Date.UTC(2025, 11, 31, 16);
const expandedShanghaiYear = feed._expandHistoryWindow(
  'yearly', shanghaiYearStart, shanghaiYearStart
);
const unseenOlder = feed._onlyUnseenOlderRows([
  { timestamp: ts('1989-12-31'), close: 1 },
  { timestamp: ts('1990-12-31'), close: 2 },
  { timestamp: ts('2026-07-31'), close: 3 },
], { min: ts('1990-12-31'), max: ts('2026-07-31') }, true);
process.stdout.write(JSON.stringify({
  apiPeriod: feed._periodToApi({ multiplier: 1, timespan: 'year' }),
  normalizedNegative: feed._normalizeTs(-14000000000000),
  monthly, quarterly, yearly, daily, expandedQuarter, expandedYear,
  expandedShanghaiYear, unseenOlder,
}));
"""
    )
    data = json.loads(output)
    assert data["apiPeriod"] == "yearly"
    assert data["normalizedNegative"] == 1
    assert [row["close"] for row in data["monthly"]] == [10, 12]
    assert [row["close"] for row in data["quarterly"]] == [21]
    assert [row["close"] for row in data["yearly"]] == [31]
    assert [row["close"] for row in data["daily"]] == [40, 41]
    assert data["expandedQuarter"] == {
        "low": 1735689600000,
        "high": 1790812799999,
    }
    assert data["expandedYear"] == {
        "low": 1577836800000,
        "high": 1798761599999,
    }
    assert data["expandedShanghaiYear"] == {
        "low": 1767225600000,
        "high": 1798761599999,
    }
    assert [row["close"] for row in data["unseenOlder"]] == [1]


def test_all_year_period_adapters_use_native_year_protocol():
    chart = CHART_CORE.read_text(encoding="utf-8")
    ui_state = (ROOT / "static" / "js" / "ui-state.js").read_text(encoding="utf-8")
    session_ui = (ROOT / "static" / "js" / "session-ui.js").read_text(encoding="utf-8")

    assert "{ multiplier: 1, timespan: 'year', text: '年' }" in chart
    assert "if(t==='year') return 'yearly';" in chart
    assert "if (p.timespan === 'year') return 'yearly';" in chart
    assert "if (p === 'yearly') return { multiplier: 1, timespan: 'year' };" in ui_state
    assert "else if (t === 'year') period = 'yearly';" in session_ui
