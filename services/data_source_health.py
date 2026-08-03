"""Read-only health summary for the five user-visible market data areas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional


def _format_date(value: Any) -> str:
    raw = str(value or "").strip().replace("-", "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return str(value or "").strip()


def _latest_date(records: Iterable[Dict[str, Any]]) -> str:
    values = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("local_max", "target_date", "last_date", "trade_date", "date"):
            value = _format_date(record.get(key))
            if value:
                values.append(value)
                break
    return max(values) if values else ""


def _item(
    item_id: str,
    label: str,
    status: str,
    status_text: str,
    source: str,
    detail: str,
    last_updated: str = "",
) -> Dict[str, str]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "status_text": status_text,
        "source": source,
        "detail": detail,
        "last_updated": last_updated,
    }


def _board_item(kind: str, label: str, snapshot: dict, spot: dict) -> Dict[str, str]:
    count = int(snapshot.get(f"captured_count_{kind}") or 0)
    if not count:
        count = int((spot.get("counts") or {}).get(kind) or 0)
    mode = str(snapshot.get("mode") or "off")
    captured_at = snapshot.get("captured_at")
    last_updated = ""
    if captured_at:
        try:
            last_updated = datetime.fromtimestamp(float(captured_at)).astimezone().isoformat()
        except (TypeError, ValueError, OSError):
            last_updated = ""
    if not last_updated:
        time_part = str((spot.get("timestamps") or {}).get(kind) or "")
        date_part = _format_date(snapshot.get("date"))
        last_updated = f"{date_part} {time_part}".strip()

    if count <= 0:
        return _item(kind, label, "warning", "待首次加载", "东财板块行情", "当前缓存为空")
    if mode == "live":
        return _item(kind, label, "healthy", "正常更新", "东财实时快照", f"已载入 {count} 个板块", last_updated)
    if mode == "frozen" or spot.get("frozen"):
        return _item(kind, label, "idle", "午休缓存", "东财上午快照", f"已冻结 {count} 个板块", last_updated)
    return _item(kind, label, "idle", "休市缓存", "板块收盘缓存", f"已载入 {count} 个板块", last_updated)


def _update_records(status: dict, key: str) -> list:
    records = status.get(key) or {}
    if isinstance(records, dict):
        return [value for value in records.values() if isinstance(value, dict)]
    return []


def _successful(record: dict) -> bool:
    state = str(record.get("status") or "").lower()
    return state in {"success", "ok", "updated", "up_to_date", "done", "skipped"}


def build_data_source_health(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Build a local-only status document; no function called here may refresh data."""
    from core.lifecycle import is_qmt_available
    from services.board_snapshot import get_snapshot_cache
    from services.board_spot_cache import get_board_spot_cache
    from services.intraday_cache import get_intraday_cache
    from services.nav_spot_service import get_nav_spot_status
    from services.update_status_store import load_status

    generated_at = (now or datetime.now().astimezone()).isoformat()
    items = []

    try:
        nav = get_nav_spot_status(now=now)
        count = int(nav.get("count") or 0)
        channels = [name.upper() for name, meta in (nav.get("channels") or {}).items() if meta.get("count")]
        source = " + ".join(channels) or "行情缓存"
        if not count:
            items.append(_item("top_navigation", "顶部导航栏", "warning", "待首次加载", source, "当前缓存为空"))
        elif nav.get("all_markets_closed"):
            items.append(_item("top_navigation", "顶部导航栏", "idle", "休市缓存", source, f"{count} 个标的", nav.get("cached_at") or ""))
        elif nav.get("stale"):
            items.append(_item("top_navigation", "顶部导航栏", "warning", "更新延迟", source, f"{count} 个标的，缓存 {nav.get('age_sec')} 秒", nav.get("cached_at") or ""))
        else:
            suffix = "，正在刷新" if nav.get("inflight") else ""
            items.append(_item("top_navigation", "顶部导航栏", "healthy", "正常更新", source, f"{count} 个标的{suffix}", nav.get("cached_at") or ""))
    except Exception:
        items.append(_item("top_navigation", "顶部导航栏", "unavailable", "状态不可用", "行情缓存", "无法读取本地状态"))

    try:
        snapshot = get_snapshot_cache().stats()
        spot = get_board_spot_cache().stats()
        items.append(_board_item("concept", "东财概念板块", snapshot, spot))
        items.append(_board_item("industry", "行业板块", snapshot, spot))
    except Exception:
        items.append(_item("concept", "东财概念板块", "unavailable", "状态不可用", "东财板块行情", "无法读取本地状态"))
        items.append(_item("industry", "行业板块", "unavailable", "状态不可用", "东财板块行情", "无法读取本地状态"))

    try:
        update_status = load_status()
    except Exception:
        update_status = {}

    try:
        index_records = _update_records(update_status, "indices")
        valid_indices = [record for record in index_records if not record.get("permanent_skip")]
        succeeded = sum(1 for record in valid_indices if _successful(record))
        latest = _latest_date(valid_indices) or _format_date(update_status.get("today"))
        if valid_indices and succeeded == len(valid_indices):
            items.append(_item("indices", "指数", "healthy", "日线正常", "QMT / 本地日线", f"{succeeded}/{len(valid_indices)} 个更新任务正常", latest))
        elif succeeded:
            items.append(_item("indices", "指数", "warning", "部分异常", "QMT / 本地日线", f"{succeeded}/{len(valid_indices)} 个更新任务正常", latest))
        else:
            items.append(_item("indices", "指数", "warning", "待首次更新", "QMT / 本地日线", "暂无成功更新记录", latest))
    except Exception:
        items.append(_item("indices", "指数", "unavailable", "状态不可用", "QMT / 本地日线", "无法读取本地状态"))

    try:
        intraday = get_intraday_cache().stats()
        batch = intraday.get("last_batch_meta") or {}
        qmt_ok = bool(is_qmt_available() or batch.get("ok") or intraday.get("size"))
        daily_date = _format_date(update_status.get("qmt_daily_done"))
        stock_records = _update_records(update_status, "stocks")
        latest = daily_date or _latest_date(stock_records)
        if qmt_ok:
            detail = f"盘中缓存 {int(intraday.get('size') or 0)} 个标的"
            items.append(_item("stocks", "个股数据源", "healthy", "QMT 可用", "QMT HTTP / 本地数据", detail, latest))
        elif latest:
            items.append(_item("stocks", "个股数据源", "warning", "本地缓存可用", "QMT 本地日线", "实时通道当前不可用", latest))
        else:
            items.append(_item("stocks", "个股数据源", "unavailable", "数据源不可用", "QMT HTTP / 本地数据", "暂无可用状态"))
    except Exception:
        items.append(_item("stocks", "个股数据源", "unavailable", "状态不可用", "QMT HTTP / 本地数据", "无法读取本地状态"))

    return {"ok": True, "generated_at": generated_at, "items": items}
