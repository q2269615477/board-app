"""Parse QMT local .DAT daily bars without xtquant; find plausible record layout."""
import json
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "_qmt_dat_parse.json"
MAIN = Path(r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\datadir\SH\86400\000001.DAT")
MINI = Path(r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini\datadir\SH\86400\000001.DAT")


def yyyymmdd_ok(v: int) -> bool:
    if v < 19900101 or v > 20301231:
        return False
    y, m, d = v // 10000, (v // 100) % 100, v % 100
    return 1990 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31


def unix_day_ok(v: int) -> bool:
    # seconds since epoch for dates 1990-2030
    if 631152000 <= v <= 1893456000:
        return True
    # ms
    if 631152000000 <= v <= 1893456000000:
        return True
    return False


def score_layout(data: bytes, rec: int, layout: str) -> dict:
    """layout tokens: I=u32 time, f=float, d=double, q=i64, Q=u64"""
    n = len(data) // rec
    if n < 5 or len(data) % rec != 0:
        return {"ok": False, "reason": "size"}
    # parse last 5 records
    samples = []
    price_ok = 0
    time_ok = 0
    for i in range(max(0, n - 5), n):
        off = i * rec
        raw = data[off : off + rec]
        vals = []
        pos = 0
        try:
            for ch in layout:
                if ch == "I":
                    vals.append(("I", struct.unpack_from("<I", raw, pos)[0]))
                    pos += 4
                elif ch == "i":
                    vals.append(("i", struct.unpack_from("<i", raw, pos)[0]))
                    pos += 4
                elif ch == "f":
                    vals.append(("f", struct.unpack_from("<f", raw, pos)[0]))
                    pos += 4
                elif ch == "d":
                    vals.append(("d", struct.unpack_from("<d", raw, pos)[0]))
                    pos += 8
                elif ch == "Q":
                    vals.append(("Q", struct.unpack_from("<Q", raw, pos)[0]))
                    pos += 8
                elif ch == "q":
                    vals.append(("q", struct.unpack_from("<q", raw, pos)[0]))
                    pos += 8
                else:
                    return {"ok": False, "reason": f"bad token {ch}"}
            if pos != rec:
                return {"ok": False, "reason": f"layout_len {pos}!={rec}"}
        except Exception as e:
            return {"ok": False, "reason": str(e)[:80]}

        # heuristic: time field index 0
        t = vals[0][1]
        t_ok = False
        t_str = str(t)
        if isinstance(t, int):
            if yyyymmdd_ok(t):
                t_ok = True
                t_str = str(t)
            elif unix_day_ok(t):
                t_ok = True
                if t > 1e12:
                    t_str = datetime.fromtimestamp(t / 1000, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                else:
                    t_str = datetime.fromtimestamp(t, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        if t_ok:
            time_ok += 1

        # price-like floats/doubles in 1..4
        prices = [v for k, v in vals[1:5] if k in ("f", "d")]
        if prices and all(10 < p < 20000 for p in prices):
            # OHLC order sanity: high>=max(o,c) low<=min(o,c) roughly
            if len(prices) >= 4:
                o, h, l, c = prices[:4]
                if h >= max(o, c) * 0.98 and l <= min(o, c) * 1.02 and h >= l:
                    price_ok += 1
            else:
                price_ok += 1

        samples.append({"t": t_str, "vals": [(k, round(v, 4) if isinstance(v, float) else v) for k, v in vals[:8]]})

    score = time_ok * 10 + price_ok * 5
    return {
        "ok": score > 0,
        "rec": rec,
        "layout": layout,
        "n": n,
        "score": score,
        "time_ok": time_ok,
        "price_ok": price_ok,
        "samples": samples,
    }


def analyze(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    data = path.read_bytes()
    out["size"] = len(data)
    out["head_hex"] = data[:48].hex()
    candidates = []
    # common layouts (rec size, layout string)
    layouts = [
        (32, "Iffffff"),   # time + 6 float
        (32, "Ifffffi"),
        (28, "Ifffff"),
        (40, "Ifffffff"),
        (40, "IffffffI"),
        (48, "Ifffffffffff"),  # too many? skip if len mismatch handled
        (48, "QffffffII"),
        (40, "Qfffff"),
        (36, "Iffffffff"),
        (32, "fffffffI"),
        (40, "dfffff"),  # double time?
        (48, "dddddd"),
        (56, "Iddddd"),
        (64, "Iddddddd"),
        (32, "iiiiiiii"),
        # time as double yyyymmdd?
        (40, "dfffffi"),
        (36, "IfffffI"),
        (44, "Iffffffffff"),  # invalid
    ]
    # auto generate for rec that divides
    for rec in (28, 32, 36, 40, 48, 52, 56, 64):
        if len(data) % rec != 0:
            continue
        # I + floats
        nfloat = (rec - 4) // 4
        if nfloat >= 4:
            layouts.append((rec, "I" + "f" * nfloat))
        nfloat2 = (rec - 8) // 4
        if nfloat2 >= 4:
            layouts.append((rec, "Q" + "f" * nfloat2))
            layouts.append((rec, "d" + "f" * nfloat2))

    seen = set()
    for rec, layout in layouts:
        key = (rec, layout)
        if key in seen:
            continue
        seen.add(key)
        # verify layout byte size
        sz = 0
        for ch in layout:
            sz += {"I": 4, "i": 4, "f": 4, "d": 8, "Q": 8, "q": 8}.get(ch, 0)
        if sz != rec:
            continue
        r = score_layout(data, rec, layout)
        if r.get("score", 0) > 0:
            candidates.append(r)

    candidates.sort(key=lambda x: -x["score"])
    out["top"] = candidates[:8]
    out["best"] = candidates[0] if candidates else None

    # if best, dump last 10 as bars
    if out["best"]:
        b = out["best"]
        rec, layout = b["rec"], b["layout"]
        n = len(data) // rec
        bars = []
        for i in range(max(0, n - 10), n):
            raw = data[i * rec : (i + 1) * rec]
            pos = 0
            vals = []
            for ch in layout:
                if ch == "I":
                    vals.append(struct.unpack_from("<I", raw, pos)[0]); pos += 4
                elif ch == "i":
                    vals.append(struct.unpack_from("<i", raw, pos)[0]); pos += 4
                elif ch == "f":
                    vals.append(struct.unpack_from("<f", raw, pos)[0]); pos += 4
                elif ch == "d":
                    vals.append(struct.unpack_from("<d", raw, pos)[0]); pos += 8
                elif ch == "Q":
                    vals.append(struct.unpack_from("<Q", raw, pos)[0]); pos += 8
                elif ch == "q":
                    vals.append(struct.unpack_from("<q", raw, pos)[0]); pos += 8
            t = vals[0]
            if isinstance(t, int) and yyyymmdd_ok(t):
                ds = f"{t//10000:04d}-{(t//100)%100:02d}-{t%100:02d}"
            elif isinstance(t, int) and unix_day_ok(t):
                if t > 1e12:
                    ds = datetime.fromtimestamp(t / 1000, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                else:
                    ds = datetime.fromtimestamp(t, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            else:
                ds = str(t)
            # assume ohlcv next
            floats = [float(v) for v in vals[1:] if isinstance(v, float)]
            bar = {"date": ds}
            if len(floats) >= 5:
                bar.update({
                    "open": round(floats[0], 2),
                    "high": round(floats[1], 2),
                    "low": round(floats[2], 2),
                    "close": round(floats[3], 2),
                    "volume": floats[4],
                })
            elif len(floats) >= 4:
                bar.update({
                    "open": round(floats[0], 2),
                    "high": round(floats[1], 2),
                    "low": round(floats[2], 2),
                    "close": round(floats[3], 2),
                })
            bars.append(bar)
        out["last_bars"] = bars
    return out


def main():
    result = {
        "main": analyze(MAIN),
        "mini": analyze(MINI),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MAIN best:", json.dumps(result["main"].get("best"), ensure_ascii=False, indent=2)[:800])
    print("MAIN last_bars:", json.dumps(result["main"].get("last_bars"), ensure_ascii=False, indent=2)[:800])
    print("MINI best:", json.dumps(result["mini"].get("best"), ensure_ascii=False, indent=2)[:400])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
