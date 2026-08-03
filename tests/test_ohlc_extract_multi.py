"""Unit tests for multi-symbol OHLC extract (no QMT runtime)."""
import sys
from pathlib import Path

# qmt-http-server deploy is not a package; load by path
DEPLOY = Path(__file__).resolve().parents[1] / "qmt-http-server" / "deploy"
sys.path.insert(0, str(DEPLOY))

from server_market_utils import (  # noqa: E402
    _bar_to_ohlc_item,
    extract_ohlc_bars_by_symbol,
    normalize_market_volume,
)


def test_signed_int32_volume_overflow_is_restored():
    assert normalize_market_volume(-1959715090) == 2335252206
    assert normalize_market_volume(12345) == 12345


class _FakeDF:
    """Minimal DataFrame-like: columns + loc[t, col]."""

    def __init__(self, columns, data):
        # data: {col: {time: value}}
        self.columns = columns
        self._data = data
        times = []
        for col in columns:
            for t in data.get(col, {}):
                if t not in times:
                    times.append(t)
        self.index = times

    @property
    def loc(self):
        return self

    def __getitem__(self, key):
        # matrix[col] -> series-like dict
        if isinstance(key, str) or key in self.columns:
            return self._data.get(key, {})
        # loc[t, col]
        if isinstance(key, tuple) and len(key) == 2:
            t, col = key
            return self._data.get(col, {}).get(t)
        raise KeyError(key)


def test_extract_field_dataframe_multi():
    times = ["20260727", "20260728"]
    symbols = ["600519.SH", "000001.SZ"]
    fields = ["open", "high", "low", "close", "volume", "amount"]
    result = {}
    for f in fields:
        col_data = {}
        for i, sym in enumerate(symbols):
            col_data[sym] = {
                times[0]: 10.0 + i,
                times[1]: 11.0 + i,
            }
            if f == "close":
                col_data[sym][times[1]] = 20.0 + i
        result[f] = _FakeDF(symbols, col_data)

    by = extract_ohlc_bars_by_symbol(result, fields, symbols, "1d")
    assert len(by["600519.SH"]) == 2
    item = _bar_to_ohlc_item("600519.SH", by["600519.SH"])
    assert item["close"] == 20.0
    assert item["pre_close"] == 10.0
    assert item["change_pct"] is not None


def test_extract_single_symbol_dict_path():
    fields = ["open", "high", "low", "close", "volume", "amount"]
    result = {
        "open": {"t1": 1.0, "t2": 1.1},
        "high": {"t1": 1.2, "t2": 1.3},
        "low": {"t1": 0.9, "t2": 1.0},
        "close": {"t1": 1.05, "t2": 1.25},
        "volume": {"t1": 100, "t2": 110},
        "amount": {"t1": 1000, "t2": 1100},
    }
    by = extract_ohlc_bars_by_symbol(result, fields, ["600519.SH"], "1d")
    assert len(by["600519.SH"]) >= 1
    item = _bar_to_ohlc_item("600519.SH", by["600519.SH"])
    assert item["close"] == 1.25
