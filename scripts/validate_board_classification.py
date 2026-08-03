# -*- coding: utf-8 -*-
"""
validate_board_classification.py — 板块分类 v5.0 校验脚本

校验规则:
  1. JSON 格式合法
  2. version == "5.0"
  3. 每个 board 必须包含 code, name, type, primary_category, secondary_category, tags
  4. code 唯一（无重复）
  5. name 非空
  6. primary_category 非空且在预期一级分类集合内
  7. secondary_category 非空
  8. tags 为列表
  9. 无 "待人工确认" / "待分类" 残留
 10. 二级分类板块数 ≤ 30（OVERSIZE 检查）
 11. 二级分类板块数 ≥ 3（SMALL 检查，仅警告）
 12. 板块总数与 v4.1 备份一致

用法:
  python scripts/validate_board_classification.py
"""
import argparse
import json
import locale
import sys
from pathlib import Path
from collections import Counter, defaultdict

# --- Encoding fix: ensure stdout/stderr use the system preferred encoding ---
# On Windows with Python 3.14+, stdout defaults to UTF-8, but
# subprocess.run(text=True) reads with locale.getpreferredencoding (cp936/gbk).
# This causes UnicodeDecodeError when Chinese UTF-8 bytes hit the gbk decoder.
# Fix: reconfigure streams to match what subprocess expects.
try:
    _enc = locale.getpreferredencoding(False) or "utf-8"
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding=_enc, errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding=_enc, errors="replace")
except Exception:
    pass

SRC = Path("static/board_classification.json")
BACKUP = Path("static/board_classification.v4.1.backup.json")
SEARCH_INDEX = Path("static/search_index.json")

EXPECTED_PRIMARIES = {
    "AI 与数字科技",
    "智能终端与电子制造",
    "先进制造与军工装备",
    "新能源与电力设备",
    "周期资源与材料",
    "医药生物与健康",
    "消费与服务",
    "金融地产与基建",
    "公用事业与交通运输",
    "农业与乡村振兴",
    "主题、风格与事件",
}

REQUIRED_FIELDS = {"code", "name", "type", "primary_category",
                   "secondary_category", "tags"}
MAX_BOARDS_PER_SUB = 30
MIN_BOARDS_PER_SUB = 3
MIN_TAGS = 2
MAX_TAGS = 6
MAX_SINGLE_USE_TAG_RATIO = 0.35
MAX_BOARDS_WITH_ONE_TAG_RATIO = 0.20
GENERIC_TAGS = {"材料", "服务", "设备", "行业", "概念", "主题题材", "其他"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_boards(data):
    """展平所有 board，兼容 v5 subcategories 与旧版 boards 混合结构"""
    boards = []
    for cat in data.get("categories", []):
        for sub in cat.get("subcategories", []):
            for b in sub.get("boards", []):
                boards.append(b)
        for b in cat.get("boards", []):
            boards.append(b)
    return boards


def flatten_backup_boards(data):
    """展平 v4.1 备份中的 board（结构不同）"""
    boards = []
    for cat in data.get("categories", []):
        for sub in cat.get("subcategories", []):
            for b in sub.get("boards", []):
                boards.append(b)
    return boards


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default=str(SRC), help="分类 JSON 路径")
    parser.add_argument("--strict-tags", action="store_true", help="将 tags 质量阈值警告升级为错误")
    args = parser.parse_args()
    src = Path(args.path)
    errors = []
    warnings = []

    # 1. JSON 格式
    try:
        data = load_json(src)
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON parse error: {e}", flush=True)
        sys.exit(1)

    # 2. version
    ver = data.get("version", "")
    is_v5 = ver in ("5.0", "5.1")
    if not ver:
        warnings.append("version 字段缺失（非 v5 格式，跳过 v5 专属检查）")
    elif not is_v5:
        warnings.append(f"version 为 '{ver}'，非 5.x（跳过 v5 专属检查）")

    # 3. 展平所有 board
    boards = flatten_boards(data)
    total = len(boards)

    # Detect whether data has v5 fields (primary_category, tags, etc.)
    sample = boards[0] if boards else {}
    has_v5_fields = "primary_category" in sample or "tags" in sample

    # 4. code 唯一性
    codes = [b.get("code", "") for b in boards]
    dup_codes = [c for c, n in Counter(codes).items() if n > 1]
    if dup_codes:
        errors.append(f"duplicate code: {dup_codes[:10]}")

    # 5-9. 逐个检查 board
    bad_primary = set()
    unassigned_count = 0
    tag_counter = Counter()
    boards_with_one_tag = 0
    empty_tags = 0
    duplicate_tags_inside_board = 0
    for b in boards:
        # name 非空
        if not b.get("name"):
            errors.append(f"{b.get('code', '?')} name is empty")

        if has_v5_fields:
            # v5 format: check required fields strictly
            missing = REQUIRED_FIELDS - set(b.keys())
            if missing:
                errors.append(f"{b.get('code', '?')} missing fields: {missing}")
                continue

            # primary_category check
            pc = b.get("primary_category", "")
            if not pc:
                errors.append(f"{b.get('code', '?')} primary_category is empty")
            elif pc == "待人工确认":
                unassigned_count += 1
            elif pc not in EXPECTED_PRIMARIES:
                bad_primary.add(pc)

            # secondary_category check
            sc = b.get("secondary_category", "")
            if not sc:
                errors.append(f"{b.get('code', '?')} secondary_category is empty")
            elif sc == "待分类":
                unassigned_count += 1

            # tags quality check
            tags = b.get("tags")
            if not isinstance(tags, list):
                errors.append(f"{b.get('code', '?')} tags is not a list")
                continue
            cleaned_tags = []
            for t in tags:
                if not isinstance(t, str) or not t.strip():
                    errors.append(f"{b.get('code', '?')} {b.get('name', '')} invalid tag: {tags}")
                    continue
                cleaned_tags.append(t.strip())
            tag_counter.update(cleaned_tags)
            if not cleaned_tags:
                empty_tags += 1
                errors.append(f"{b.get('code', '?')} {b.get('name', '')} tags is empty")
            if len(cleaned_tags) == 1:
                boards_with_one_tag += 1
            if len(cleaned_tags) < MIN_TAGS:
                warnings.append(f"{b.get('code', '?')} {b.get('name', '')} tags too few: {cleaned_tags}")
            if len(cleaned_tags) > MAX_TAGS:
                errors.append(f"{b.get('code', '?')} {b.get('name', '')} tags too many: {cleaned_tags}")
            if len(cleaned_tags) != len(set(cleaned_tags)):
                duplicate_tags_inside_board += 1
                errors.append(f"{b.get('code', '?')} {b.get('name', '')} tags duplicated: {cleaned_tags}")
            if len(cleaned_tags) == 1 and cleaned_tags[0] in GENERIC_TAGS:
                warnings.append(f"{b.get('code', '?')} {b.get('name', '')} only generic tag: {cleaned_tags}")

    if has_v5_fields:
        if bad_primary:
            errors.append(f"unknown primary category: {bad_primary}")
        if unassigned_count > 0:
            warnings.append(f"{unassigned_count} boards unassigned")

    single_use_tags = [t for t, n in tag_counter.items() if n == 1]
    single_use_ratio = len(single_use_tags) / max(len(tag_counter), 1)
    one_tag_ratio = boards_with_one_tag / max(total, 1)
    tag_quality_messages = []
    if single_use_ratio > MAX_SINGLE_USE_TAG_RATIO:
        tag_quality_messages.append(
            f"single_use_tag_ratio too high: {single_use_ratio:.1%} > {MAX_SINGLE_USE_TAG_RATIO:.0%}"
        )
    if one_tag_ratio > MAX_BOARDS_WITH_ONE_TAG_RATIO:
        tag_quality_messages.append(
            f"boards_with_1_tag ratio too high: {one_tag_ratio:.1%} > {MAX_BOARDS_WITH_ONE_TAG_RATIO:.0%}"
        )
    if args.strict_tags:
        errors.extend(tag_quality_messages)
    else:
        warnings.extend(tag_quality_messages)

    # 10-11. 二级分类大小检查 (only when v5 fields present)
    sub_counts = defaultdict(int)
    sub_to_primary = {}
    if has_v5_fields:
        for b in boards:
            key = (b.get("primary_category", ""), b.get("secondary_category", ""))
            sub_counts[key] += 1
            sub_to_primary[key[1]] = key[0]

        for (pc, sc), count in sorted(sub_counts.items()):
            if count > MAX_BOARDS_PER_SUB:
                errors.append(f"OVERSIZE: {pc}/{sc} = {count} (>{MAX_BOARDS_PER_SUB})")
            if count < MIN_BOARDS_PER_SUB:
                warnings.append(f"SMALL: {pc}/{sc} = {count} (<{MIN_BOARDS_PER_SUB})")

    # 12. 板块总数对比
    try:
        backup = load_json(BACKUP)
        backup_boards = flatten_backup_boards(backup)
        backup_codes = set(b.get("code", "") for b in backup_boards)
        new_codes = set(codes)
        missing = backup_codes - new_codes
        extra = new_codes - backup_codes
        if missing and len(missing) > 5:
            errors.append(f"compared to v4.1, {len(missing)} boards missing: {list(missing)[:10]}")
        elif missing:
            warnings.append(f"compared to v4.1, {len(missing)} board(s) missing: {list(missing)[:10]}")
        if extra:
            warnings.append(f"compared to v4.1, {len(extra)} extra board(s): {list(extra)[:10]}")
        if len(backup_codes) != total:
            warnings.append(f"total count mismatch: v4.1={len(backup_codes)} v5.0={total}")
    except FileNotFoundError:
        warnings.append("v4.1 backup not found, skipping count comparison")

    # 13. 搜索索引同步检查（存在时检查，不阻塞首次生成）
    if src == SRC and SEARCH_INDEX.exists():
        try:
            search_items = load_json(SEARCH_INDEX).get("items", {})
            missing_search_tags = []
            for b in boards:
                item = search_items.get(b.get("code", ""))
                if not item or "tags" not in item:
                    missing_search_tags.append(b.get("code", ""))
            if missing_search_tags:
                warnings.append(f"search_index missing tags field: {len(missing_search_tags)} boards, e.g. {missing_search_tags[:10]}")
        except Exception as e:
            warnings.append(f"search_index check failed: {e}")

    # === Output Report (ASCII-safe for subprocess compatibility) ===
    print("=" * 60, flush=True)
    print(f"board classification v5.0 validation report", flush=True)
    print("=" * 60, flush=True)
    print(f"  total boards: {total}", flush=True)
    print(f"  primary categories: {len(set(b.get('primary_category','') for b in boards))}", flush=True)
    print(f"  secondary categories: {len(sub_counts)}", flush=True)
    print(f"  unassigned: {unassigned_count}", flush=True)
    print(f"  tag total: {len(tag_counter)}", flush=True)
    print(f"  single-use tags: {len(single_use_tags)} ({single_use_ratio:.1%})", flush=True)
    print(f"  boards with 1 tag: {boards_with_one_tag} ({one_tag_ratio:.1%})", flush=True)
    print(f"  empty tags: {empty_tags}", flush=True)
    print(f"  duplicate tags inside board: {duplicate_tags_inside_board}", flush=True)
    print(flush=True)

    if warnings:
        print(f"[WARN] {len(warnings)} warning(s):", flush=True)
        for w in warnings:
            print(f"  {w}", flush=True)
        print(flush=True)

    if errors:
        print(f"[FAIL] {len(errors)} error(s):", flush=True)
        for e in errors:
            print(f"  {e}", flush=True)
        print(flush=True)
        sys.exit(1)
    else:
        print("[PASS] classification validation OK", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
