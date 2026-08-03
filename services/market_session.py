"""Exchange-local session clocks for navigation quote refresh decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

from services.exchange_calendar_service import (
    MARKET_A_SHARE,
    MARKET_HONG_KONG,
    MARKET_JAPAN,
    MARKET_SOUTH_KOREA,
    MARKET_STATIC,
    MARKET_TAIWAN,
    MARKET_US,
    latest_expected_session_date,
    market_state_for_market,
    market_timezone,
    normalize_market,
)


_HK_CODES = frozenset({"HSI", "HSTECH"})
_JAPAN_CODES = frozenset({"^N225"})
_KOREA_CODES = frozenset({"^KS11"})
_TAIWAN_CODES = frozenset({"^TWII"})
_US_CODES = frozenset({"SPX", "IXIC", "DJI"})
_A_SHARE_TYPES = frozenset({"index", "stock", "concept", "industry"})


def classify_market(code: Any, data_type: Optional[str] = None) -> str:
    """Classify a navigation symbol without converting between timezones."""

    raw = str(code or "").strip()
    direct_market = normalize_market(raw)
    if direct_market != MARKET_STATIC:
        return direct_market

    upper = raw.upper()
    if upper in _HK_CODES:
        return MARKET_HONG_KONG
    if upper in _JAPAN_CODES:
        return MARKET_JAPAN
    if upper in _KOREA_CODES:
        return MARKET_SOUTH_KOREA
    if upper in _TAIWAN_CODES:
        return MARKET_TAIWAN
    if upper in _US_CODES:
        return MARKET_US

    lower = raw.lower()
    if (
        upper == "800000"
        or upper.startswith("BK")
        or lower.startswith(("sh", "sz", "bj"))
        or (raw.isdigit() and len(raw) == 6 and data_type in _A_SHARE_TYPES)
    ):
        return MARKET_A_SHARE
    return MARKET_STATIC


def _local_now(market: str, now: Optional[datetime]) -> datetime:
    timezone_name = market_timezone(market)
    if not timezone_name:
        return now or datetime.now(timezone.utc)
    tz = ZoneInfo(timezone_name)
    if now is None:
        return datetime.now(timezone.utc).astimezone(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def market_state(
    code_or_market: Any,
    now: Optional[datetime] = None,
    data_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the exchange-local phase and live flag for one symbol/market."""

    raw = str(code_or_market or "").strip()
    market = normalize_market(raw)
    if market == MARKET_STATIC:
        market = classify_market(raw, data_type)
    return market_state_for_market(market, now=_local_now(market, now))


def market_is_live(
    code_or_market: Any,
    now: Optional[datetime] = None,
    data_type: Optional[str] = None,
) -> bool:
    return bool(market_state(code_or_market, now=now, data_type=data_type)["market_open"])


def _target_parts(target: Any) -> Tuple[Any, Optional[str]]:
    if isinstance(target, (tuple, list)):
        code = target[0] if target else ""
        data_type = target[2] if len(target) > 2 else None
        return code, data_type
    if isinstance(target, dict):
        return target.get("code") or target.get("ticker"), target.get("type")
    return target, None


def active_market_signature(
    targets: Iterable[Any], now: Optional[datetime] = None
) -> Tuple[str, ...]:
    """Return the sorted set of markets currently trading for the targets."""
    active = set()
    for target in targets:
        code, data_type = _target_parts(target)
        state = market_state(code, now=now, data_type=data_type)
        if state["market_open"]:
            active.add(state["market"])
    return tuple(sorted(active))


def market_phase_signature(
    targets: Iterable[Any], now: Optional[datetime] = None
) -> Tuple[str, ...]:
    """Return a stable signature that changes at every relevant market boundary."""
    states = {}
    for target in targets:
        code, data_type = _target_parts(target)
        state = market_state(code, now=now, data_type=data_type)
        market = state["market"]
        if market != MARKET_STATIC:
            states[market] = state
    return tuple(
        f"{market}:{states[market]['market_phase']}:{int(states[market]['market_open'])}"
        for market in sorted(states)
    )


def nav_market_signature(
    targets: Iterable[Any], now: Optional[datetime] = None
) -> Tuple[str, ...]:
    """Compatibility-friendly alias for the full navigation boundary signature."""
    return market_phase_signature(targets, now=now)
