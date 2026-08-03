"""browser_smoke_seed.py — 真实浏览器 smoke 的临时运行数据种子。

在调用方给定的 ``runtime_root`` 下动态创建：

- ``runtime_root/data/kline.db`` —— 复用生产 ``data.sqlite_repo.SqliteRepo``
  写库（表结构与生产完全一致），为 sh000001 / sz399006 / 603259 / BK1106
  生成足够多的日线（工作日、amount>0、可周线重采样）；
- ``runtime_root/data/search_index.json`` —— 与生产
  ``static/search_index.json`` 同构，至少包含上述四个标的，603259 的
  initials 支持 ``ymkd`` 搜索。

绝不写项目 ``data/`` 或 ``static/``。返回的 env dict 必须在导入
``core.config`` / ``app`` 之前并入 ``os.environ``；
``browser_smoke_server`` 已按此顺序处理。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# 与生产 static/search_index.json 中对应条目保持一致的元数据
SMOKE_SYMBOLS: list[dict[str, Any]] = [
    {
        "code": "sh000001",
        "name": "上证指数",
        "type": "index",
        "category": "主题、风格与事件",
        "initials": ["S", "Z", "", ""],
        "tags": ["主题风格", "宽基指数风格指标", "指数", "上证指数", "主题、风格与事件"],
        "seed": 1,
        "base_price": 3000.0,
        "volume_base": 300_000_000,
        "amount_factor": 100.0,
    },
    {
        "code": "sz399006",
        "name": "创业板指",
        "type": "index",
        "category": "主题、风格与事件",
        "initials": ["C", "Y", "B", ""],
        "tags": ["主题风格", "宽基指数风格指标", "指数", "创业板指", "主题、风格与事件"],
        "seed": 2,
        "base_price": 2100.0,
        "volume_base": 200_000_000,
        "amount_factor": 100.0,
    },
    {
        "code": "603259",
        "name": "药明康德",
        "type": "stock",
        "category": "个股",
        "initials": ["Y", "M", "K", "D"],
        "tags": ["药明康德", "个股"],
        "seed": 3,
        "base_price": 52.0,
        "volume_base": 15_000_000,
        "amount_factor": 1.0,
    },
    {
        "code": "BK1106",
        "name": "创新药",
        "type": "concept",
        "category": "医药生物与健康",
        "initials": ["C", "X", "Y"],
        "tags": ["医药健康", "创新药", "概念", "CXO", "医药生物与健康"],
        "seed": 4,
        "base_price": 1250.0,
        "volume_base": 180_000_000,
        "amount_factor": 100.0,
    },
]

TRADING_DAYS = 500


def _generate_daily(
    code: str,
    seed: int,
    base_price: float,
    volume_base: int,
    amount_factor: float,
):
    """生成确定性日线：工作日、OHLC 有效、volume>0、amount>0。"""
    import numpy as np
    import pandas as pd

    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=TRADING_DAYS)
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0004, 0.016, len(dates))
    closes = base_price * np.cumprod(1.0 + returns)
    opens = np.empty(len(dates))
    opens[0] = base_price
    opens[1:] = closes[:-1]
    spread = np.abs(rng.normal(0.006, 0.003, len(dates)))
    highs = np.maximum(opens, closes) * (1.0 + spread)
    lows = np.minimum(opens, closes) * (1.0 - spread)
    volumes = rng.integers(max(1, volume_base // 2), volume_base + 1, len(dates))

    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens.round(2),
            "high": highs.round(2),
            "low": lows.round(2),
            "close": closes.round(2),
            "volume": volumes,
        }
    )
    frame["amount"] = (frame["volume"] * frame["close"] * amount_factor).round(2)
    return frame


def seed_smoke_environment(runtime_root: Path) -> Dict[str, str]:
    """在 ``runtime_root`` 下创建临时运行库与搜索索引，返回环境变量 dict。

    幂等：重复调用同一 ``runtime_root`` 会先重建其中的 kline.db（仅限
    runtime_root 内部），不会触碰项目 data/ 与 static/。
    """
    root = Path(runtime_root).resolve()
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "kline.db"
    index_path = data_dir / "search_index.json"

    # 重建运行库，保证幂等（目标明确限定在 runtime_root 内）
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(db_path) + suffix)
        if stale.exists():
            stale.unlink()

    # 复用生产 SqliteRepo：表结构、写库与 kline_amount/kline_meta 完全一致
    from data.sqlite_repo import SqliteRepo

    repo = SqliteRepo(db_path)
    for symbol in SMOKE_SYMBOLS:
        frame = _generate_daily(
            symbol["code"],
            symbol["seed"],
            symbol["base_price"],
            symbol["volume_base"],
            symbol["amount_factor"],
        )
        repo.save_kline(
            symbol["code"],
            "daily",
            frame,
            name=symbol["name"],
            data_type=symbol["type"],
        )

    index = {
        "version": 2,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(SMOKE_SYMBOLS),
        "items": {
            symbol["code"]: {
                "name": symbol["name"],
                "type": symbol["type"],
                "category": symbol["category"],
                "initials": list(symbol["initials"]),
                "tags": list(symbol["tags"]),
            }
            for symbol in SMOKE_SYMBOLS
        },
    }
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    return {
        "BOARD_APP_DATA_DIR": str(data_dir),
        "BOARD_APP_SEARCH_INDEX_PATH": str(index_path),
        "QMT_ENABLED": "0",
        "QMT_AUTO_START": "0",
        "QMT_STARTUP_HISTORY_SYNC": "0",
        "BOARD_APP_STARTUP_PREWARM": "0",
        "BOARD_APP_AUTO_BOOTSTRAP": "0",
    }
