#!/usr/bin/env python
"""Repair a bounded full-history slice from QMT HTTP 18080."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.history_repair_service import repair_history_batch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    result = repair_history_batch(limit=args.limit, reset=args.reset)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
