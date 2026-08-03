"""Exchange trading-calendar adapter used by market-session decisions.

The optional :mod:`pandas_market_calendars` dependency is the authoritative
source when it is available.  The fallback deliberately knows only weekdays
and the regular session windows; it must therefore advertise its lower
certainty to callers instead of pretending that exchange holidays are known.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as wall_time, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple
import warnings
from zoneinfo import ZoneInfo

try:  # Keep importing the board app possible in a minimal installation.
    import pandas_market_calendars as mcal
except ImportError:  # pragma: no cover - exercised through the fallback tests.
    mcal = None


MARKET_A_SHARE = "a_share"
MARKET_HONG_KONG = "hong_kong"
MARKET_JAPAN = "japan"
MARKET_SOUTH_KOREA = "south_korea"
MARKET_TAIWAN = "taiwan"
MARKET_US = "us"
MARKET_STATIC = "static"

CALENDAR_SOURCE = "pandas_market_calendars"
FALLBACK_SOURCE = "weekday_fallback"
STATIC_SOURCE = "static"
EXACT_CERTAINTY = "high"
FALLBACK_CERTAINTY = "low"


@dataclass(frozen=True)
class ExchangeSpec:
    """Static facts needed when a calendar backend is unavailable."""

    calendar_name: str
    timezone: str
    fallback_sessions: Tuple[Tuple[wall_time, wall_time, str], ...]
    break_phase: str = "lunch"


_MARKET_SPECS: Dict[str, ExchangeSpec] = {
    MARKET_A_SHARE: ExchangeSpec(
        calendar_name="SSE",
        timezone="Asia/Shanghai",
        fallback_sessions=(
            (wall_time(9, 30), wall_time(11, 30), "live_morning"),
            (wall_time(13, 0), wall_time(15, 0), "live_afternoon"),
        ),
    ),
    MARKET_HONG_KONG: ExchangeSpec(
        calendar_name="XHKG",
        timezone="Asia/Hong_Kong",
        fallback_sessions=(
            (wall_time(9, 30), wall_time(12, 0), "live_morning"),
            (wall_time(13, 0), wall_time(16, 0), "live_afternoon"),
        ),
    ),
    MARKET_JAPAN: ExchangeSpec(
        calendar_name="JPX",
        timezone="Asia/Tokyo",
        fallback_sessions=(
            (wall_time(9, 0), wall_time(11, 30), "live_morning"),
            (wall_time(12, 30), wall_time(15, 30), "live_afternoon"),
        ),
    ),
    MARKET_SOUTH_KOREA: ExchangeSpec(
        calendar_name="XKRX",
        timezone="Asia/Seoul",
        fallback_sessions=((wall_time(9, 0), wall_time(15, 30), "live"),),
    ),
    MARKET_TAIWAN: ExchangeSpec(
        calendar_name="XTAI",
        timezone="Asia/Taipei",
        fallback_sessions=((wall_time(9, 0), wall_time(13, 30), "live"),),
    ),
    MARKET_US: ExchangeSpec(
        calendar_name="XNYS",
        timezone="America/New_York",
        fallback_sessions=((wall_time(9, 30), wall_time(16, 0), "live"),),
    ),
}

SUPPORTED_MARKETS = tuple(_MARKET_SPECS)

_MARKET_ALIASES = {
    **{market: market for market in _MARKET_SPECS},
    "sse": MARKET_A_SHARE,
    "shanghai": MARKET_A_SHARE,
    "a-share": MARKET_A_SHARE,
    "xhkg": MARKET_HONG_KONG,
    "hk": MARKET_HONG_KONG,
    "hong-kong": MARKET_HONG_KONG,
    "jpx": MARKET_JAPAN,
    "japan": MARKET_JAPAN,
    "xkrx": MARKET_SOUTH_KOREA,
    "krx": MARKET_SOUTH_KOREA,
    "korea": MARKET_SOUTH_KOREA,
    "south-korea": MARKET_SOUTH_KOREA,
    "xtai": MARKET_TAIWAN,
    "taiwan": MARKET_TAIWAN,
    "xnys": MARKET_US,
    "nyse": MARKET_US,
    "us": MARKET_US,
    "usa": MARKET_US,
    "static": MARKET_STATIC,
}


@dataclass(frozen=True)
class TradingSession:
    """One exchange-local session, including provenance metadata."""

    market: str
    session_date: date
    is_trading_day: bool
    open_at: Optional[datetime]
    close_at: Optional[datetime]
    break_start_at: Optional[datetime]
    break_end_at: Optional[datetime]
    certainty: str
    source: str


def normalize_market(market: Any) -> str:
    """Return a canonical market name, or ``static`` for an unknown value."""

    raw = str(market or "").strip()
    return _MARKET_ALIASES.get(raw.lower(), MARKET_STATIC)


def _resolve_market_code(code_or_market: Any, data_type: Optional[str] = None) -> str:
    """Resolve either a canonical market or one of the navigation symbols."""

    canonical = normalize_market(code_or_market)
    if canonical != MARKET_STATIC:
        return canonical
    raw = str(code_or_market or "").strip()
    if not raw or raw.lower() == MARKET_STATIC:
        return MARKET_STATIC
    # Import lazily so market_session can import this module without a cycle.
    from services.market_session import classify_market

    return classify_market(raw, data_type)


def market_spec(market: Any) -> Optional[ExchangeSpec]:
    """Return the exchange specification for a market alias."""

    return _MARKET_SPECS.get(normalize_market(market))


def market_timezone(market: Any) -> Optional[str]:
    spec = market_spec(market)
    return spec.timezone if spec else None


def market_calendar_name(market: Any) -> Optional[str]:
    spec = market_spec(market)
    return spec.calendar_name if spec else None


def _backend_token() -> Tuple[int, Optional[str]]:
    """Make cache entries safe when tests replace the optional backend."""

    if mcal is None:
        return (0, None)
    return (id(mcal), getattr(mcal, "__version__", None))


@lru_cache(maxsize=len(_MARKET_SPECS))
def _get_calendar_cached(
    market: str, backend_token: Tuple[int, Optional[str]]
) -> Any:
    del backend_token  # The token is part of the cache key, not the operation.
    spec = _MARKET_SPECS.get(market)
    if spec is None or mcal is None:
        return None
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*break_start.*break_end.*discontinued.*",
                category=UserWarning,
            )
            return mcal.get_calendar(spec.calendar_name)
    except Exception:
        # A broken optional installation is no better than a missing one.
        return None


def get_calendar(market: Any) -> Any:
    """Return the cached PMC calendar, or ``None`` when fallback is active."""

    canonical = normalize_market(market)
    if canonical not in _MARKET_SPECS:
        return None
    return _get_calendar_cached(canonical, _backend_token())


@lru_cache(maxsize=512)
def _get_schedule_cached(
    market: str,
    start_date: date,
    end_date: date,
    backend_token: Tuple[int, Optional[str]],
) -> Any:
    del backend_token
    calendar = _get_calendar_cached(market, _backend_token())
    if calendar is None:
        return None
    try:
        return calendar.schedule(start_date.isoformat(), end_date.isoformat())
    except Exception:
        # Keep the service usable if an optional calendar has a version/runtime
        # problem.  The caller will label the resulting weekday decision low.
        return None


def _coerce_session_date(market: str, value: Any) -> date:
    spec = _MARKET_SPECS.get(market)
    timezone_name = spec.timezone if spec else "UTC"
    local_tz = ZoneInfo(timezone_name)

    if value is None:
        return datetime.now(timezone.utc).astimezone(local_tz).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(local_tz).date()
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        return _coerce_session_date(market, value.to_pydatetime())
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise TypeError("session date must be a date, datetime, ISO date, or None")


def _coerce_local_as_of(market: str, value: Any) -> Optional[datetime]:
    """Return an exchange-local instant when ``value`` carries a clock.

    A plain date deliberately returns ``None``: date-based callers ask for the
    latest session *on or before that date*. Datetime callers ask what data
    should already exist at that instant, so pre-open still means the previous
    session.
    """

    local_tz = ZoneInfo(_MARKET_SPECS[market].timezone)
    if value is None:
        return datetime.now(timezone.utc).astimezone(local_tz)
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=local_tz)
        return value.astimezone(local_tz)
    if isinstance(value, str) and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=local_tz)
        return parsed.astimezone(local_tz)
    return None


def get_schedule(
    market: Any, start_date: Any, end_date: Any = None
) -> Any:
    """Return a cached PMC schedule for an exchange-local date range.

    ``None`` means the optional calendar backend is unavailable or failed.  An
    empty DataFrame is a meaningful exact answer: it denotes no sessions in
    that range.
    """

    canonical = normalize_market(market)
    if canonical not in _MARKET_SPECS:
        return None
    start = _coerce_session_date(canonical, start_date)
    end = _coerce_session_date(canonical, end_date if end_date is not None else start)
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")
    return _get_schedule_cached(canonical, start, end, _backend_token())


def _is_missing(value: Any) -> bool:
    if value is None or value.__class__.__name__ == "NaTType":
        return True
    try:
        result = value != value
        return isinstance(result, bool) and result
    except Exception:
        return False


def _as_local_datetime(value: Any, timezone_name: str) -> Optional[datetime]:
    if _is_missing(value):
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        # PMC schedule timestamps are UTC-aware.  Treat a naive timestamp as
        # UTC rather than silently assigning the exchange-local timezone.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name))


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _index_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        try:
            result = value.date()
            if isinstance(result, date):
                return result
        except (TypeError, ValueError):
            pass
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _schedule_row(schedule: Any, session_date: date) -> Any:
    if schedule is None or bool(getattr(schedule, "empty", False)):
        return None
    try:
        iterator = schedule.iterrows()
    except AttributeError:
        iterator = (
            (key, value)
            for key, value in (schedule.items() if isinstance(schedule, Mapping) else ())
        )
    for index, row in iterator:
        if _index_date(index) == session_date:
            return row
    return None


def _fallback_session(market: str, session_date: date) -> TradingSession:
    spec = _MARKET_SPECS[market]
    local_tz = ZoneInfo(spec.timezone)
    if session_date.weekday() >= 5:
        return TradingSession(
            market=market,
            session_date=session_date,
            is_trading_day=False,
            open_at=None,
            close_at=None,
            break_start_at=None,
            break_end_at=None,
            certainty=FALLBACK_CERTAINTY,
            source=FALLBACK_SOURCE,
        )

    periods = spec.fallback_sessions
    first_start, _, _ = periods[0]
    _, last_end, _ = periods[-1]
    break_start = break_end = None
    if len(periods) > 1:
        break_start = datetime.combine(session_date, periods[0][1], local_tz)
        break_end = datetime.combine(session_date, periods[1][0], local_tz)
    return TradingSession(
        market=market,
        session_date=session_date,
        is_trading_day=True,
        open_at=datetime.combine(session_date, first_start, local_tz),
        close_at=datetime.combine(session_date, last_end, local_tz),
        break_start_at=break_start,
        break_end_at=break_end,
        certainty=FALLBACK_CERTAINTY,
        source=FALLBACK_SOURCE,
    )


def _calendar_session(market: str, session_date: date) -> Optional[TradingSession]:
    """Load one exact session, returning ``None`` when PMC cannot be used."""

    calendar = get_calendar(market)
    if calendar is None:
        return None
    schedule = get_schedule(market, session_date, session_date)
    if schedule is None:
        return None
    row = _schedule_row(schedule, session_date)
    if row is None:
        return TradingSession(
            market=market,
            session_date=session_date,
            is_trading_day=False,
            open_at=None,
            close_at=None,
            break_start_at=None,
            break_end_at=None,
            certainty=EXACT_CERTAINTY,
            source=CALENDAR_SOURCE,
        )

    timezone_name = _MARKET_SPECS[market].timezone
    break_start = _as_local_datetime(_row_value(row, "break_start"), timezone_name)
    break_end = _as_local_datetime(_row_value(row, "break_end"), timezone_name)
    discontinued = getattr(calendar, "discontinued_market_times", {}) or {}
    for key, value in (("break_start", break_start), ("break_end", break_end)):
        discontinued_at = _index_date(discontinued.get(key))
        if discontinued_at is not None and session_date >= discontinued_at:
            if key == "break_start":
                break_start = None
            else:
                break_end = None
    return TradingSession(
        market=market,
        session_date=session_date,
        is_trading_day=True,
        open_at=_as_local_datetime(_row_value(row, "market_open"), timezone_name),
        close_at=_as_local_datetime(_row_value(row, "market_close"), timezone_name),
        break_start_at=break_start,
        break_end_at=break_end,
        certainty=EXACT_CERTAINTY,
        source=CALENDAR_SOURCE,
    )


def session_for_date(
    market: Any, session_date: Any = None, data_type: Optional[str] = None
) -> TradingSession:
    """Return exact or fallback session data for one local exchange date."""

    canonical = _resolve_market_code(market, data_type)
    if canonical not in _MARKET_SPECS:
        resolved_date = _coerce_session_date(canonical, session_date)
        return TradingSession(
            market=MARKET_STATIC,
            session_date=resolved_date,
            is_trading_day=False,
            open_at=None,
            close_at=None,
            break_start_at=None,
            break_end_at=None,
            certainty=EXACT_CERTAINTY,
            source=STATIC_SOURCE,
        )

    resolved_date = _coerce_session_date(canonical, session_date)
    exact = _calendar_session(canonical, resolved_date)
    return exact if exact is not None else _fallback_session(canonical, resolved_date)


def _trading_day_details(session: TradingSession) -> Dict[str, Any]:
    return {
        "market": session.market,
        "session_date": session.session_date,
        "is_trading_day": session.is_trading_day,
        "certainty": session.certainty,
        "source": session.source,
    }


def is_trading_day(
    code_or_market: Any,
    session_date: Any = None,
    data_type: Optional[str] = None,
    *,
    return_metadata: bool = False,
    as_of: Any = None,
) -> Any:
    """Test whether ``session_date`` is open for the requested exchange.

    The default remains a plain ``bool`` for predicate callers.  Set
    ``return_metadata=True`` to receive the decision, resolved local date,
    certainty, and source used to make it.
    """

    if as_of is not None:
        if session_date is not None:
            raise TypeError("pass only one of session_date and as_of")
        session_date = as_of
    session = session_for_date(code_or_market, session_date, data_type)
    if return_metadata:
        return _trading_day_details(session)
    return bool(session.is_trading_day)


def trading_day_status(
    code_or_market: Any,
    session_date: Any = None,
    data_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Explicit metadata form of :func:`is_trading_day`."""

    return is_trading_day(
        code_or_market, session_date, data_type, return_metadata=True
    )


def _latest_schedule_date(schedule: Any, end_date: date) -> Optional[date]:
    if schedule is None or bool(getattr(schedule, "empty", False)):
        return None
    candidates = []
    try:
        indexes = schedule.index
    except AttributeError:
        indexes = schedule.keys() if isinstance(schedule, Mapping) else ()
    for index in indexes:
        candidate = _index_date(index)
        if candidate is not None and candidate <= end_date:
            candidates.append(candidate)
    return max(candidates) if candidates else None


def _latest_date_metadata(
    market: str,
    session_date: Optional[date],
    certainty: str,
    source: str,
) -> Dict[str, Any]:
    return {
        "market": market,
        "session_date": session_date,
        "latest_expected_session_date": session_date,
        "certainty": certainty,
        "source": source,
    }


def latest_expected_session_date(
    code_or_market: Any,
    now: Any = None,
    data_type: Optional[str] = None,
    *,
    return_metadata: bool = False,
    as_of: Any = None,
    session_date: Any = None,
) -> Any:
    """Return the latest session whose data should exist at ``now``.

    Datetime callers before a market's opening receive the previous session;
    date-only callers include that date. Holidays and weekends are skipped
    when the exact calendar is available. In fallback mode only weekends are
    skipped, and metadata makes that limitation explicit. The positional
    interface remains ``(code, now=None, data_type=None)``.
    """

    if as_of is not None:
        if now is not None:
            raise TypeError("pass only one of now and as_of")
        now = as_of
    if session_date is not None:
        if now is not None:
            raise TypeError("pass only one of now and session_date")
        now = session_date

    canonical = _resolve_market_code(code_or_market, data_type)
    if canonical not in _MARKET_SPECS:
        result = _latest_date_metadata(
            MARKET_STATIC, None, EXACT_CERTAINTY, STATIC_SOURCE
        )
        return result if return_metadata else None

    resolved_end = _coerce_session_date(canonical, now)
    local_as_of = _coerce_local_as_of(canonical, now)
    if local_as_of is not None:
        current_session = session_for_date(canonical, resolved_end)
        if (
            current_session.is_trading_day
            and current_session.open_at is not None
            and local_as_of < current_session.open_at
        ):
            resolved_end -= timedelta(days=1)
    if get_calendar(canonical) is not None:
        for lookback_days in (31, 366, 3660):
            start = max(
                date.min + timedelta(days=lookback_days),
                resolved_end - timedelta(days=lookback_days),
            )
            schedule = get_schedule(canonical, start, resolved_end)
            if schedule is None:
                break
            latest = _latest_schedule_date(schedule, resolved_end)
            if latest is not None:
                result = _latest_date_metadata(
                    canonical, latest, EXACT_CERTAINTY, CALENDAR_SOURCE
                )
                return result if return_metadata else latest
            # An empty exact schedule can happen only near a calendar's lower
            # bound.  Widen the search before giving up.
        else:
            result = _latest_date_metadata(
                canonical, None, EXACT_CERTAINTY, CALENDAR_SOURCE
            )
            return result if return_metadata else None

    candidate = resolved_end
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    result = _latest_date_metadata(
        canonical, candidate, FALLBACK_CERTAINTY, FALLBACK_SOURCE
    )
    return result if return_metadata else candidate


def market_state_for_market(market: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return an exchange-local open/lunch/close state with provenance."""

    canonical = normalize_market(market)
    spec = _MARKET_SPECS.get(canonical)
    if spec is None:
        # Keep the historic static-market shape stable for existing callers.
        return {
            "market": MARKET_STATIC,
            "market_phase": "closed",
            "market_open": False,
            "market_timezone": None,
        }

    local_tz = ZoneInfo(spec.timezone)
    if now is None:
        local_now = datetime.now(timezone.utc).astimezone(local_tz)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=local_tz)
    else:
        local_now = now.astimezone(local_tz)

    session = session_for_date(canonical, local_now.date())
    phase = "closed"
    is_open = False
    if session.is_trading_day and session.open_at and session.close_at:
        if local_now < session.open_at:
            phase = "preopen"
        elif local_now >= session.close_at:
            phase = "closed"
        elif (
            session.break_start_at
            and session.break_end_at
            and session.break_start_at < session.break_end_at
            and session.break_start_at <= local_now < session.break_end_at
        ):
            phase = spec.break_phase
        elif session.break_end_at and local_now >= session.break_end_at:
            phase = "live_afternoon" if session.break_start_at else "live"
            is_open = True
        else:
            phase = "live_morning" if session.break_start_at else "live"
            is_open = True

    return {
        "market": canonical,
        "market_phase": phase,
        "market_open": is_open,
        "market_timezone": spec.timezone,
        "is_trading_day": session.is_trading_day,
        "certainty": session.certainty,
        "source": session.source,
    }


def clear_caches() -> None:
    """Clear calendar and schedule caches, primarily for tests and reloads."""

    _get_calendar_cached.cache_clear()
    _get_schedule_cached.cache_clear()


def market_state(
    code_or_market: Any,
    now: Optional[datetime] = None,
    data_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Lazy compatibility wrapper for callers importing state here."""

    from services.market_session import market_state as _market_state

    return _market_state(code_or_market, now=now, data_type=data_type)
