"""exchange_calendar_service 接入 data_update_manager 的测试。

覆盖：
- 同一自然日，A股/香港/全球指数可有不同期望会话日；
- 休市（无会话日）时指数不被误刷新；
- 本地市场盘中时结算日线延后，按各市场状态判断；
- 日历服务未就绪时 A股沿用旧逻辑、其他市场按本地时区工作日回退。

services.exchange_calendar_service 由另一写集新增，这里用 FakeCalendar
按约定接口（latest_expected_session_date / market_state）注入。
"""
import sqlite3
from datetime import date, datetime

import pytest

import data_update_manager as dum


def _make_db(tmp_path):
    db = tmp_path / 'kline.db'
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kline "
        "(code TEXT, period TEXT, date TEXT, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL, updated_at TEXT)"
    )
    conn.commit()
    return db, conn


def _seed(conn, code, date_norm):
    conn.execute(
        "INSERT OR REPLACE INTO kline "
        "(code, period, date, open, high, low, close, volume, updated_at) "
        "VALUES (?, 'daily', ?, 100, 101, 99, 100.5, 1000, '')",
        (code, date_norm),
    )
    conn.commit()


class FakeCalendar:
    """模拟 services.exchange_calendar_service 的约定接口。"""

    def __init__(self, expected, open_states):
        self.expected = expected          # {code: 期望会话日(YYYY-MM-DD/YYYYMMDD)}
        self.open_states = open_states    # {code: market_open}

    def latest_expected_session_date(self, code, now=None):
        return self.expected[str(code)]

    def market_state(self, code, now=None):
        return {'market_open': self.open_states.get(str(code), False)}


@pytest.fixture
def index_env(tmp_path, monkeypatch):
    """把 update_all_indices_qmt 缩到 sh000001 + HSI 两个目标，接临时 DB。"""
    db, conn = _make_db(tmp_path)
    monkeypatch.setattr(dum, 'PREWARM_TARGETS', [
        ('sh000001', '上证指数', 'index'),
        ('HSI', '恒生指数', 'hk_index'),
    ])
    monkeypatch.setattr(dum, 'QMT_INDEX_MAP', {
        'sh000001': '000001.SH',
        'HSI': 'HSI.HK',
    })
    monkeypatch.setattr(dum, 'EASTMONEY_INDEX_CODES', frozenset())
    monkeypatch.setattr(dum, '_LEDGER_DB', str(db))
    monkeypatch.setattr(dum, '_ensure_ledger_schema', lambda: None)
    yield conn
    conn.close()


def _install_fake_calendar(monkeypatch, fake):
    monkeypatch.setattr(
        dum,
        '_exchange_calendar_api',
        lambda: (fake.latest_expected_session_date, fake.market_state),
    )


def test_normalize_session_date():
    assert dum._normalize_session_date(date(2026, 8, 3)) == '20260803'
    assert dum._normalize_session_date('2026-08-03') == '20260803'
    assert dum._normalize_session_date('20260803') == '20260803'
    assert dum._normalize_session_date(None) == ''
    assert dum._normalize_session_date('n/a') == ''


def test_same_calendar_day_different_target_dates_no_refresh_on_closed_market(
    index_env, monkeypatch
):
    """周一 15:35：A股目标 08-03；香港休市目标 07-31，且不误刷新。"""
    conn = index_env
    fake = FakeCalendar(
        expected={'sh000001': '2026-08-03', 'HSI': '2026-07-31'},
        open_states={'sh000001': False, 'HSI': False},
    )
    _install_fake_calendar(monkeypatch, fake)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: True)

    fetched = []

    def fake_fetch(code, start_date):
        fetched.append((code, start_date))
        if code == 'sh000001':
            return [{
                'date': '2026-08-03',
                'open': 100, 'high': 101, 'low': 99,
                'close': 100.5, 'volume': 1000,
            }]
        return []

    monkeypatch.setattr(dum, 'fetch_qmt_kline', fake_fetch)
    _seed(conn, 'sh000001', '2026-07-31')
    _seed(conn, 'HSI', '2026-07-31')

    result = dum.update_all_indices_qmt()

    assert result['failed'] == 0
    assert result['success'] == 2
    assert result['updated_codes'] == ['sh000001']
    status = dum._load_status()
    # 同一天，两个市场各自的目标日不同
    assert status['indices']['sh000001']['target_date'] == '20260803'
    assert status['indices']['HSI']['target_date'] == '20260731'
    # A股补上 08-03 当日 bar
    rows = conn.execute(
        "SELECT date FROM kline WHERE code='sh000001' AND period='daily' ORDER BY date"
    ).fetchall()
    assert [r[0] for r in rows] == ['2026-07-31', '2026-08-03']
    # 香港休市：未写 08-03，也未发起任何抓取
    rows = conn.execute(
        "SELECT date FROM kline WHERE code='HSI' AND period='daily' ORDER BY date"
    ).fetchall()
    assert [r[0] for r in rows] == ['2026-07-31']
    assert not any(code == 'HSI' for code, _start in fetched)


def test_intraday_open_market_defers_while_local_market_open(index_env, monkeypatch):
    """15:35 北京：A股已收盘照常结算；香港仍在盘中 → HSI 延后不落日线。"""
    conn = index_env
    fake = FakeCalendar(
        expected={'sh000001': '2026-08-03', 'HSI': '2026-08-03'},
        open_states={'sh000001': False, 'HSI': True},
    )
    _install_fake_calendar(monkeypatch, fake)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: True)
    monkeypatch.setattr(
        dum,
        'fetch_qmt_kline',
        lambda code, start: [{
            'date': '2026-08-03',
            'open': 100, 'high': 101, 'low': 99,
            'close': 100.5, 'volume': 1000,
        }] if code == 'sh000001' else [],
    )
    _seed(conn, 'sh000001', '2026-07-31')
    _seed(conn, 'HSI', '2026-07-31')

    result = dum.update_all_indices_qmt()

    assert result['deferred'] == 1
    assert result['success'] == 1
    assert result['completion_ready'] is False
    status = dum._load_status()
    assert status['indices']['HSI']['status'] == 'deferred'
    assert status['indices']['HSI']['target_date'] == '20260803'
    assert status['indices']['sh000001']['status'] == 'success'
    # 盘中市场不写正式日线；A股正常结算
    assert conn.execute(
        "SELECT COUNT(*) FROM kline WHERE code='HSI' AND date='2026-08-03'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM kline WHERE code='sh000001' AND date='2026-08-03'"
    ).fetchone()[0] == 1


def test_tushare_fallback_receives_per_market_target(index_env, monkeypatch):
    """QMT 无新 bar 时，Tushare 兜底收到各自市场的目标日。"""
    conn = index_env
    fake = FakeCalendar(
        expected={'sh000001': '2026-08-03', 'HSI': '2026-07-31'},
        open_states={'sh000001': False, 'HSI': False},
    )
    _install_fake_calendar(monkeypatch, fake)
    monkeypatch.setattr(dum, '_qmt_connect', lambda: True)
    monkeypatch.setattr(dum, 'fetch_qmt_kline', lambda code, start: [])
    _seed(conn, 'sh000001', '2026-07-30')
    _seed(conn, 'HSI', '2026-07-30')

    fallback_calls = []

    def fake_fallback(code, name, local_max, last_td_norm, cur, insert_sql, now_str):
        fallback_calls.append((code, local_max, last_td_norm))
        return 0

    monkeypatch.setattr(dum, '_tushare_fallback_single_index', fake_fallback)

    dum.update_all_indices_qmt()

    assert ('sh000001', '20260730', '20260803') in fallback_calls
    assert ('HSI', '20260730', '20260731') in fallback_calls


def test_fallback_without_service_keeps_a_share_legacy_and_local_weekday(monkeypatch):
    """服务未落地：A股沿用旧 last_td_norm；HSI 按香港时区最近工作日回退。"""
    monkeypatch.setattr(dum, '_exchange_calendar_api', lambda: None)
    saturday = datetime(2026, 8, 1, 12, 0)

    target_a, open_a = dum._index_session_target(
        'sh000001', now=saturday, fallback_td_norm='20260731'
    )
    assert (target_a, open_a) == ('20260731', False)

    target_hk, open_hk = dum._index_session_target('HSI', now=saturday)
    assert (target_hk, open_hk) == ('20260731', False)


def test_service_exception_falls_back_without_breaking_update(monkeypatch):
    """日历服务抛异常时回退工作日逻辑，不中断指数更新。"""
    def broken_latest(code, now=None):
        raise RuntimeError('calendar unavailable')

    monkeypatch.setattr(
        dum,
        '_exchange_calendar_api',
        lambda: (broken_latest, lambda code, now=None: {'market_open': False}),
    )

    target, is_open = dum._index_session_target(
        'HSI', now=datetime(2026, 8, 3, 15, 35), fallback_td_norm='20260803'
    )
    assert is_open is False
    assert target == '20260803'  # 周一 → 香港本地工作日
