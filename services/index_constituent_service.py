"""Local index constituent snapshots.

The panel must not call Tushare while a user is clicking an index.  This
module owns the small, local snapshot/catalog used by that read path and the
offline updater.  ``panel_code`` is deliberately kept separate from the
provider's ``source_code`` because the same displayed index may have
different provider codes.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pandas as pd

from core.config import BOARD_CLASSIFICATION_FILE, SQLITE_PATH

logger = logging.getLogger('index_constituent_service')

_PANEL_CODE_RE = re.compile(r'^(sh|sz|bj)(\d{6})$', re.IGNORECASE)
_DIGITS_RE = re.compile(r'^\d{6}$')
_stock_name_cache: Optional[dict[str, str]] = None
_stock_name_lock = threading.Lock()


def normalize_panel_code(code: Any) -> str:
    """Return a stable lower-case panel code without a provider suffix."""
    value = str(code or '').strip()
    if '.' in value:
        value = value.split('.', 1)[0]
    return value.lower()


def source_code_candidates(panel_code: str) -> list[str]:
    """Build provider-code candidates from an explicit panel code.

    Tushare commonly exposes mainland indices with ``.SH``/``.SZ`` while
    some CSI instruments are returned under ``.CSI``.  We try the exchange
    mapping first and retain the actual successful code in the catalog.
    """
    panel = normalize_panel_code(panel_code)
    match = _PANEL_CODE_RE.match(panel)
    if match:
        exchange, number = match.groups()
        exchange = exchange.upper()
    elif _DIGITS_RE.match(panel):
        number = panel
        if number.startswith('399'):
            exchange = 'SZ'
        elif number.startswith(('899', '4', '8', '92')):
            exchange = 'BJ'
        else:
            exchange = 'SH'
    else:
        return []

    candidates = [f'{number}.{exchange}']
    if exchange in {'SH', 'SZ'}:
        candidates.append(f'{number}.CSI')
        candidates.append(f'{number}.{"SZ" if exchange == "SH" else "SH"}')
    return list(dict.fromkeys(candidates))


def discover_indices(classification_path: Optional[Path] = None) -> list[dict]:
    """Discover every ``type=index`` entry in the classification tree."""
    path = Path(classification_path or BOARD_CLASSIFICATION_FILE)
    with path.open('r', encoding='utf-8') as handle:
        document = json.load(handle)

    found: dict[str, dict] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get('type') == 'index' and value.get('code'):
                panel_code = normalize_panel_code(value['code'])
                found.setdefault(panel_code, {
                    'panel_code': panel_code,
                    'name': str(value.get('name') or panel_code),
                    'type': 'index',
                    'source_candidates': source_code_candidates(panel_code),
                })
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return sorted(found.values(), key=lambda item: item['panel_code'])


def _date_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ''
    raw = str(value).strip()
    if not raw or raw.lower() == 'nan':
        return ''
    if len(raw) >= 8 and raw[:8].isdigit():
        return f'{raw[:4]}-{raw[4:6]}-{raw[6:8]}'
    try:
        return pd.Timestamp(raw).strftime('%Y-%m-%d')
    except Exception:
        return raw[:10]


def _text(row: Any, *keys: str) -> str:
    for key in keys:
        try:
            value = row.get(key)
        except AttributeError:
            value = None
        if value is not None and not pd.isna(value) and str(value).strip():
            return str(value).strip()
    return ''


def _weight(row: Any) -> Optional[float]:
    for key in ('weight', 'con_weight', 'weight_pct'):
        try:
            value = row.get(key)
            if value is not None and not pd.isna(value):
                return float(value)
        except (AttributeError, TypeError, ValueError):
            continue
    return None


def normalize_weights(data: Any, source: str) -> list[dict]:
    """Normalize a Tushare response and keep only its newest date."""
    if data is None:
        return []
    if isinstance(data, pd.DataFrame):
        rows: Iterable[Any] = data.to_dict('records')
    elif isinstance(data, dict):
        rows = data.get('data') or data.get('rows') or []
    else:
        rows = data

    normalized = []
    for row in rows or []:
        code = _text(row, 'con_code', 'constituent_code', 'code')
        if not code:
            continue
        code = code.split('.', 1)[0]
        if not re.fullmatch(r'\d{6}', code):
            continue
        trade_date = _date_value(_text(row, 'trade_date', 'as_of_date', 'end_date'))
        normalized.append({
            'code': code,
            'name': _text(row, 'con_name', 'name', 'constituent_name') or code,
            'weight': _weight(row),
            'as_of_date': trade_date,
            'source': source,
        })
    if not normalized:
        return []
    latest = max((row['as_of_date'] for row in normalized), default='')
    if latest:
        normalized = [row for row in normalized if row['as_of_date'] == latest]
    unique = {}
    for row in normalized:
        unique[row['code']] = row
    return list(unique.values())


def local_stock_names() -> dict[str, str]:
    """Build a process cache from the tracked constituent seeds."""
    global _stock_name_cache
    with _stock_name_lock:
        if _stock_name_cache is not None:
            return _stock_name_cache
        names: dict[str, str] = {}
        data_dir = Path(BOARD_CLASSIFICATION_FILE).resolve().parent.parent / 'data'
        for filename in ('industry_constituents.json', 'concept_constituents.json'):
            path = data_dir / filename
            try:
                document = json.loads(path.read_text(encoding='utf-8'))
                for entry in document.values():
                    for row in entry.get('cons', []):
                        code = str(row.get('code') or '').strip()
                        name = str(row.get('name') or '').strip()
                        if code and name:
                            names[code] = name
            except Exception as exc:
                logger.debug('[index_constituents] name seed %s: %s', path, exc)
        _stock_name_cache = names
        return _stock_name_cache


class IndexConstituentService:
    """SQLite-backed catalog and latest-snapshot reader/writer."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or SQLITE_PATH)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS index_catalog (
                    panel_code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_code TEXT,
                    provider TEXT NOT NULL DEFAULT 'tushare',
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    as_of_date TEXT,
                    member_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS index_constituents (
                    panel_code TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    constituent_code TEXT NOT NULL,
                    constituent_name TEXT NOT NULL,
                    weight REAL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (panel_code, as_of_date, constituent_code)
                );
                CREATE INDEX IF NOT EXISTS idx_index_constituents_latest
                    ON index_constituents(panel_code, as_of_date);
                """
            )

    def _get_pro(self):
        from core.env_bootstrap import ensure_tushare_token

        if not ensure_tushare_token():
            raise RuntimeError('TUSHARE_TOKEN is not configured')
        from data_loader import get_tushare_pro

        pro = get_tushare_pro()
        if pro is None:
            raise RuntimeError(
                'TUSHARE_TOKEN is not configured or Tushare client initialization failed'
            )
        return pro

    def fetch_index_weights(self, source_code: str, pro=None) -> list[dict]:
        """Fetch the latest available index_weight snapshot for one code."""
        pro = pro or self._get_pro()
        start = (datetime.now() - timedelta(days=120)).strftime('%Y%m%d')
        end = datetime.now().strftime('%Y%m%d')
        last_error = None
        for keyword in ('index_code', 'ts_code'):
            try:
                frame = pro.index_weight(**{
                    keyword: source_code,
                    'start_date': start,
                    'end_date': end,
                })
                rows = normalize_weights(frame, 'tushare.index_weight')
                if rows:
                    return rows
                return []
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                raise RuntimeError(f'{source_code}: {exc}') from exc
        if last_error:
            raise RuntimeError(f'{source_code}: index_weight signature unsupported') from last_error
        return []

    def get_latest(self, panel_code: str) -> list[dict]:
        """Read only the newest local snapshot; never contacts Tushare."""
        panel = normalize_panel_code(panel_code)
        with self._connection() as conn:
            latest = conn.execute(
                'SELECT MAX(as_of_date) FROM index_constituents WHERE panel_code=?',
                (panel,),
            ).fetchone()[0]
            if not latest:
                return []
            rows = conn.execute(
                """SELECT constituent_code, constituent_name, weight, source, as_of_date
                   FROM index_constituents
                   WHERE panel_code=? AND as_of_date=?
                   ORDER BY COALESCE(weight, -1) DESC, constituent_code""",
                (panel, latest),
            ).fetchall()
        return [
            {
                'code': row['constituent_code'],
                'name': row['constituent_name'],
                'weight': row['weight'],
                'source': row['source'],
                'as_of_date': row['as_of_date'],
            }
            for row in rows
        ]

    def get_catalog(self, panel_code: Optional[str] = None) -> list[dict]:
        with self._connection() as conn:
            if panel_code:
                rows = conn.execute(
                    'SELECT * FROM index_catalog WHERE panel_code=?',
                    (normalize_panel_code(panel_code),),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM index_catalog ORDER BY panel_code'
                ).fetchall()
        return [dict(row) for row in rows]

    def _record_catalog(self, item: dict, **updates) -> None:
        now = datetime.now().isoformat(timespec='seconds')
        values = {
            'panel_code': item['panel_code'],
            'name': item.get('name') or item['panel_code'],
            'source_code': updates.get('source_code'),
            'provider': 'tushare',
            'status': updates.get('status', 'pending'),
            'error': updates.get('error'),
            'as_of_date': updates.get('as_of_date'),
            'member_count': updates.get('member_count', 0),
            'last_attempt_at': now,
            'updated_at': now,
        }
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO index_catalog
                   (panel_code,name,source_code,provider,status,error,as_of_date,
                    member_count,last_attempt_at,updated_at)
                   VALUES (:panel_code,:name,:source_code,:provider,:status,:error,
                           :as_of_date,:member_count,:last_attempt_at,:updated_at)
                   ON CONFLICT(panel_code) DO UPDATE SET
                    name=excluded.name, source_code=COALESCE(excluded.source_code,
                    index_catalog.source_code), provider=excluded.provider,
                    status=excluded.status, error=excluded.error,
                    as_of_date=COALESCE(excluded.as_of_date,index_catalog.as_of_date),
                    member_count=CASE WHEN excluded.member_count > 0 THEN
                    excluded.member_count ELSE index_catalog.member_count END,
                    last_attempt_at=excluded.last_attempt_at,
                    updated_at=excluded.updated_at""",
                values,
            )
            conn.commit()

    def update_index(
        self,
        item: dict,
        *,
        dry_run: bool = False,
        fetcher: Optional[Callable[[str], list[dict]]] = None,
    ) -> dict:
        """Fetch and atomically replace one index snapshot.

        A failed update never deletes the previous successful snapshot.
        """
        panel = normalize_panel_code(item.get('panel_code'))
        normalized_item = dict(item, panel_code=panel)
        candidates = item.get('source_candidates') or source_code_candidates(panel)
        try:
            rows = []
            source_code = None
            for candidate in candidates:
                rows = (fetcher(candidate) if fetcher else self.fetch_index_weights(candidate)) or []
                if rows:
                    source_code = candidate
                    break
            if not rows:
                result = dict(normalized_item, status='unsupported', member_count=0,
                              error='no index_weight rows', source_code=source_code)
                if not dry_run:
                    self._record_catalog(normalized_item, **result)
                return result

            names = local_stock_names()
            for row in rows:
                if not row.get('name') or row.get('name') == row.get('code'):
                    row['name'] = names.get(row.get('code'), row.get('code'))
            as_of = max(row['as_of_date'] for row in rows)
            fetched_at = datetime.now().isoformat(timespec='seconds')
            result = dict(normalized_item, status='ready', source_code=source_code,
                          as_of_date=as_of, member_count=len(rows), error=None)
            if dry_run:
                return result

            with self._lock, self._connection() as conn:
                conn.execute('BEGIN')
                conn.execute('DELETE FROM index_constituents WHERE panel_code=?', (panel,))
                conn.executemany(
                    """INSERT INTO index_constituents
                       (panel_code,as_of_date,constituent_code,constituent_name,
                        weight,source,fetched_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    [
                        (panel, row['as_of_date'], row['code'], row['name'],
                         row.get('weight'), row.get('source', 'tushare.index_weight'),
                         fetched_at)
                        for row in rows
                    ],
                )
                now = datetime.now().isoformat(timespec='seconds')
                conn.execute(
                    """INSERT INTO index_catalog
                       (panel_code,name,source_code,provider,status,error,as_of_date,
                        member_count,last_attempt_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(panel_code) DO UPDATE SET
                        name=excluded.name, source_code=excluded.source_code,
                        provider=excluded.provider, status=excluded.status,
                        error=excluded.error, as_of_date=excluded.as_of_date,
                        member_count=excluded.member_count,
                        last_attempt_at=excluded.last_attempt_at,
                        updated_at=excluded.updated_at""",
                    (panel, normalized_item.get('name') or panel, source_code,
                     'tushare', 'ready', None, as_of, len(rows), now, now),
                )
                conn.commit()
            return result
        except Exception as exc:
            result = dict(normalized_item, status='error', member_count=0,
                          error=str(exc), source_code=None)
            if not dry_run:
                self._record_catalog(normalized_item, **result)
            logger.warning('[index_constituents] %s failed: %s', panel, exc)
            return result
