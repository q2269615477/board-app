# -*- coding: utf-8 -*-
"""
report_tag_quality.py — Tags 精修质量报告

读取 static/board_classification.json，统计 tags 分布与质量指标。
兼容 v5 (categories[].subcategories[].boards[]) 与旧版 (cat.boards) 结构。

输出字段（全部 ASCII 字段名）：
  - total_boards           板块总数
  - tag_total              tag 出现总次数（含重复）
  - unique_tags            去重 tag 数
  - single_use_tags        仅出现 1 次的 tag 数
  - single_use_ratio       单例 tag 占比 (0.0-1.0)
  - boards_with_1_tag      仅有 1 个 tag 的板块数
  - boards_with_1_tag_ratio
  - top_tags               出现次数 top 15 的 tag
  - generic_tag_candidates 过于宽泛的 tag 候选
  - single_char_tags       单字符 tag 列表
  - long_tags              长度 > 6 的 tag 列表
  - tag_count_distribution 每板块 tag 数量分布

用法:
  python scripts/report_tag_quality.py            # 仅报告，返回码 0
  python scripts/report_tag_quality.py --strict   # 严格模式，质量不达标返回非 0

strict 阈值:
  - single_use_ratio > 35%  -> exit 1
  - boards_with_1_tag > 20% -> exit 2
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SRC = Path("static/board_classification.json")

# 过于宽泛、信息量低的 tag 候选集合
GENERIC_TAG_CANDIDATES = {
    "材料", "服务", "设备", "行业", "概念", "主题题材", "其他",
    "相关", "板块", "板块概念", "综合", "其它",
}

# 长度阈值
LONG_TAG_MIN_LEN = 6


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_boards(data: dict) -> list:
    """展平所有 board，兼容 v5 subcategories 与旧版 cat.boards 混合结构"""
    boards = []
    for cat in data.get("categories", []):
        # v5: categories[].subcategories[].boards[]
        for sub in cat.get("subcategories", []):
            for b in sub.get("boards", []):
                boards.append(b)
        # 旧版: categories[].boards[]
        for b in cat.get("boards", []):
            boards.append(b)
    return boards


def analyze_tags(boards: list) -> dict:
    """分析 tags 质量，返回 ASCII 字段名的报告字典"""
    all_tags = []           # 所有 tag 出现（含重复）
    tag_count_per_board = []  # 每个 board 的 tag 数量
    single_char_tags = set()
    long_tags = set()
    generic_found = set()

    for b in boards:
        tags = b.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        # 去重、非空
        seen = set()
        deduped = []
        for t in tags:
            if not isinstance(t, str):
                continue
            t = t.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            deduped.append(t)
            all_tags.append(t)

            # 单字符检测
            if len(t) == 1:
                single_char_tags.add(t)
            # 长 tag 检测
            if len(t) > LONG_TAG_MIN_LEN:
                long_tags.add(t)
            # 宽泛 tag 检测
            if t in GENERIC_TAG_CANDIDATES:
                generic_found.add(t)

        tag_count_per_board.append(len(deduped))

    tag_counter = Counter(all_tags)
    unique_tags = list(tag_counter.keys())
    single_use_tags = [t for t, c in tag_counter.items() if c == 1]

    boards_with_1_tag = sum(1 for n in tag_count_per_board if n == 1)

    total_boards = len(boards)
    tag_total = len(all_tags)
    unique_count = len(unique_tags)
    single_use_count = len(single_use_tags)

    single_use_ratio = single_use_count / unique_count if unique_count else 0.0
    boards_with_1_tag_ratio = boards_with_1_tag / total_boards if total_boards else 0.0

    # top tags
    top_tags = tag_counter.most_common(15)

    # tag count distribution
    count_dist = Counter(tag_count_per_board)

    return {
        "total_boards": total_boards,
        "tag_total": tag_total,
        "unique_tags": unique_count,
        "single_use_tags": single_use_count,
        "single_use_ratio": round(single_use_ratio, 4),
        "boards_with_1_tag": boards_with_1_tag,
        "boards_with_1_tag_ratio": round(boards_with_1_tag_ratio, 4),
        "top_tags": [(t, c) for t, c in top_tags],
        "generic_tag_candidates": sorted(generic_found),
        "single_char_tags": sorted(single_char_tags),
        "long_tags": sorted(long_tags),
        "tag_count_distribution": dict(sorted(count_dist.items())),
    }


def format_report(report: dict) -> str:
    """格式化为人类可读的 ASCII 报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("  Tag Quality Report")
    lines.append("=" * 60)
    lines.append(f"  total_boards             = {report['total_boards']}")
    lines.append(f"  tag_total                = {report['tag_total']}")
    lines.append(f"  unique_tags              = {report['unique_tags']}")
    lines.append(f"  single_use_tags          = {report['single_use_tags']}")
    lines.append(f"  single_use_ratio         = {report['single_use_ratio']:.2%}")
    lines.append(f"  boards_with_1_tag        = {report['boards_with_1_tag']}")
    lines.append(f"  boards_with_1_tag_ratio  = {report['boards_with_1_tag_ratio']:.2%}")
    lines.append("-" * 60)
    lines.append("  top_tags (tag: count):")
    for tag, cnt in report["top_tags"]:
        lines.append(f"    {tag:<20s} {cnt}")
    lines.append("-" * 60)
    lines.append(f"  generic_tag_candidates   = {report['generic_tag_candidates']}")
    lines.append(f"  single_char_tags         = {report['single_char_tags']}")
    lines.append(f"  long_tags (>6 chars)     = {report['long_tags']}")
    lines.append("-" * 60)
    lines.append("  tag_count_distribution (n_tags: n_boards):")
    for n_tags, n_boards in report["tag_count_distribution"].items():
        lines.append(f"    {n_tags} tags -> {n_boards} boards")
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Tag quality report for board classification")
    parser.add_argument("--strict", action="store_true",
                        help="Strict mode: exit non-zero if quality thresholds violated")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of formatted report")
    args = parser.parse_args()

    if not SRC.exists():
        print(f"[ERROR] source file not found: {SRC}", file=sys.stderr)
        sys.exit(1)

    data = load_json(SRC)
    boards = flatten_boards(data)
    report = analyze_tags(boards)

    if args.json:
        # JSON 输出：确保中文 tag 内容不崩溃
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    if args.strict:
        if report["single_use_ratio"] > 0.35:
            print("\n[STRICT FAIL] single_use_ratio > 35%", file=sys.stderr)
            sys.exit(1)
        if report["boards_with_1_tag_ratio"] > 0.20:
            print("\n[STRICT FAIL] boards_with_1_tag_ratio > 20%", file=sys.stderr)
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
