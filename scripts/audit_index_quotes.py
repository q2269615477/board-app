#!/usr/bin/env python3
"""Read-only audit of static and prewarm index quote coverage."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import PREWARM_TARGETS
from data_loader import get_global_index_spot, get_spot_index

EXTRA_GLOBAL_TARGETS = (
    ('^N225', '日经225'), ('^KS11', 'KOSPI'), ('^TWII', '台湾加权'),
    ('SPX', '标普500'), ('IXIC', '纳斯达克'), ('DJI', '道琼斯'),
)


def _walk(value):
    if isinstance(value, dict):
        if value.get('type') == 'index' and value.get('code'):
            yield {
                'code': str(value['code']),
                'name': str(value.get('name') or value['code']),
                'source_set': 'classification',
            }
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def discover_indices(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = list(_walk(data))
    for code, name, _typ in PREWARM_TARGETS:
        if _is_index_code(code):
            rows.append({'code': code, 'name': name, 'source_set': 'prewarm'})
    for code, name in EXTRA_GLOBAL_TARGETS:
        rows.append({'code': code, 'name': name, 'source_set': 'nav_global'})
    unique = {}
    for row in rows:
        unique[row['code']] = {
            **unique.get(row['code'], {}),
            **row,
        }
    return list(unique.values())


def _is_index_code(code):
    key = str(code or '').lower()
    return key == '800000' or key.startswith(('sh', 'sz', 'bj')) or key in {
        'hsi', 'hstech', '^n225', '^ks11', '^twii', 'spx', 'ixic', 'dji'
    }


def _fetch(row):
    code = row['code']
    data = get_spot_index(code) if _is_a_share(code) else get_global_index_spot(code)
    return row, data or {}


def _is_a_share(code):
    key = str(code or '').lower()
    return key.startswith(('sh', 'sz', 'bj')) and len(key) == 8


def _audit_row(row, data):
    if not data:
        return 'empty', {**row, 'reason': 'no_quote'}
    if data.get('unavailable'):
        return 'inactive', {
            **row,
            'status': 'deprecated',
            'reason': data.get('reason', 'unavailable'),
        }
    try:
        price = float(data.get('price') or data.get('close') or 0)
        change_pct = data.get('change_pct')
        if change_pct is None:
            change_pct = data.get('changePct')
        change_pct = float(change_pct)
    except (TypeError, ValueError):
        return 'error', {**row, 'reason': 'invalid_quote'}
    if price <= 0:
        return 'error', {**row, 'reason': 'price_not_positive'}
    previous = data.get('pre_close')
    if previous is None and data.get('change') is not None:
        try:
            previous = price - float(data['change'])
        except (TypeError, ValueError):
            previous = None
    if previous is None:
        return 'error', {**row, 'reason': 'previous_missing'}
    try:
        previous = float(previous)
        expected = (price - previous) / previous * 100 if previous else 0
    except (TypeError, ValueError):
        return 'error', {**row, 'reason': 'previous_invalid'}
    if abs(expected - change_pct) > 0.08:
        return 'error', {**row, 'reason': 'change_pct_mismatch',
                         'expected': round(expected, 4), 'actual': change_pct}
    result = {**row, 'channel': data.get('channel', 'unknown'), 'price': price,
              'change_pct': change_pct}
    if data.get('stale') or data.get('channel') in ('sqlite', 'local_close'):
        return 'stale_local', result
    return 'checked', result


def audit(args):
    rows = discover_indices(args.classification)
    buckets = {'empty': [], 'error': [], 'stale_local': [], 'inactive': []}
    checked = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_fetch, row) for row in rows]
        for future in as_completed(futures):
            row, data = future.result()
            bucket, result = _audit_row(row, data)
            if bucket == 'checked':
                checked += 1
            else:
                buckets[bucket].append(result)
    report = {
        'total': len(rows),
        'checked': checked,
        'active_total': len(rows) - len(buckets['inactive']),
        'empty': sorted(buckets['empty'], key=lambda x: x['code']),
        'error': sorted(buckets['error'], key=lambda x: x['code']),
        'stale_local': sorted(buckets['stale_local'], key=lambda x: x['code']),
        'inactive': sorted(buckets['inactive'], key=lambda x: x['code']),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['empty'] or report['error'] else 0


def build_parser():
    parser = argparse.ArgumentParser(description='只读审计全量指数现货行情')
    parser.add_argument('--classification', type=Path,
                        default=ROOT / 'static' / 'board_classification.json')
    parser.add_argument('--workers', type=int, default=12)
    return parser


if __name__ == '__main__':
    raise SystemExit(audit(build_parser().parse_args()))
