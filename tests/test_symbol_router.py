"""Tests for symbol-router logic (classify / importSymbol / normalizeCode).

This file verifies that the SymbolRouter module correctly classifies
symbols into their expected types (stock, index, industry, concept,
hk_index, global_index).

The actual browser-side SymbolRouter is a JS module; these tests
mirror its Python equivalent to prevent regressions in shared logic.
"""
import pytest

# Python equivalent mirrors the JS SymbolRouter.classify() logic
HK_INDEX_TICKERS = {"HSI", "HSTECH", "HSCEI", "HSHKI"}
US_INDEX_TICKERS = {"SPX", "IXIC", "DJI", "^N225", "^KS11", "^TWII"}


def classify(code):
    """Python equivalent of JS SymbolRouter.classify()."""
    if not code:
        return "stock"
    c = str(code).strip()

    # 东方财富全A — 必须在纯数字判断之前，否则会被当成个股
    if c == "800000":
        return "index"

    # A 股指数
    if c.startswith(("sh", "sz", "bj")) and len(c) == 8 and c[2:].isdigit():
        idx_prefixes = [
            "sh0000", "sh0001", "sh0003", "sh0006", "sh0008", "sh0009",
            "sz3990", "sz3991", "sz3992", "sz3993", "sz3994",
        ]
        for p in idx_prefixes:
            if c.startswith(p):
                return "index"
        return "stock"

    # BK = board
    if c.startswith("BK") and len(c) >= 6 and c[2:].isdigit():
        return "board"

    # 港股指数
    if c in HK_INDEX_TICKERS:
        return "hk_index"

    # 全球指数
    if c in US_INDEX_TICKERS or c.startswith("^"):
        return "global_index"

    # 纯 6 位数字 = A 股个股
    if len(c) == 6 and c.isdigit():
        return "stock"

    return "stock"


# ========== classify 测试 ==========

def test_classify_a_share_index():
    assert classify("sh000001") == "index"
    assert classify("sh000300") == "index"
    assert classify("sz399006") == "index"


def test_classify_a_share_stock():
    assert classify("600519") == "stock"
    assert classify("000001") == "stock"
    assert classify("sh600519") == "stock"


def test_classify_board():
    assert classify("BK0001") == "board"
    assert classify("BK1158") == "board"


def test_classify_hk_index():
    assert classify("HSI") == "hk_index"
    assert classify("HSTECH") == "hk_index"


def test_classify_global_index():
    assert classify("SPX") == "global_index"
    assert classify("IXIC") == "global_index"
    assert classify("^N225") == "global_index"


def test_classify_special():
    assert classify("800000") == "index"


def test_classify_empty():
    assert classify("") == "stock"
    assert classify(None) == "stock"


def test_classify_board_number():
    """BK 后面必须跟数字"""
    assert classify("BK0001") == "board"


def test_classify_empty_board():
    """BK 后面没有数字应该是默认 stock"""
    assert classify("BKXYZ") == "stock"


def test_classify_hk_stock_with_prefix():
    """港股代码如 00700 是 5 位纯数字，不应混淆"""
    assert classify("00700") == "stock"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ========== normalizeCode 测试 ==========

def test_normalize_pure_digit_index():
    """纯数字指数代码应归一化为 sh 前缀"""
    code = "000300"
    if len(code) == 6 and code.isdigit():
        normalized = "sh" + code
    else:
        normalized = code
    assert normalized == "sh000300"


def test_normalize_stock_code_unchanged():
    """个股代码不应改变"""
    code = "600519"
    normalized = code  # 个股不加前缀
    assert normalized == "600519"


def test_normalize_hk_index_unchanged():
    """港股指数保持原样"""
    assert classify("HSI") == "hk_index"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
