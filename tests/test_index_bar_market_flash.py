"""Runtime contract for top-index flash gating."""
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static" / "js" / "index-bar.js"


def _flash_helper_source():
    source = SOURCE.read_text(encoding="utf-8")
    match = re.search(
        r"function _idxQuoteShouldFlash\([^\n]+\) \{.*?\n\}",
        source,
        flags=re.DOTALL,
    )
    assert match, "missing _idxQuoteShouldFlash helper"
    return source, match.group(0)


def test_flash_only_occurs_for_changed_quotes_while_market_is_open():
    source, helper = _flash_helper_source()
    script = f"""
var _idxLastQuoteValues = new Map();
{helper}
const result = [
  _idxQuoteShouldFlash('SPX', '100.00', '0.10', true),
  _idxQuoteShouldFlash('SPX', '100.00', '0.10', true),
  _idxQuoteShouldFlash('SPX', '101.00', '0.20', true),
  _idxQuoteShouldFlash('SPX', '102.00', '0.30', false),
  _idxQuoteShouldFlash('SPX', '102.00', '0.30', true),
  _idxQuoteShouldFlash('SPX', '103.00', '0.40', true)
];
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [False, False, True, False, False, True]
    assert "v.market_open === true" in source
    assert "const shouldFlash = _idxQuoteShouldFlash(" in source
