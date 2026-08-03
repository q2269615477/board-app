"""Constituents loading contract.

The data-update architecture requires board constituents to come from the
process-resident JSON cache during normal page requests.  Tushare dc_member is
an offline updater, not an interactive fallback.
"""

import os
import sqlite3

os.environ.setdefault("BOARD_APP_AUTO_BOOTSTRAP", "0")


def test_get_constituents_uses_memory_cache(monkeypatch):
    import services.board_service as bs

    svc = bs.get_board_service()
    svc._cache.clear()

    with bs._constituents_json_lock:
        bs._constituents_json_cache.clear()
        bs._constituents_json_cache.update({
            "concept": {
                "concept:BKTEST": {
                    "name": "测试板块",
                    "cons": [{"code": "000001", "name": "平安银行"}],
                }
            }
        })

    monkeypatch.setattr(
        bs,
        "get_concept_constituents",
        lambda code: (_ for _ in ()).throw(AssertionError("must not call Tushare fallback")),
    )
    monkeypatch.setattr(bs.BoardService, "_enrich_constituents", lambda self, cons, live=False: cons)

    rows = svc.get_constituents("concept", "BKTEST")
    assert rows == [{"code": "000001", "name": "平安银行"}]


def test_board_service_has_no_interactive_tushare_sleep_loop():
    from pathlib import Path

    source = Path("services/board_service.py").read_text(encoding="utf-8")
    assert "sleep(0.35)" not in source
    assert "still_missing[:50]" not in source


def test_live_constituent_refresh_uses_qmt_http_18080(monkeypatch, tmp_path):
    import services.board_service as bs

    db = tmp_path / "kline.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE kline (
            code TEXT, period TEXT, date TEXT, close REAL, volume REAL
        );
        CREATE TABLE mkt_cap (code TEXT PRIMARY KEY, mkt_cap REAL);
        INSERT INTO mkt_cap VALUES ('603259', 1234.5);
    """)
    conn.commit()
    conn.close()
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        bs.sqlite3,
        "connect",
        lambda _path: real_connect(db),
    )

    class FakeHttp:
        def ohlc_batch(self, codes, **kwargs):
            assert codes == ["603259"]
            return {
                "items": {
                    "603259": {
                        "close": 128.43,
                        "pre_close": 125.48,
                        "change_pct": 2.3509,
                        "volume": 570793,
                    }
                }
            }

    monkeypatch.setattr(
        "data.qmt_http_client.get_qmt_http_client",
        lambda: FakeHttp(),
    )
    service = bs.BoardService.__new__(bs.BoardService)
    rows = service._enrich_constituents(
        [{"code": "603259", "name": "药明康德"}],
        live=True,
    )
    assert rows[0]["close"] == 128.43
    assert rows[0]["change_pct"] == 2.35
    assert rows[0]["mkt_cap"] == 1234.5
    assert rows[0]["source"] == "qmt18080"


def test_settled_constituents_use_latest_rows_without_fixed_date_cutoff(
    monkeypatch, tmp_path
):
    import services.board_service as bs

    db = tmp_path / "kline.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE kline (
            code TEXT, period TEXT, date TEXT, close REAL, volume REAL
        );
        CREATE TABLE mkt_cap (code TEXT PRIMARY KEY, mkt_cap REAL);
        INSERT INTO kline VALUES ('600001', 'daily', '2025-01-02', 10, 100);
        INSERT INTO kline VALUES ('600001', 'daily', '2025-01-03', 11, 120);
        INSERT INTO mkt_cap VALUES ('600001', 500);
    """)
    conn.commit()
    conn.close()
    real_connect = sqlite3.connect
    monkeypatch.setattr(bs.sqlite3, "connect", lambda _path: real_connect(db))

    service = bs.BoardService.__new__(bs.BoardService)
    rows = service._enrich_constituents(
        [{"code": "600001", "name": "旧日期样本"}],
        live=False,
    )
    assert rows[0]["close"] == 11
    assert rows[0]["pre_close"] == 10
    assert rows[0]["change_pct"] == 10.0
