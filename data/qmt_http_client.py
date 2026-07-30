"""
QMT 策略 HTTP 客户端（127.0.0.1:18080）

盘中 OHLC 快照主路径：GET /ohlc_batch
历史 K：GET /candles（本模块按需封装）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlencode

import requests

from core.config import QMT_HTTP_BASE_URL, QMT_HTTP_TIMEOUT_SEC

logger = logging.getLogger("qmt_http_client")


def to_qmt_symbol(code: str) -> Optional[str]:
    """board-app 代码 → QMT 代码（600519 / sh600519 / 000001.SZ → 标准后缀）。"""
    if not code:
        return None
    raw = str(code).strip()
    if not raw:
        return None
    upper = raw.upper()
    # already QMT-like
    if upper.endswith((".SH", ".SZ", ".BJ")) and len(upper) >= 9:
        return upper
    # sh000001 / sz399006 / bjxxxxxx
    if upper.startswith("SH") and len(upper) >= 8 and upper[2:].isdigit():
        return "%s.SH" % upper[2:]
    if upper.startswith("SZ") and len(upper) >= 8 and upper[2:].isdigit():
        return "%s.SZ" % upper[2:]
    if upper.startswith("BJ") and len(upper) >= 8 and upper[2:].isdigit():
        return "%s.BJ" % upper[2:]
    # bare 6-digit
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 6:
        if digits.startswith(("5", "6", "9")):
            return "%s.SH" % digits
        if digits.startswith(("0", "1", "2", "3")):
            return "%s.SZ" % digits
        if digits.startswith(("4", "8")):
            return "%s.BJ" % digits
    return None


def from_qmt_symbol(symbol: str) -> str:
    """000001.SH → sh000001；600519.SH → 600519（个股裸码）。"""
    if not symbol:
        return symbol
    upper = str(symbol).strip().upper()
    if "." not in upper:
        return upper.lower() if upper.startswith(("SH", "SZ")) else upper
    code, mkt = upper.split(".", 1)
    if len(code) == 6 and code.startswith("0") and mkt == "SH":
        # index 000001.SH
        return "sh%s" % code
    if len(code) == 6 and code.startswith(("0", "3")) and mkt == "SZ" and code.startswith("399"):
        return "sz%s" % code
    if mkt == "SH" and code.startswith("000"):
        return "sh%s" % code
    if mkt == "SZ" and code.startswith("399"):
        return "sz%s" % code
    return code


class QmtHttpClient:
    def __init__(
        self,
        base_url: str = None,
        timeout: float = None,
        session: requests.Session = None,
    ):
        self.base_url = (base_url or QMT_HTTP_BASE_URL).rstrip("/")
        self.timeout = float(timeout if timeout is not None else QMT_HTTP_TIMEOUT_SEC)
        self._session = session or requests.Session()

    def health(self) -> Dict[str, Any]:
        try:
            r = self._session.get("%s/health" % self.base_url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            data["_ok"] = data.get("status") == "ok"
            return data
        except Exception as e:
            logger.debug("[qmt_http] health failed: %s", e)
            return {"_ok": False, "error": str(e)}

    def is_available(self) -> bool:
        return bool(self.health().get("_ok"))

    def ohlc_batch(
        self,
        codes: Sequence[str],
        period: str = "1d",
        max_batch: int = 500,
    ) -> Dict[str, Any]:
        """
        批量当日 OHLC 快照。

        codes: board-app 或 QMT 代码均可。
        返回:
          {
            ok, channel, mode, elapsed_ms, items: {board_code: ohlc_dict},
            errors, requested_count, success_count
          }
        items 的 key 尽量用调用方传入的原始 code（能映射的）。
        """
        t0 = time.time()
        raw_list = [str(c).strip() for c in codes if c and str(c).strip()]
        if not raw_list:
            return {
                "ok": False,
                "error": "symbols_required",
                "items": {},
                "errors": [],
                "channel": None,
                "elapsed_ms": 0,
            }

        qmt_symbols: List[str] = []
        qmt_to_raw: Dict[str, str] = {}
        skipped = []
        for raw in raw_list:
            q = to_qmt_symbol(raw)
            if not q:
                skipped.append({"symbol": raw, "error": "unmapped_symbol"})
                continue
            if q not in qmt_to_raw:
                qmt_symbols.append(q)
                qmt_to_raw[q] = raw

        if not qmt_symbols:
            return {
                "ok": False,
                "error": "no_mappable_symbols",
                "items": {},
                "errors": skipped,
                "channel": None,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        # chunk
        chunks = [
            qmt_symbols[i : i + max_batch]
            for i in range(0, len(qmt_symbols), max(1, max_batch))
        ]
        merged_items: Dict[str, Any] = {}
        merged_errors = list(skipped)
        channel = None
        mode = None
        for chunk in chunks:
            try:
                qs = urlencode(
                    {
                        "symbols": ",".join(chunk),
                        "period": period,
                        "max": str(max_batch),
                    }
                )
                r = self._session.get(
                    "%s/ohlc_batch?%s" % (self.base_url, qs),
                    timeout=self.timeout,
                )
                r.raise_for_status()
                payload = r.json()
            except Exception as e:
                logger.warning("[qmt_http] ohlc_batch failed: %s", e)
                for s in chunk:
                    merged_errors.append({"symbol": qmt_to_raw.get(s, s), "error": str(e)})
                continue

            channel = payload.get("channel") or channel
            mode = payload.get("mode") or mode
            items = payload.get("items") or {}
            for qsym, item in items.items():
                raw_key = qmt_to_raw.get(qsym) or qmt_to_raw.get(str(qsym).upper())
                if raw_key is None:
                    # try reverse match
                    for k, v in qmt_to_raw.items():
                        if k.upper() == str(qsym).upper():
                            raw_key = v
                            break
                if raw_key is None:
                    raw_key = from_qmt_symbol(qsym)
                row = dict(item) if isinstance(item, dict) else {}
                row["code"] = raw_key
                row["qmt_symbol"] = qsym
                # normalize price aliases for UI
                if "price" not in row and row.get("close") is not None:
                    row["price"] = row["close"]
                if "changePct" not in row and row.get("change_pct") is not None:
                    row["changePct"] = row["change_pct"]
                if "change_pct" not in row and row.get("changePct") is not None:
                    row["change_pct"] = row["changePct"]
                merged_items[raw_key] = row
            for err in payload.get("errors") or []:
                es = err.get("symbol")
                raw_key = qmt_to_raw.get(es, es)
                merged_errors.append({"symbol": raw_key, "error": err.get("error")})

        ok = len(merged_items) > 0
        return {
            "ok": ok,
            "channel": channel or "qmt18080",
            "mode": mode,
            "items": merged_items,
            "errors": merged_errors,
            "requested_count": len(raw_list),
            "success_count": len(merged_items),
            "error_count": len(merged_errors),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def candles(
        self,
        code: str,
        period: str = "1d",
        count: int = 2,
    ) -> Dict[str, Any]:
        q = to_qmt_symbol(code)
        if not q:
            return {"ok": False, "error": "unmapped_symbol", "bars": []}
        try:
            qs = urlencode({"symbol": q, "period": period, "count": str(count)})
            r = self._session.get(
                "%s/candles?%s" % (self.base_url, qs),
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            bars = payload.get("bars") or []
            return {
                "ok": not payload.get("error") and bool(bars),
                "symbol": q,
                "bars": bars,
                "error": payload.get("error"),
                "channel": "qmt18080",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "bars": [], "channel": "qmt18080"}


_client: Optional[QmtHttpClient] = None


def get_qmt_http_client() -> QmtHttpClient:
    global _client
    if _client is None:
        _client = QmtHttpClient()
    return _client
