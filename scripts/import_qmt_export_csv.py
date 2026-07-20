"""Import CSVs written by in-client QMT ContextInfo export into board-app SQLite.

Usage:
  venv\\Scripts\\python.exe scripts\\import_qmt_export_csv.py
  venv\\Scripts\\python.exe scripts\\import_qmt_export_csv.py path\\to\\file.csv

Expected CSV: from ContextInfo.get_market_data_ex → DataFrame.to_csv
  index = time / stime, columns include open,high,low,close,volume
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXPORT_DIR = ROOT / "data" / "qmt_export"


def normalize_code(name: str) -> str:
    """000001_SH_....csv or 000001.SH → board code."""
    stem = Path(name).stem
    # strip timestamp tail
    stem = re.sub(r"_\d{8}_\d{6}$", "", stem)
    if "." in stem:
        code, mkt = stem.split(".", 1)
        mkt = mkt.upper()
    elif "_" in stem:
        parts = stem.split("_")
        code, mkt = parts[0], parts[1].upper() if len(parts) > 1 else "SH"
    else:
        code, mkt = stem, "SH"
    # board-app index style: sh000001 / stock bare code
    if code.startswith("000") or code.startswith("399") or code.startswith("880"):
        prefix = "sh" if mkt in ("SH", "SS") else "sz"
        return f"{prefix}{code}"
    return code


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    # index may be first column
    time_col = None
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("time", "stime", "date", "datetime", "unnamed: 0"):
            time_col = c
            break
    if time_col is None and df.columns[0] not in ("open", "high", "low", "close", "volume"):
        time_col = df.columns[0]
    if time_col is not None:
        df = df.rename(columns={time_col: "date"})
    # normalize date
    if "date" in df.columns:
        s = df["date"].astype(str)
        # ms timestamp?
        if s.str.match(r"^\d{12,}$").all():
            df["date"] = pd.to_datetime(s.astype("int64"), unit="ms").dt.strftime("%Y-%m-%d")
        else:
            df["date"] = pd.to_datetime(s, errors="coerce").dt.strftime("%Y-%m-%d")
    cols = {}
    for need in ("open", "high", "low", "close", "volume"):
        for c in df.columns:
            if str(c).lower() == need:
                cols[need] = c
                break
    missing = [k for k in ("open", "high", "low", "close") if k not in cols]
    if missing or "date" not in df.columns:
        raise ValueError(f"{path.name}: missing columns {missing} date_ok={'date' in df.columns}")
    out = pd.DataFrame(
        {
            "date": df["date"],
            "open": pd.to_numeric(df[cols["open"]], errors="coerce"),
            "high": pd.to_numeric(df[cols["high"]], errors="coerce"),
            "low": pd.to_numeric(df[cols["low"]], errors="coerce"),
            "close": pd.to_numeric(df[cols["close"]], errors="coerce"),
            "volume": pd.to_numeric(df[cols.get("volume", cols["close"])], errors="coerce").fillna(0),
        }
    )
    out = out.dropna(subset=["date", "close"]).drop_duplicates("date").sort_values("date")
    return out


def write_db(code: str, df: pd.DataFrame) -> int:
    """Write via project standard _db_write_kline (INSERT OR REPLACE)."""
    from data_loader import _db_write_kline

    n = _db_write_kline(code, "daily", df)
    return int(n or len(df))


def main():
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else sorted(EXPORT_DIR.glob("*.csv"))
    if not paths:
        print("no csv in", EXPORT_DIR)
        print("先在 QMT 内运行 data/qmt_export/in_client_export_strategy.py 导出 CSV")
        return
    total = 0
    for p in paths:
        if not p.exists() or p.suffix.lower() != ".csv":
            print("skip", p)
            continue
        code = normalize_code(p.name)
        try:
            df = load_csv(p)
            n = write_db(code, df)
            total += n
            print(f"OK {p.name} → {code} rows={len(df)} written={n} last={df['date'].iloc[-1] if len(df) else None}")
        except Exception as e:
            print(f"FAIL {p.name}: {e}")
    print("total_written", total)


if __name__ == "__main__":
    main()
