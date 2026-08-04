"""Global index historical K-line loader.

This module owns the top-bar non-A-share index history path.  Spot quotes are
handled elsewhere; this file only deals with historical bars and persistence.
"""
import logging
import re
import datetime as _datetime
from urllib.parse import quote

import pandas as pd
import requests

from data.kline_resample import resample_ohlcv
from data.sqlite_repo import get_sqlite_repo


logger = logging.getLogger("global_index_kline")

RESAMPLE_PERIODS = {"weekly", "monthly", "quarterly", "yearly"}

TENCENT_SYMBOLS = {
    "HSI": "hkHSI",
    "HSTECH": "hkHSTECH",
}

YAHOO_SYMBOLS = {
    "HSI": "^HSI",
    "HSTECH": "^HSTECH",
    "^N225": "^N225",
    "^KS11": "^KS11",
    "^TWII": "^TWII",
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
}

EASTMONEY_SYMBOLS = {
    "800000": "47.800000",
    "HSI": "100.HSI",
    "HSTECH": "124.HSTECH",
    "^N225": "100.N225",
    "^KS11": "100.KS11",
    "^TWII": "100.TWII",
    "SPX": "100.SPX",
    "IXIC": "100.NDX",
    "DJI": "100.DJIA",
}

# These three legacy classification entries use an ``sz`` prefix even though
# Eastmoney treats the underlying index as a Shanghai-listed index. Keep the
# public code stable and canonicalize only at the market-data boundary.
A_SHARE_INDEX_COMPAT_ALIASES = {
    "sz000038": "sh000038",
    "sz000903": "sh000903",
    "sz000987": "sh000987",
}

INACTIVE_A_SHARE_INDEX_CODES = frozenset({
    "sh000803", "sh000829", "sh000843", "sh000844", "sh000920",
    "sh000921", "sh000938", "sh000950", "sh000951", "sh000953",
    "sh000957", "sh000962", "sh000963", "sh000968", "sh000981",
})

TUSHARE_INDEX_CODE_OVERRIDES = {
    'sh000853': '932000.CSI',
    'sh000985': '000985.CSI',
}


def _annotate(df: pd.DataFrame, source: str, fallback_chain) -> pd.DataFrame:
    """Attach truthful loader provenance to a returned frame."""
    result = df if df is not None else pd.DataFrame()
    attrs = dict(getattr(result, "attrs", {}) or {})
    attrs["source"] = source
    attrs["fallback_chain"] = list(fallback_chain or [])
    result.attrs = attrs
    return result


def canonical_a_share_index_code(code: str) -> str:
    key = str(code or "").strip().lower()
    return A_SHARE_INDEX_COMPAT_ALIASES.get(key, key)


def is_inactive_a_share_index(code: str) -> bool:
    return str(code or "").strip().lower() in INACTIVE_A_SHARE_INDEX_CODES


def is_standard_a_share_index_code(code: str) -> bool:
    return bool(re.match(r'^(sh|sz|bj)\d{6}$', str(code or '').strip().lower()))


def tushare_a_share_index_code(code: str) -> str:
    """Map the public panel code to Tushare index_daily ts_code."""
    candidates = tushare_a_share_index_candidates(code)
    return candidates[0] if candidates else ''


def tushare_a_share_index_candidates(code: str) -> list:
    """Return ordered Tushare candidates for one public A-share index code."""
    canonical = canonical_a_share_index_code(code)
    if canonical in TUSHARE_INDEX_CODE_OVERRIDES:
        return [TUSHARE_INDEX_CODE_OVERRIDES[canonical]]
    match = re.match(r'^(sh|sz|bj)(\d{6})$', canonical)
    if not match:
        return []
    market = {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}[match.group(1)]
    bare = match.group(2)
    candidates = [f'{bare}.{market}']
    if match.group(1) == 'sh' and bare.startswith('000'):
        candidates.append(f'{bare}.CSI')
    return candidates


def _get_tushare_index_pro():
    """Return the unique Tushare client from the data_loader factory."""
    try:
        from core.env_bootstrap import ensure_tushare_token
        if not ensure_tushare_token():
            return None
        from data_loader import get_tushare_pro
        return get_tushare_pro()
    except Exception as exc:
        logger.warning('[TushareIndex] init failed: %s', exc)
        return None


def _clean_a_share_index_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or 'date' not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out['date'] = pd.to_datetime(out['date'], errors='coerce')
    out = out.dropna(subset=['date'])
    out = out.loc[out['date'].dt.dayofweek < 5]
    for col in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors='coerce')
    out = out.dropna(subset=['open', 'high', 'low', 'close'])
    out = out.loc[(out[['open', 'high', 'low', 'close']] > 0).all(axis=1)]
    out['date'] = out['date'].dt.strftime('%Y-%m-%d')
    return _dedupe(out[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']])


def fetch_tushare_a_share_index_tail(
    code: str, start_date: str = '', end_date: str = ''
) -> pd.DataFrame:
    """Fetch an authoritative recent A-share index daily tail."""
    if not is_standard_a_share_index_code(code) or is_inactive_a_share_index(code):
        return pd.DataFrame()
    ts_codes = tushare_a_share_index_candidates(code)
    if not ts_codes:
        return pd.DataFrame()
    end = str(end_date or _datetime.date.today().strftime('%Y%m%d')).replace('-', '')
    start = str(start_date or (
        _datetime.date.today() - _datetime.timedelta(days=120)
    ).strftime('%Y%m%d')).replace('-', '')
    try:
        pro = _get_tushare_index_pro()
        if pro is None:
            return pd.DataFrame()
        for offset, ts_code in enumerate(ts_codes):
            try:
                raw = pro.index_daily(
                    ts_code=ts_code,
                    start_date=start,
                    end_date=end,
                )
                if raw is None or raw.empty:
                    logger.info(
                        '[TushareIndex] %s candidate %s empty%s',
                        code, ts_code,
                        '; trying next candidate' if offset + 1 < len(ts_codes) else '',
                    )
                    continue
                rows = []
                for _, row in raw.iterrows():
                    rows.append({
                        'date': row.get('trade_date') or row.get('date'),
                        'open': row.get('open'),
                        'high': row.get('high'),
                        'low': row.get('low'),
                        'close': row.get('close'),
                        'volume': row.get('vol', row.get('volume', 0)),
                        # Tushare index_daily amount is published in thousand yuan.
                        'amount': pd.to_numeric(row.get('amount'), errors='coerce') * 1000,
                    })
                cleaned = _clean_a_share_index_frame(pd.DataFrame(rows))
                if not cleaned.empty:
                    return cleaned
                logger.info(
                    '[TushareIndex] %s candidate %s had no valid rows%s',
                    code, ts_code,
                    '; trying next candidate' if offset + 1 < len(ts_codes) else '',
                )
            except Exception as exc:
                logger.warning(
                    '[TushareIndex] %s candidate %s failed: %s%s',
                    code, ts_code, exc,
                    '; trying next candidate' if offset + 1 < len(ts_codes) else '',
                )
    except Exception as exc:
        logger.warning('[TushareIndex] %s init failed: %s', code, exc)
        return pd.DataFrame()
    return pd.DataFrame()


def load_a_share_index_kline(code: str, period: str = 'daily') -> pd.DataFrame:
    """Load A-share index history with a Tushare-authoritative recent range."""
    code = str(code or '').strip()
    if not is_standard_a_share_index_code(code) or is_inactive_a_share_index(code):
        return _annotate(pd.DataFrame(), 'unavailable', [])
    repo = get_sqlite_repo()
    cached = _clean_a_share_index_frame(repo.read_kline(code, 'daily'))
    base_chain = ['sqlite']
    if period in RESAMPLE_PERIODS:
        daily = load_a_share_index_kline(code, 'daily')
        if daily is None or daily.empty:
            return _annotate(
                pd.DataFrame(), 'unavailable',
                (getattr(daily, 'attrs', {}) or {}).get(
                    'fallback_chain', base_chain + ['tushare_index_daily']
                ),
            )
        out = resample_kline(daily, period)
        attrs = getattr(daily, 'attrs', {}) or {}
        return _annotate(
            out if out is not None else pd.DataFrame(),
            attrs.get('source') or 'unavailable',
            attrs.get('fallback_chain') or base_chain,
        )

    cached_amount = pd.to_numeric(
        cached.get('amount', pd.Series(dtype=float)), errors='coerce'
    ).fillna(0)
    needs_amount_backfill = cached.empty or not cached_amount.gt(0).any()
    amount_start = (
        str(cached['date'].min()).replace('-', '')
        if needs_amount_backfill and not cached.empty
        else ('20000101' if needs_amount_backfill else '')
    )
    remote = fetch_tushare_a_share_index_tail(code, start_date=amount_start)
    if remote is None or remote.empty:
        return _annotate(
            cached, 'sqlite' if not cached.empty else 'unavailable',
            base_chain + ['tushare_index_daily'],
        )

    remote = _clean_a_share_index_frame(remote)
    remote_start = remote['date'].min()
    remote_end = remote['date'].max()
    outside = cached.loc[
        (cached['date'] < remote_start) | (cached['date'] > remote_end)
    ] if cached is not None and not cached.empty else pd.DataFrame()
    merged = _dedupe(pd.concat([outside, remote], ignore_index=True))
    # Replace, rather than date-merge, so malformed same-date rows and weekend
    # or 10,000-point contamination inside the authoritative range disappear.
    try:
        repo.replace_kline_period(code, 'daily', merged)
    except Exception as exc:
        logger.warning('[TushareIndex] replace %s failed: %s', code, exc)
    return _annotate(
        merged, 'tushare_index_daily',
        base_chain + ['tushare_index_daily'] if not cached.empty
        else ['tushare_index_daily'],
    )


def eastmoney_index_secid(code: str) -> str:
    """Return Eastmoney secid for global, special, or A-share indices."""
    key = str(code or '').strip()
    if key in EASTMONEY_SYMBOLS:
        return EASTMONEY_SYMBOLS[key]
    key = canonical_a_share_index_code(key)
    match = re.match(r'^(sh|sz|bj)(\d{6})$', key.lower())
    if not match:
        return ''
    return f"{'1' if match.group(1) == 'sh' else '0'}.{match.group(2)}"

EASTMONEY_HISTORY_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
)
EASTMONEY_HISTORY_FALLBACK_URL = (
    "https://push2test.eastmoney.com/api/qt/stock/kline/get"
)

SINA_GLOBAL_SYMBOLS = {
    "^N225": "NKY",
    "^KS11": "KOSPI",
    "^TWII": "TWJQ",
}

SINA_US_SYMBOLS = {
    "SPX": ".INX",
    "IXIC": ".IXIC",
    "DJI": ".DJI",
}

# Exchange-local trading-date ownership for timestamp-based feeds.  US index
# sessions run overnight in Asia/Shanghai, so attributing an Eastmoney spot
# or Yahoo timestamp to Asia/Shanghai would stamp a Friday US bar as
# Saturday.  History feeds that already publish an exchange trading date
# (Eastmoney klines, Tencent, Sina) pass their date strings through
# unchanged and do not need this mapping.
GLOBAL_INDEX_MARKET_TIMEZONES = {
    "HSI": "Asia/Hong_Kong",
    "HSTECH": "Asia/Hong_Kong",
    "^N225": "Asia/Tokyo",
    "^KS11": "Asia/Seoul",
    "^TWII": "Asia/Taipei",
    "SPX": "America/New_York",
    "IXIC": "America/New_York",
    "DJI": "America/New_York",
    "800000": "Asia/Shanghai",
}


def market_timezone_for_index(code: str) -> str:
    """Return the IANA timezone that owns a global index's trading date."""
    return GLOBAL_INDEX_MARKET_TIMEZONES.get(
        str(code or "").strip(), "Asia/Shanghai"
    )


def _frame_last_date(df: pd.DataFrame) -> str:
    """Return a normalized last date for a history candidate.

    A source can return rows in either ascending or descending order and a
    cache can contain a malformed date left by an older writer.  Comparing
    the normalized maximum rather than relying on row order is what lets the
    incremental loader decide whether an apparently non-empty Eastmoney tail
    is actually behind the local cache.
    """
    if df is None or df.empty or "date" not in df.columns:
        return ""
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().all():
        return ""
    return dates.max().strftime("%Y-%m-%d")


def _global_history_sources(code: str):
    """Return applicable fallback history providers in stable order.

    Do not put providers that cannot serve ``code`` in the chain.  The chain
    is user-visible observability, so recording an inapplicable source would
    falsely suggest that it was queried.
    """
    normalized = str(code or "").strip()
    sources = []
    if normalized in TENCENT_SYMBOLS:
        sources.append(("tencent_history", fetch_tencent_global_kline))
    if normalized in SINA_GLOBAL_SYMBOLS or normalized in SINA_US_SYMBOLS:
        sources.append(("sina_history", fetch_sina_global_kline))
    return sources


def _normalize_history_date(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _eastmoney_history_is_stale(
    cached: pd.DataFrame,
    eastmoney: pd.DataFrame,
    target_date=None,
) -> bool:
    """Whether an Eastmoney tail must be checked against backup histories.

    A non-empty response is not enough to prove freshness.  When its last
    date is at or before the local tail, probe independent history feeds so a
    newer tail can repair the cache.  If there is no local tail, the existing
    cold-start behavior remains unchanged: Eastmoney is trusted while it
    returns rows and other sources are only used when it is empty.
    """
    if eastmoney is None or eastmoney.empty:
        return True
    eastmoney_last = _frame_last_date(eastmoney)
    target = _normalize_history_date(target_date)
    if target and (not eastmoney_last or eastmoney_last < target):
        return True
    if cached is None or cached.empty:
        return False
    cached_last = _frame_last_date(cached)
    if not eastmoney_last:
        return True
    if not cached_last:
        return True
    return eastmoney_last <= cached_last


def _history_candidate_sort_key(item):
    """Sort history candidates from oldest to newest for date-wise merging."""
    _, frame = item
    return _frame_last_date(frame)


def load_global_index_kline(
    code: str,
    period: str = "daily",
    *,
    target_date=None,
) -> pd.DataFrame:
    """Load and incrementally refresh global-index bars."""
    code = str(code or "").strip()
    period = str(period or "daily").strip()
    if not code:
        return _annotate(pd.DataFrame(), "unavailable", [])
    if is_standard_a_share_index_code(code):
        return load_a_share_index_kline(code, period)

    repo = get_sqlite_repo()

    if period in RESAMPLE_PERIODS:
        daily = load_global_index_kline(code, "daily", target_date=target_date)
        if daily is None or daily.empty:
            attrs = getattr(daily, "attrs", {}) or {}
            return _annotate(
                pd.DataFrame(), "unavailable",
                attrs.get("fallback_chain") or ["sqlite"],
            )

        out = resample_kline(daily, period)
        if not out.empty:
            repo.save_kline(code, period, out)
        attrs = getattr(daily, "attrs", {}) or {}
        return _annotate(
            out, attrs.get("source") or "unavailable",
            attrs.get("fallback_chain") or ["sqlite"],
        )

    cached = repo.read_kline(code, "daily")
    cached = _dedupe(cached) if cached is not None else pd.DataFrame()
    fallback_chain = ["sqlite"]

    # A cache hit is not proof that the tail is current. Always request a
    # bounded Eastmoney tail and merge by date; this also overwrites malformed
    # bars previously synthesized from a spot-only response.
    limit = 180 if not cached.empty else 10000
    fallback_chain.append("eastmoney_history")
    eastmoney = fetch_eastmoney_global_kline(code, limit=limit)
    history_candidates = []
    if eastmoney is not None and not eastmoney.empty:
        history_candidates.append(("eastmoney_history", eastmoney))

    # A non-empty Eastmoney response can still be behind the local tail (for
    # example, the endpoint may be serving Friday while Sina already has
    # Monday).  In that case query applicable independent history sources and
    # merge the newest tail by date.  Empty Eastmoney responses retain the
    # original fallback behavior.
    if _eastmoney_history_is_stale(cached, eastmoney, target_date=target_date):
        for source_name, fetcher in _global_history_sources(code):
            fallback_chain.append(source_name)
            try:
                candidate = fetcher(code)
            except Exception as exc:
                logger.warning(
                    "[GlobalKline] %s %s failed: %s",
                    source_name,
                    code,
                    exc,
                )
                candidate = pd.DataFrame()
            if candidate is not None and not candidate.empty:
                history_candidates.append((source_name, candidate))

        # Yahoo remains a cold-start fallback, or a last resort after all
        # applicable independent sources failed to provide a newer tail.  Do
        # not put it on the chain for a healthy incremental Eastmoney hit.
        latest_history_date = max(
            (_frame_last_date(frame) for _, frame in history_candidates),
            default="",
        )
        eastmoney_last = _frame_last_date(eastmoney)
        target = _normalize_history_date(target_date)
        yahoo_supported = str(code or "").strip() in YAHOO_SYMBOLS
        if yahoo_supported and (
            not history_candidates
            or (target and latest_history_date < target)
            or not eastmoney_last
            or (not target and latest_history_date <= eastmoney_last)
        ):
            fallback_chain.append("yahoo_history")
            try:
                yahoo = fetch_yahoo_global_kline(code)
            except Exception as exc:
                logger.warning("[GlobalKline] yahoo_history %s failed: %s", code, exc)
                yahoo = pd.DataFrame()
            if yahoo is not None and not yahoo.empty:
                history_candidates.append(("yahoo_history", yahoo))

    fallback_chain.append("eastmoney_spot")
    spot_bar = fetch_eastmoney_spot_bar(code)
    spot_source = (
        "eastmoney_spot" if spot_bar is not None and not spot_bar.empty else ""
    )
    remote_frames = [frame for _, frame in history_candidates
                     if frame is not None and not frame.empty]
    remote = (
        _dedupe(pd.concat(remote_frames, ignore_index=True))
        if remote_frames else pd.DataFrame()
    )
    replace_incompatible_cache = (
        code == "800000" and _all_a_cache_is_incompatible(cached, remote)
    )
    if replace_incompatible_cache:
        logger.warning(
            "[GlobalKline] replacing incompatible 800000 history cache"
        )
        cached = pd.DataFrame()

    history_parts = [frame for frame in (cached, remote)
                     if frame is not None and not frame.empty]
    if not history_parts and (spot_bar is None or spot_bar.empty):
        return _annotate(pd.DataFrame(), "unavailable", fallback_chain)

    # Sort history candidates by their actual last date before merging.  This
    # makes a newer Sina/Tencent tail win same-date collisions without
    # discarding older Eastmoney history.  Spot is intentionally kept out of
    # this persisted frame: it can be an incomplete live-session bar.
    history_sources = list(history_candidates)
    if cached is not None and not cached.empty:
        history_sources.append(("sqlite", cached))
    ordered_history = sorted(history_sources, key=_history_candidate_sort_key)
    history_frames = [
        frame for _, frame in ordered_history
        if frame is not None and not frame.empty
    ]
    history_merged = (
        _dedupe(pd.concat(history_frames, ignore_index=True))
        if history_frames else pd.DataFrame()
    )
    display_parts = [frame for frame in (history_merged, spot_bar)
                     if frame is not None and not frame.empty]
    merged = _dedupe(pd.concat(display_parts, ignore_index=True))

    if remote is not None and not remote.empty:
        if replace_incompatible_cache:
            repo.replace_kline_period(code, "daily", history_merged)
        else:
            repo.save_kline(code, "daily", history_merged)

    # Report the provider owning the freshest historical tail.  Equal-date
    # collisions intentionally prefer the later candidate (independent backup
    # providers come after Eastmoney in ``history_candidates``).  A spot-only
    # response is returned for display but is never persisted or mislabeled as
    # a historical source.
    source_candidates = []
    if cached is not None and not cached.empty:
        source_candidates.append(("sqlite", cached))
    source_candidates.extend(history_candidates)
    freshest_source = ""
    freshest_date = ""
    for candidate_source, frame in source_candidates:
        candidate_date = _frame_last_date(frame)
        if candidate_date and candidate_date >= freshest_date:
            freshest_source = candidate_source
            freshest_date = candidate_date
    spot_date = _frame_last_date(spot_bar)
    if spot_source and spot_date and spot_date > freshest_date:
        source = spot_source
    else:
        source = freshest_source or spot_source or "unavailable"
    return _annotate(merged, source, fallback_chain)


def resample_kline(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Resample daily OHLCV bars into week/month/quarter/year periods."""
    out = _dedupe(resample_ohlcv(df, period))
    out.attrs = dict(getattr(df, "attrs", {}) or {})
    return out


def fetch_eastmoney_global_kline(code: str, limit: int = 180) -> pd.DataFrame:
    """Fetch daily bars from Eastmoney's official global-index chart feed."""
    if is_standard_a_share_index_code(code):
        # push2test has mixed/contaminated A-share history. Standard A-share
        # indices use Tushare's authoritative tail instead.
        return pd.DataFrame()
    secid = eastmoney_index_secid(code)
    if not secid:
        return pd.DataFrame()
    urls = [EASTMONEY_HISTORY_URL]
    key = str(code or "").strip().lower()
    if key == "800000":
        urls.append(EASTMONEY_HISTORY_FALLBACK_URL)

    for url in urls:
        try:
            response = requests.get(
                url,
                params={
                    "secid": secid,
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                    "klt": "101",
                    "fqt": "0",
                    "lmt": str(max(1, int(limit))),
                    "end": "20500101",
                    "iscca": "1",
                    "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
                    "fields2": (
                        "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                    ),
                },
                # Keep requests' default trust_env behavior so configured
                # ALL_PROXY/HTTP(S)_PROXY can serve push2test reliably.
                timeout=6,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://quote.eastmoney.com/unify/r/{secid}",
                },
            )
            response.raise_for_status()
            rows = ((response.json() or {}).get("data") or {}).get("klines") or []
            out = []
            for raw in rows:
                parts = str(raw or "").split(",")
                if len(parts) < 6:
                    continue
                out.append({
                    "date": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "volume": parts[5],
                    "amount": parts[6] if len(parts) > 6 else 0,
                })
            cleaned = _clean_http_kline(pd.DataFrame(out))
            if str(code or "").strip() == "800000":
                cleaned = _weekday_only(cleaned)
            if not cleaned.empty:
                return cleaned
        except Exception as exc:
            logger.warning(
                "[GlobalKline] Eastmoney history %s via %s failed: %s",
                code,
                url,
                exc,
            )
    return pd.DataFrame()


def fetch_eastmoney_spot_bar(code: str) -> pd.DataFrame:
    """Build the current trading day's OHLC bar from Eastmoney spot fields."""
    secid = eastmoney_index_secid(code)
    if not secid:
        return pd.DataFrame()
    try:
        session = requests.Session()
        # Keep requests' default trust_env behavior so the configured
        # 7688 proxy can serve overseas/global Eastmoney spot quotes.
        response = session.get(
            "https://push2delay.eastmoney.com/api/qt/stock/get",
            params={
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86",
            },
            timeout=4,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            },
        )
        row = (response.json() or {}).get("data")
        if not isinstance(row, dict):
            return pd.DataFrame()

        def scaled(field):
            value = pd.to_numeric(row.get(field), errors="coerce")
            return float(value) / 100 if pd.notna(value) else None

        close = scaled("f43")
        opened = scaled("f46")
        high = scaled("f44")
        low = scaled("f45")
        timestamp = pd.to_numeric(row.get("f86"), errors="coerce")
        if not close or not opened or not high or not low or pd.isna(timestamp):
            return pd.DataFrame()
        trade_date = (
            pd.to_datetime(int(timestamp), unit="s", utc=True)
            .tz_convert(market_timezone_for_index(code))
            .strftime("%Y-%m-%d")
        )
        return _clean_http_kline(pd.DataFrame([{
            "date": trade_date,
            "open": opened,
            "close": close,
            "high": high,
            "low": low,
            "volume": pd.to_numeric(row.get("f47"), errors="coerce") or 0,
            "amount": pd.to_numeric(row.get("f48"), errors="coerce") or 0,
        }]))
    except Exception as exc:
        logger.warning("[GlobalKline] Eastmoney spot %s failed: %s", code, exc)
        return pd.DataFrame()


def fetch_sina_global_kline(code: str) -> pd.DataFrame:
    """Fetch APAC/US daily history from Sina's independent history feeds."""
    normalized = str(code or "").strip()
    symbol = SINA_GLOBAL_SYMBOLS.get(normalized)
    if symbol:
        try:
            session = requests.Session()
            # Keep requests' default trust_env so the 7688 proxy applies to
            # this overseas history feed.
            response = session.get(
                "https://gi.finance.sina.com.cn/hq/daily",
                params={"symbol": symbol, "num": "1000"},
                timeout=6,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            rows = (((response.json() or {}).get("result") or {}).get("data")
                    or [])
            out = [{
                "date": row.get("d"),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v") or 0,
            } for row in rows if isinstance(row, dict)]
            return _clean_http_kline(pd.DataFrame(out))
        except Exception as exc:
            logger.warning("[GlobalKline] Sina global %s failed: %s", code, exc)
            return pd.DataFrame()

    us_symbol = SINA_US_SYMBOLS.get(normalized)
    if us_symbol:
        try:
            # AkShare owns Sina's compressed US-history decoder and is a
            # pinned runtime dependency of board-app.
            import akshare as ak

            frame = ak.index_us_stock_sina(symbol=us_symbol)
            if frame is None or frame.empty:
                return pd.DataFrame()
            out = frame.copy()
            out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            return _clean_http_kline(out)
        except Exception as exc:
            logger.warning("[GlobalKline] Sina US %s failed: %s", code, exc)
            return pd.DataFrame()
    return pd.DataFrame()


def fetch_tencent_global_kline(code: str) -> pd.DataFrame:
    """Fetch Hong Kong index daily bars from Tencent."""
    symbol = TENCENT_SYMBOLS.get(str(code or "").strip())
    if not symbol:
        return pd.DataFrame()
    try:
        response = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{symbol},day,,,800,qfq"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        node = ((response.json().get("data") or {}).get(symbol) or {})
        rows = node.get("qfqday") or node.get("day") or []
        out = []
        for row in rows:
            if len(row) < 6:
                continue
            out.append({
                "date": row[0],
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
            })
        return _clean_http_kline(pd.DataFrame(out))
    except Exception as exc:
        logger.warning("[GlobalKline] Tencent %s failed: %s", code, exc)
        return pd.DataFrame()


def fetch_yahoo_global_kline(code: str) -> pd.DataFrame:
    """Fetch global index daily bars from Yahoo chart API."""
    symbol = YAHOO_SYMBOLS.get(str(code or "").strip())
    if not symbol:
        return pd.DataFrame()
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
            params={
                "range": "10y",
                "interval": "1d",
                "events": "history",
                "includeAdjustedClose": "true",
            },
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [])
        if not result:
            return pd.DataFrame()

        item = result[0]
        timestamps = item.get("timestamp") or []
        quote_data = (((item.get("indicators") or {}).get("quote") or [{}])[0]) or {}
        rows = []
        for index, ts in enumerate(timestamps):
            close = _list_get(quote_data.get("close"), index)
            if close is None:
                continue
            rows.append({
                "date": (
                    pd.to_datetime(int(ts), unit="s", utc=True)
                    .tz_convert(market_timezone_for_index(code))
                    .strftime("%Y-%m-%d")
                ),
                "open": _list_get(quote_data.get("open"), index),
                "high": _list_get(quote_data.get("high"), index),
                "low": _list_get(quote_data.get("low"), index),
                "close": close,
                "volume": _list_get(quote_data.get("volume"), index) or 0,
            })
        return _clean_http_kline(pd.DataFrame(rows))
    except Exception as exc:
        logger.warning("[GlobalKline] Yahoo %s failed: %s", code, exc)
        return pd.DataFrame()


def _list_get(values, index):
    try:
        return values[index]
    except Exception:
        return None


def _weekday_only(df: pd.DataFrame) -> pd.DataFrame:
    """Reject impossible weekend bars from the A-share fallback feed."""
    if df is None or df.empty:
        return pd.DataFrame()
    dates = pd.to_datetime(df["date"], errors="coerce")
    return df.loc[dates.dt.dayofweek < 5].reset_index(drop=True)


def _all_a_cache_is_incompatible(
    cached: pd.DataFrame,
    remote: pd.DataFrame,
) -> bool:
    """Detect an old 800000 cache populated from a different index."""
    if cached is None or cached.empty or remote is None or remote.empty:
        return False

    left = cached[["date", "close"]].copy()
    right = remote[["date", "close"]].copy()
    left["close"] = pd.to_numeric(left["close"], errors="coerce")
    right["close"] = pd.to_numeric(right["close"], errors="coerce")
    overlap = left.merge(right, on="date", suffixes=("_cache", "_remote"))
    overlap = overlap.dropna(subset=["close_cache", "close_remote"])

    if len(overlap) >= 2:
        ratio = (overlap["close_cache"] / overlap["close_remote"]).median()
        return bool(ratio < 0.8 or ratio > 1.25)

    cached_dates = pd.to_datetime(cached["date"], errors="coerce")
    remote_dates = pd.to_datetime(remote["date"], errors="coerce")
    if cached_dates.notna().any() and remote_dates.notna().any():
        gap = abs((cached_dates.max() - remote_dates.min()).days)
        if gap <= 31:
            cache_tail = pd.to_numeric(
                cached.sort_values("date").tail(5)["close"], errors="coerce"
            ).median()
            remote_head = pd.to_numeric(
                remote.sort_values("date").head(5)["close"], errors="coerce"
            ).median()
            if pd.notna(cache_tail) and pd.notna(remote_head) and remote_head:
                ratio = cache_tail / remote_head
                return bool(ratio < 0.8 or ratio > 1.25)
    return False


def _clean_http_kline(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "date" not in out.columns:
        return pd.DataFrame()
    out = out.dropna(subset=["date", "close"])
    if out.empty:
        return pd.DataFrame()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out.get(col), errors="coerce")
    out["open"] = out["open"].fillna(out["close"])
    out["high"] = out["high"].fillna(out["close"])
    out["low"] = out["low"].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0)
    out["amount"] = out["amount"].fillna(0)
    return _dedupe(out[["date", "open", "high", "low", "close", "volume", "amount"]])


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return df if df is not None else pd.DataFrame()
    out = df.copy()
    out["_seq"] = range(len(out))
    out["_sort"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["_sort"]).sort_values(
        ["_sort", "_seq"], kind="mergesort"
    )
    out = out.drop_duplicates(subset=["date"], keep="last")
    out["date"] = out["_sort"].dt.strftime("%Y-%m-%d")
    return out.drop(columns=["_sort", "_seq"]).reset_index(drop=True)
