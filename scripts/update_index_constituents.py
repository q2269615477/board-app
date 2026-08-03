#!/usr/bin/env python
"""Update local index constituent snapshots from Tushare ``index_weight``.

Examples:
    python scripts/update_index_constituents.py --dry-run
    python scripts/update_index_constituents.py --index sz399811
    python scripts/update_index_constituents.py --all

The script is an offline updater.  The web request path only reads the
SQLite snapshot and never invokes this script or Tushare.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.index_constituent_service import (  # noqa: E402
    IndexConstituentService,
    discover_indices,
    normalize_panel_code,
)


def _select(items: list[dict], target: str | None, update_all: bool) -> list[dict]:
    if not target or update_all:
        return items
    normalized = normalize_panel_code(target)
    return [
        item for item in items
        if item['panel_code'] == normalized
        or normalized in {
            normalize_panel_code(candidate)
            for candidate in item.get('source_candidates', [])
        }
    ]


def run_update(
    *,
    target: str | None = None,
    update_all: bool = False,
    dry_run: bool = False,
    classification_path: Path | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    service = IndexConstituentService(db_path=db_path)
    items = discover_indices(classification_path)
    selected = _select(items, target, update_all)
    if target and not selected:
        raise SystemExit(f'index not found in classification: {target}')

    results = []
    for item in selected:
        result = service.update_index(item, dry_run=dry_run)
        results.append(result)
        print(
            f"[{result['status']}] {result['panel_code']} "
            f"{result.get('name', '')} members={result.get('member_count', 0)} "
            f"as_of={result.get('as_of_date') or '-'}"
            + (f" error={result['error']}" if result.get('error') else '')
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description='更新本地指数成分股快照')
    parser.add_argument('--index', help='只更新一个面板代码，如 sz399811')
    parser.add_argument('--all', action='store_true', help='明确执行分类中的全部指数')
    parser.add_argument('--dry-run', action='store_true', help='只请求并验证，不写入 SQLite')
    parser.add_argument('--classification', type=Path, help='自定义分类 JSON（测试/诊断用）')
    parser.add_argument('--db', type=Path, help='自定义 SQLite 路径（测试/诊断用）')
    args = parser.parse_args()
    if not args.index and not args.all and not args.dry_run:
        parser.error('specify --index CODE or --all (use --dry-run for a full preview)')
    run_update(
        target=args.index,
        update_all=args.all,
        dry_run=args.dry_run,
        classification_path=args.classification,
        db_path=args.db,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
