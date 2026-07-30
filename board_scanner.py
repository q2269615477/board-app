"""
board_scanner.py — 板块批量周扫描器（独立模块，不影响现有面板架构）
============================
执行步骤：
1. 读取 static/board_classification.json（900+板块分类）
2. 对每个板块 GET http://127.0.0.1:5000/api/kline/industry/{code}
3. 调用 SR 算法，计算支撑/阻力位
4. 判断当前价格是否临近关键位（< 3%阈值）
5. 按临近程度排序输出 top-N 机会
6. 推送 SSE 通知面板

设计原则（零侵入）：
- 不修改任何现有 .py 文件（app.py / services/ / api/ 均不动）
- 仅依赖 Flask 已有的 kline_routes Blueprint 和 board_classification.json
- 可作为独立脚本执行（`python board_scanner.py`）
- 也可作为定时任务（WorkBuddy automation / cron）
"""

from __future__ import annotations
import json
import sys
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
import pandas as pd

# ============================================================
# 配置
# ============================================================
BASE_URL = "http://127.0.0.1:5000"
BASE_DIR = Path(__file__).resolve().parent
CLASSIFICATION_FILE = BASE_DIR / "static" / "board_classification.json"
REPORT_DIR = BASE_DIR / "reports"
PROXIES = {"http": None, "https": None}  # 本地请求，不走代理
REQUEST_TIMEOUT = 15  # 单板块K线请求超时
RATE_LIMIT_SEC = 0.05  # 20 QPS
SR_N_BARS = 60  # 只看最近60日K线找关键位
SR_ATR_MULTIPLIER = 1.5  # ATR倍数判断突破
SR_CLUSTER_ATR = 0.5  # 支撑/阻力聚类ATR容差
PROXIMITY_PCT = 3.0  # 距离关键位 < 3% 视为"临近"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scanner] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scanner")


# ============================================================
# 数据结构
# ============================================================
@dataclass
class SupportResistance:
    supports: list[float] = field(default_factory=list)
    resistances: list[float] = field(default_factory=list)


@dataclass
class ScanResult:
    code: str
    name: str
    category: str
    type_: str  # industry / concept
    current_price: float
    price_date: str
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    dist_to_support_pct: Optional[float] = None
    dist_to_resistance_pct: Optional[float] = None
    near_support: bool = False
    near_resistance: bool = False
    near_any_level: bool = False
    score: float = 0.0  # 临近程度评分（0-100，越高越值得关注）
    bars_scanned: int = 0
    error: Optional[str] = None


@dataclass
class ScanReport:
    scan_time: datetime
    total_boards: int
    scanned_boards: int
    near_support: list[ScanResult]
    near_resistance: list[ScanResult]
    all_results: list[ScanResult]


# ============================================================
# 支撑/阻力算法
# ============================================================
def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """平均真实波幅"""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def find_support_resistance(df: pd.DataFrame, n_bars: int = 60) -> SupportResistance:
    """
    基于ATR聚类的支撑/阻力识别。
    算法：
    1. 计算最近N根K线的局部高/低点
    2. 用ATR容差进行水平聚类
    3. 取聚类中心（交易量加权）
    """
    if df is None or len(df) < 20:
        return SupportResistance()

    recent = df.tail(n_bars).copy()
    atr = calc_atr(df)
    if atr == 0:
        return SupportResistance()

    # 局部高/低点
    recent["hh"] = recent["high"].rolling(5, center=True).max()
    recent["ll"] = recent["low"].rolling(5, center=True).min()
    recent["is_pivot_high"] = recent["high"] == recent["hh"]
    recent["is_pivot_low"] = recent["low"] == recent["ll"]

    pivot_highs = recent[recent["is_pivot_high"]][["high", "volume"]].values.tolist()
    pivot_lows = recent[recent["is_pivot_low"]][["low", "volume"]].values.tolist()

    # ATR 容差聚类
    def cluster_levels(pivots: list[list[float]], atr_mult: float) -> list[float]:
        if not pivots:
            return []
        threshold = atr * atr_mult
        clusters: list[list[list[float]]] = []
        for price, vol in pivots:
            placed = False
            for c in clusters:
                if abs(price - c[0][0]) < threshold:
                    c.append([price, vol])
                    placed = True
                    break
            if not placed:
                clusters.append([[price, vol]])
        # 取交易量加权平均
        levels = []
        for c in clusters:
            total_vol = sum(p[1] for p in c) or 1
            weighted = sum(p[0] * p[1] for p in c) / total_vol
            levels.append(round(weighted, 2))
        return sorted(levels)

    resistance_levels = cluster_levels(pivot_highs, SR_CLUSTER_ATR)
    support_levels = cluster_levels(pivot_lows, SR_CLUSTER_ATR)

    return SupportResistance(supports=support_levels, resistances=resistance_levels)


def calc_proximity(current: float, levels: list[float]) -> tuple[Optional[float], Optional[float]]:
    """当前价格距离最近关键位的百分比"""
    if not levels or current <= 0:
        return None, None
    nearest = min(levels, key=lambda lv: abs(lv - current))
    dist_pct = round((current - nearest) / current * 100, 2)
    return nearest, dist_pct


# ============================================================
# 数据获取层
# ============================================================
def load_classification() -> dict:
    """读取板块分类配置"""
    if not CLASSIFICATION_FILE.exists():
        raise FileNotFoundError(f"分类文件不存在: {CLASSIFICATION_FILE}")
    data = json.loads(CLASSIFICATION_FILE.read_text(encoding="utf-8"))
    return data


def fetch_kline(code: str, type_: str = "industry") -> Optional[pd.DataFrame]:
    """请求本地K线API"""
    url = f"{BASE_URL}/api/kline/{type_}/{code}"
    try:
        resp = requests.get(
            url, params={"period": "daily"},
            proxies=PROXIES, timeout=REQUEST_TIMEOUT
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("error") or data.get("loading"):
            return None
        rows = data.get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows)
        return df
    except Exception as e:
        log.debug(f"K线请求失败 {code}: {e}")
        return None


# ============================================================
# 核心扫描逻辑
# ============================================================
def scan_one_board(
    code: str, name: str, category: str, type_: str
) -> ScanResult:
    """扫描单个板块，返回 SR 分析结果"""
    result = ScanResult(code=code, name=name, category=category, type_=type_)

    df = fetch_kline(code, type_)
    if df is None or len(df) < 20:
        result.error = "kline_unavailable"
        return result

    result.bars_scanned = len(df)
    current_price = float(df["close"].iloc[-1])
    result.current_price = current_price
    # 取日期：优先 date 列，否则用最后索引
    if "date" in df.columns:
        result.price_date = str(df["date"].iloc[-1])[:10]
    else:
        result.price_date = str(df.index[-1])[:10]

    # 计算支撑/阻力
    sr = find_support_resistance(df, n_bars=SR_N_BARS)
    if not sr.supports and not sr.resistances:
        return result

    # 距离最近支撑
    if sr.supports:
        ns, ds = calc_proximity(current_price, sr.supports)
        result.nearest_support = ns
        result.dist_to_support_pct = ds
        # 价格在支撑上方且 < PROXIMITY_PCT
        if ds is not None and 0 < ds < PROXIMITY_PCT:
            result.near_support = True
        elif ds is not None and ds <= 0:
            # 价格已跌破支撑
            result.near_support = False

    # 距离最近阻力
    if sr.resistances:
        nr, dr = calc_proximity(current_price, sr.resistances)
        result.nearest_resistance = nr
        result.dist_to_resistance_pct = dr
        if dr is not None and -PROXIMITY_PCT < dr < 0:
            result.near_resistance = True
        elif dr is not None and dr >= 0:
            # 价格已突破阻力
            result.near_resistance = False

    result.near_any_level = result.near_support or result.near_resistance

    # 评分：临近程度（0-100）
    score = 0.0
    if result.near_support and result.dist_to_support_pct is not None:
        # 距支撑越近分数越高
        score += max(0, 50 - result.dist_to_support_pct * 15)
    if result.near_resistance and result.dist_to_resistance_pct is not None:
        # 距阻力越近（负值）分数越高
        score += max(0, 50 - abs(result.dist_to_resistance_pct) * 15)
    result.score = round(min(score, 100), 1)

    return result


def run_scan(top_n: int = 30) -> ScanReport:
    """执行全量扫描"""
    log.info("=" * 60)
    log.info("板块周扫描启动")
    log.info("=" * 60)

    cls_data = load_classification()
    categories = cls_data.get("categories", [])
    if not categories:
        categories = cls_data if isinstance(cls_data, list) else []

    # 收集所有板块
    all_boards: list[tuple[str, str, str, str]] = []  # (code, name, category, type)
    for cat in categories:
        cat_name = cat.get("name", "未分类")
        for board in cat.get("boards", []):
            all_boards.append((
                board["code"], board["name"], cat_name, board.get("type", "industry")
            ))

    total = len(all_boards)
    log.info(f"共 {total} 个板块待扫描")

    results: list[ScanResult] = []
    scanned = 0
    for i, (code, name, cat, type_) in enumerate(all_boards):
        if (i + 1) % 100 == 0:
            log.info(f"进度: {i+1}/{total}")
        result = scan_one_board(code, name, cat, type_)
        results.append(result)
        if result.bars_scanned > 0:
            scanned += 1
        time.sleep(RATE_LIMIT_SEC)

    # 分类汇总
    near_sup = [r for r in results if r.near_support]
    near_res = [r for r in results if r.near_resistance]
    near_sup.sort(key=lambda r: r.score, reverse=True)
    near_res.sort(key=lambda r: r.score, reverse=True)

    report = ScanReport(
        scan_time=datetime.now(),
        total_boards=total,
        scanned_boards=scanned,
        near_support=near_sup[:top_n],
        near_resistance=near_res[:top_n],
        all_results=results,
    )

    log.info(f"扫描完成: {scanned}/{total} 板块有数据")
    log.info(f"临近支撑: {len(near_sup)} 个")
    log.info(f"临近阻力: {len(near_res)} 个")
    return report


# ============================================================
# 报告输出
# ============================================================
def format_report(report: ScanReport) -> str:
    """生成 Markdown 格式报告"""
    lines = [
        f"# 板块周扫描报告",
        f"",
        f"**扫描时间**: {report.scan_time.strftime('%Y-%m-%d %H:%M')}",
        f"**扫描板块**: {report.scanned_boards}/{report.total_boards}",
        f"**参数**: SR_N_BARS={SR_N_BARS}, ATR_MULT={SR_ATR_MULTIPLIER}, "
        f"CLUSTER_ATR={SR_CLUSTER_ATR}, PROXIMITY={PROXIMITY_PCT}%",
        f"",
        f"---",
        f"",
        f"## 🟢 临近支撑 TOP-{len(report.near_support)}",
        f"",
        f"| 排名 | 板块 | 分类 | 现价 | 距离支撑 | 距离% | 评分 |",
        f"|------|------|------|------|----------|-------|------|",
    ]
    for i, r in enumerate(report.near_support, 1):
        lines.append(
            f"| {i} | [{r.code}](http://127.0.0.1:5000/?code={r.code}) "
            f"| {r.category} | {r.current_price:.2f} "
            f"| {r.nearest_support} | {r.dist_to_support_pct:+.2f}% | {r.score} |"
        )

    lines += [
        f"",
        f"## 🔴 临近阻力 TOP-{len(report.near_resistance)}",
        f"",
        f"| 排名 | 板块 | 分类 | 现价 | 距离阻力 | 距离% | 评分 |",
        f"|------|------|------|------|----------|-------|------|",
    ]
    for i, r in enumerate(report.near_resistance, 1):
        lines.append(
            f"| {i} | [{r.code}](http://127.0.0.1:5000/?code={r.code}) "
            f"| {r.category} | {r.current_price:.2f} "
            f"| {r.nearest_resistance} | {r.dist_to_resistance_pct:+.2f}% | {r.score} |"
        )

    lines += [
        f"",
        f"## 📊 统计摘要",
        f"",
        f"- 总板块: {report.total_boards}",
        f"- 有数据: {report.scanned_boards}",
        f"- 临近支撑: {len(report.near_support)}",
        f"- 临近阻力: {len(report.near_resistance)}",
        f"- 无数据: {report.total_boards - report.scanned_boards}",
    ]

    return "\n".join(lines)


def save_report(report: ScanReport) -> Path:
    """保存报告到 reports/ 目录"""
    REPORT_DIR.mkdir(exist_ok=True)
    date_str = report.scan_time.strftime("%Y-%m-%d")
    md_path = REPORT_DIR / f"weekly_scan_{date_str}.md"
    md_path.write_text(format_report(report), encoding="utf-8")
    log.info(f"报告已保存: {md_path}")
    return md_path


# ============================================================
# SSE 推送（可选）
# ============================================================
def push_to_panel(report: ScanReport):
    """通过 /api/skill/result 推送扫描结果到面板"""
    payload = {
        "board_code": "scanner",
        "skill_id": "weekly_scan",
        "overlay": [],
        "report": format_report(report),
        "summary": (
            f"周扫描完成: {report.scanned_boards}/{report.total_boards} 板块, "
            f"发现 {len(report.near_support)} 个临近支撑, "
            f"{len(report.near_resistance)} 个临近阻力"
        ),
    }
    try:
        requests.post(
            f"{BASE_URL}/api/skill/result",
            json=payload, proxies=PROXIES, timeout=5
        )
        log.info("SSE 推送成功")
    except Exception as e:
        log.warning(f"SSE 推送失败: {e}")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    report = run_scan(top_n=30)
    save_report(report)
    push_to_panel(report)
    print("\n" + format_report(report))
