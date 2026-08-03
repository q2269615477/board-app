"""Low-level helpers for the QMT index update pipeline.

The manager historically contained both the exchange-session policy and the
Tushare tail-fallback implementation.  This module owns those low-level
operations without importing :mod:`data_update_manager`.  The manager keeps
thin compatibility facades and injects its runtime dependencies so existing
callers can continue patching the old names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional


logger = logging.getLogger("data_update")


def normalize_session_date(value: Any) -> str:
    """Normalize a calendar result to ``YYYYMMDD``.

    Calendar providers may return ``date``/``datetime`` objects or strings in
    either dashed or compact form.  Invalid values intentionally become an
    empty string so the caller can use its documented fallback policy.
    """

    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    value_str = str(value).strip().replace("-", "").replace("/", "")
    return value_str if len(value_str) == 8 and value_str.isdigit() else ""


def load_exchange_calendar_api(
    logger_: logging.Logger = logger,
) -> Optional[tuple[Callable[..., Any], Callable[..., Any]]]:
    """Load the optional exchange-calendar provider lazily.

    Importing the calendar service at module import time would make the
    standalone update manager depend on the complete Flask/runtime graph.
    Returning ``None`` preserves the historical local-weekday fallback when
    the optional service is not installed or cannot be imported.
    """

    try:
        from services.exchange_calendar_service import (
            latest_expected_session_date,
            market_state,
        )
    except Exception as exc:  # pragma: no cover - exercised by runtime faults
        logger_.debug(
            "[交易日] exchange_calendar_service 未就绪，回退本地工作日: %s",
            exc,
        )
        return None
    return latest_expected_session_date, market_state


def local_weekday_session_date(
    code: str,
    now: Optional[datetime] = None,
    *,
    index_exchange_market: Optional[Mapping[str, str]] = None,
    index_exchange_tz: Optional[Mapping[str, str]] = None,
    now_factory: Callable[[], datetime] = datetime.now,
) -> str:
    """Return the latest local-weekday session date for an index market.

    This is deliberately only the calendar-service *fallback*: it removes
    weekends, but does not invent exchange holidays.  ``now_factory`` and the
    market maps are injected by the manager for deterministic tests and to
    keep the canonical mapping in one place.
    """

    market_map = index_exchange_market or {}
    timezone_map = index_exchange_tz or {}
    market = market_map.get(str(code), "a_share")
    tz_name = timezone_map.get(market, "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo

        current = now if now is not None else now_factory()
        if current.tzinfo is None:
            local = current.replace(tzinfo=ZoneInfo(tz_name))
        else:
            local = current.astimezone(ZoneInfo(tz_name))
        day = local.date()
    except Exception:
        current = now if now is not None else now_factory()
        day = current.date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y%m%d")


@dataclass(frozen=True)
class IndexUpdateDependencies:
    """Runtime seams supplied by ``data_update_manager``.

    Every callable is intentionally injected.  In particular, the manager
    supplies its compatibility wrappers for calendar/session helpers, so
    monkeypatches against the historical manager names continue to affect the
    new implementation.
    """

    tushare_index_api_map: Mapping[str, tuple[str, str]]
    fetch_tushare_index_df: Callable[[str, str, str, str], Any]
    exchange_calendar_api: Callable[[], Optional[tuple[Callable[..., Any], Callable[..., Any]]]]
    normalize_session_date: Callable[[Any], str]
    local_weekday_session_date: Callable[[str, Optional[datetime]], str]
    index_exchange_market: Mapping[str, str]
    now: Callable[[], datetime]
    logger: logging.Logger = logger


class IndexUpdateService:
    """Low-level index fallback and exchange-session policy operations."""

    def __init__(self, dependencies: IndexUpdateDependencies):
        self.dependencies = dependencies

    def tushare_fallback_single_index(
        self,
        code: str,
        name: str,
        local_max: str,
        last_td_norm: str,
        cur,
        insert_sql: str,
        now_str: str,
    ) -> int:
        """Fill a stale QMT index tail with Tushare rows.

        The DB cursor/SQL are supplied by the caller because the manager owns
        the shared ledger transaction.  This method only fetches, normalizes
        the rows already returned by the injected fetcher, and commits those
        rows through the cursor's connection, matching the historical
        behavior exactly.
        """

        start = (
            datetime.strptime(local_max, "%Y%m%d") + timedelta(days=1)
        ).strftime("%Y%m%d")
        if start > last_td_norm:
            return 0
        try:
            mapping = self.dependencies.tushare_index_api_map.get(code)
            if not mapping:
                self.dependencies.logger.warning(
                    "[Tushare兜底] %s(%s) 无 Tushare 映射，跳过", name, code
                )
                return 0
            api, ts_code = mapping
            frame = self.dependencies.fetch_tushare_index_df(
                api, ts_code, start, last_td_norm
            )
            rows = frame.to_dict("records") if not frame.empty else []
            if not rows:
                # 盘中/非交易时段 Tushare 日线尚未更新 → 降为 INFO
                current_day = self.dependencies.now().strftime("%Y%m%d")
                if start > current_day or start == last_td_norm:
                    self.dependencies.logger.info(
                        "[Tushare兜底] %s(%s) 今日日线暂未发布 (%s/%s %s) "
                        "— 数据已是最新",
                        name,
                        code,
                        api,
                        ts_code,
                        start,
                    )
                else:
                    self.dependencies.logger.warning(
                        "[Tushare兜底] %s(%s) Tushare 也无新数据 "
                        "(%s/%s %s..%s)",
                        name,
                        code,
                        api,
                        ts_code,
                        start,
                        last_td_norm,
                    )
                return 0
            batch = [
                (
                    code,
                    "daily",
                    row["date"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    now_str,
                )
                for row in rows
            ]
            cur.executemany(insert_sql, batch)
            cur.connection.commit()
            last_date = max(row["date"] for row in rows)
            self.dependencies.logger.info(
                "[Tushare兜底] ✓ %s(%s) 补齐 %s 条 %s→%s (api=%s/%s)",
                name,
                code,
                len(batch),
                start,
                last_date,
                api,
                ts_code,
            )
            return len(batch)
        except Exception as exc:
            self.dependencies.logger.error(
                "[Tushare兜底] %s(%s) 失败: %s", name, code, exc
            )
            return 0

    def index_session_target(
        self,
        code: str,
        now: Optional[datetime] = None,
        fallback_td_norm: Optional[str] = None,
    ) -> tuple[str, bool]:
        """Return ``(latest expected session date, market-open flag)``."""

        api = self.dependencies.exchange_calendar_api()
        if api is not None:
            latest_session, market_state = api
            try:
                target = self.dependencies.normalize_session_date(
                    latest_session(str(code), now=now)
                )
                state = market_state(str(code), now=now)
                is_open = (
                    bool(state.get("market_open"))
                    if isinstance(state, dict)
                    else False
                )
                if target:
                    return target, is_open
                self.dependencies.logger.warning(
                    "[交易日] %s 日历服务返回空会话日，回退本地工作日", code
                )
            except Exception as exc:
                self.dependencies.logger.warning(
                    "[交易日] %s 日历服务异常，回退本地工作日: %s", code, exc
                )
        market = self.dependencies.index_exchange_market.get(
            str(code), "a_share"
        )
        if market == "a_share":
            return (
                fallback_td_norm
                or self.dependencies.local_weekday_session_date(str(code), now),
                False,
            )
        return (
            self.dependencies.local_weekday_session_date(str(code), now),
            False,
        )

