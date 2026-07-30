"""tests/test_kline_bootstrap.py — K线数据自举测试

验证 fresh-clone 场景：当本地 SQLite 缓存为空、CSV 文件不存在时，
KLineService 能通过 QMT HTTP 或 QMT xtdata 在线获取数据。
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('BOARD_APP_AUTO_BOOTSTRAP', '0')
os.environ.setdefault('QMT_ENABLED', '0')
os.environ.setdefault('QMT_AUTO_START', '0')
os.environ.setdefault(
    'ANNOTATION_VAULT_PATH',
    str(PROJECT_ROOT / 'vault' / 'TradingVault'),
)

from services.kline_service import KLineService


def _make_service_with_empty_db():
    """创建 KLineService 实例，SQLite 和 QMT 均返回空（模拟 fresh clone）。"""
    svc = KLineService.__new__(KLineService)
    svc._cache = MagicMock()
    svc._cache.get.return_value = None
    svc._cache.set = MagicMock()
    svc._cache.delete = MagicMock()

    empty_db = MagicMock()
    empty_db.read_kline.return_value = None
    empty_db.save_kline = MagicMock()
    svc._db = empty_db

    svc._qmt = MagicMock()
    svc._qmt.to_qmt_code = lambda code, data_type: f"{code}.SH"
    return svc


def test_stock_daily_empty_db_falls_back_to_qmt_http():
    """SQLite 无数据时，日线应通过 QMT HTTP (/candles) 获取。"""
    svc = _make_service_with_empty_db()

    qmt_http_df = pd.DataFrame([
        {
            "date": "2026-07-29",
            "open": 1680.0,
            "high": 1695.0,
            "low": 1675.0,
            "close": 1690.0,
            "volume": 12345678,
        }
    ])

    # 模拟第一次 read_kline 返回 None（空库），
    # save_kline 后第二次 read_kline 返回数据
    read_call_count = [0]
    def fake_read(code, period):
        read_call_count[0] += 1
        if read_call_count[0] <= 1:
            return None
        return qmt_http_df
    svc._db.read_kline = fake_read
    svc._db.save_kline = MagicMock()

    with patch("services.kline_service._qmt_http_daily", lambda code, count=-1: qmt_http_df), \
         patch("services.kline_service.is_qmt_available", lambda: False):
        df = svc._do_load("stock", "600519", "daily", "", "stock:600519:daily")

    assert not df.empty, "空库时应通过 QMT HTTP 获取数据"
    assert df.iloc[-1]["close"] == 1690.0
    assert svc._db.save_kline.called, "获取数据后应保存到 SQLite"


def test_stock_daily_empty_db_no_qmt_http_falls_back_to_qmt_xtapi():
    """QMT HTTP 不可用时，应降级到 QMT xtdata 获取日线。"""
    svc = _make_service_with_empty_db()

    qmt_df = pd.DataFrame([
        {
            "date": "2026-07-28",
            "open": 1670.0,
            "high": 1685.0,
            "low": 1665.0,
            "close": 1680.0,
            "volume": 9876543,
        },
        {
            "date": "2026-07-29",
            "open": 1680.0,
            "high": 1695.0,
            "low": 1675.0,
            "close": 1690.0,
            "volume": 12345678,
        }
    ])

    # 模拟 SQLite: 第一次 read 返回 None（空库），
    # save_kline 后第二次 read 返回数据
    call_count = [0]
    def fake_read(code, period):
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # 初始查询，空库
        return qmt_df    # save 后重新读取，返回数据
    svc._db.read_kline = fake_read
    svc._db.save_kline = MagicMock()

    svc._qmt.get_daily = MagicMock(return_value=qmt_df)
    svc._qmt.to_qmt_code = lambda code, data_type: f"{code}.SH"

    with patch("services.kline_service._qmt_http_daily", lambda code, count=-1: pd.DataFrame()), \
         patch("services.kline_service.is_qmt_available", lambda: True):
        df = svc._do_load("stock", "600519", "daily", "", "stock:600519:daily")

    assert not df.empty, "QMT HTTP 失败时应通过 xtdata 获取数据"
    assert df.iloc[-1]["close"] == 1690.0
    assert svc._qmt.get_daily.called, "应调用 QMT xtdata get_daily"


def test_stock_daily_all_sources_fail_returns_empty():
    """所有数据源都不可用时，应返回空 DataFrame，不崩溃。"""
    svc = _make_service_with_empty_db()
    svc._db.read_kline.return_value = None

    with patch("services.kline_service._qmt_http_daily", lambda code, count=-1: pd.DataFrame()), \
         patch("services.kline_service.is_qmt_available", lambda: False):
        df = svc._do_load("stock", "600519", "daily", "", "stock:600519:daily")

    assert df.empty, "所有源不可用时应返回空 DataFrame"


def test_board_daily_empty_db_loads_from_board_kline():
    """板块日线在 SQLite 为空时，应通过 load_board_kline 获取。"""
    svc = _make_service_with_empty_db()
    svc._db.read_kline.return_value = None

    board_df = pd.DataFrame([
        {
            "date": "2026-07-29",
            "open": 1000.0,
            "high": 1010.0,
            "low": 995.0,
            "close": 1005.0,
            "volume": 5000000,
        }
    ])

    with patch("services.kline_service.load_board_kline", lambda *a, **kw: board_df):
        df = svc._do_load("industry", "BK1158", "daily", "半导体", "industry:BK1158:daily")

    assert not df.empty, "板块日线空库时应通过 load_board_kline 获取"
    assert df.iloc[-1]["close"] == 1005.0
