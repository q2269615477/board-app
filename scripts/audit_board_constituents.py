#!/usr/bin/env python3
"""Read-only audit for board constituent coverage and Eastmoney fallback."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.board_api import get_eastmoney_constituents


def _walk_boards(value):
    if isinstance(value, dict):
        if value.get('type') in ('industry', 'concept') and value.get('code'):
            yield {
                'board_type': value['type'],
                'code': str(value['code']),
                'name': str(value.get('name') or value['code']),
            }
        for child in value.values():
            yield from _walk_boards(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_boards(child)


def discover_boards(classification_path):
    data = json.loads(Path(classification_path).read_text(encoding='utf-8'))
    unique = {}
    for row in _walk_boards(data):
        unique[(row['board_type'], row['code'])] = row
    return list(unique.values())


def load_local_maps(root=ROOT):
    result = {}
    for board_type in ('industry', 'concept'):
        path = Path(root) / 'data' / f'{board_type}_constituents.json'
        result[board_type] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return result


def _classify_remote_result(row, has_members, error, covered_keys):
    key = (row['board_type'], row['code'])
    if error:
        return 'error', {**row, 'error': error}
    if has_members:
        return None, row
    if key in covered_keys:
        return 'remote_empty_but_local_covered', row
    return 'empty', row


def audit(args):
    boards = discover_boards(args.classification)
    local = load_local_maps(ROOT)
    covered = [
        row for row in boards
        if (local.get(row['board_type'], {}).get(
            f"{row['board_type']}:{row['code']}", {}
        ).get('cons') or [])
    ]
    covered_keys = {(row['board_type'], row['code']) for row in covered}
    missing = [row for row in boards if (row['board_type'], row['code']) not in covered_keys]
    targets = boards if args.remote_all else missing
    empty = []
    errors = []
    remote_empty_but_local_covered = []

    def check(row):
        try:
            result = get_eastmoney_constituents(row['board_type'], row['code'])
            return row, bool(result), None
        except Exception as exc:  # defensive: the client itself fails closed
            return row, False, str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check, row) for row in targets]
        for future in as_completed(futures):
            row, has_members, error = future.result()
            category, item = _classify_remote_result(
                row, has_members, error, covered_keys
            )
            if category == 'error':
                errors.append(item)
            elif category == 'empty':
                empty.append(item)
            elif category == 'remote_empty_but_local_covered':
                remote_empty_but_local_covered.append(item)

    report = {
        'total': len(boards),
        'local_covered': len(covered),
        'local_missing': len(missing),
        'remote_scope': 'all' if args.remote_all else 'missing',
        'remote_checked': len(targets),
        'empty': sorted(empty, key=lambda x: (x['board_type'], x['code'])),
        'error': sorted(errors, key=lambda x: (x['board_type'], x['code'])),
        'remote_empty_but_local_covered': sorted(
            remote_empty_but_local_covered,
            key=lambda x: (x['board_type'], x['code']),
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    fatal_error = any(
        (item['board_type'], item['code']) not in covered_keys
        for item in errors
    )
    return 1 if empty or fatal_error else 0


def build_parser():
    parser = argparse.ArgumentParser(description='只读审计板块成分覆盖及东财兜底')
    parser.add_argument(
        '--classification',
        type=Path,
        default=ROOT / 'static' / 'board_classification.json',
        help='板块分类 JSON 路径',
    )
    parser.add_argument(
        '--remote-all', action='store_true',
        help='对全部板块调用东财接口，而不是只巡检本地缺失项',
    )
    parser.add_argument('--workers', type=int, default=12, help='并发请求数')
    return parser


if __name__ == '__main__':
    parsed = build_parser().parse_args()
    raise SystemExit(audit(parsed))
