"""Contract tests for the read-only data-asset diagnostic command."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_data_assets.ps1"


def _powershell() -> str:
    for executable in ("pwsh", "powershell"):
        path = shutil.which(executable)
        if path:
            return path
    pytest.skip("PowerShell is required for the data-asset diagnostic contract")


def _run(project: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        _powershell(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-ProjectRoot",
        str(project),
        "-DataDir",
        "data",
        "-SearchIndexPath",
        "static/search_index.json",
        "-ClassificationPath",
        "static/board_classification.json",
        "-BackupRoot",
        "data/backup",
        *extra,
    ]
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _write_sqlite(path: Path, *, include_kline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        if include_kline:
            connection.execute(
                "CREATE TABLE kline_meta (code TEXT, period TEXT, rows INTEGER, "
                "first_date TEXT, last_date TEXT, updated_at TEXT)"
            )
            connection.execute(
                "INSERT INTO kline_meta VALUES (?, ?, ?, ?, ?, ?)",
                ("603259", "daily", 2, "2026-01-01", "2026-01-02", "20260102 120000"),
            )
            # This deliberately large-named table lets the diagnostic prove it
            # does not issue a full COUNT against a main kline table.
            connection.execute("CREATE TABLE kline (code TEXT, date TEXT, close REAL)")
        connection.commit()


def _fixture(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    data = project / "data"
    static = project / "static"
    backup = data / "backup" / "snapshot"
    for filename in (
        "kline.db",
        "stock_data.db",
        "annotation_index.sqlite",
        "session_index.sqlite",
    ):
        _write_sqlite(data / filename, include_kline=filename == "kline.db")

    static.mkdir(parents=True, exist_ok=True)
    search_index = {
        "version": 2,
        "built_at": "2099-01-01 00:00:00",
        "total": 1,
        "items": {
            "603259": {
                "name": "药明康德",
                "type": "stock",
                "category": "医药",
                "initials": ["YMKD"],
                "tags": [],
            }
        },
    }
    (static / "search_index.json").write_text(
        json.dumps(search_index, ensure_ascii=False), encoding="utf-8"
    )
    classification = {
        "version": "5.1-panel",
        "categories": [
            {
                "name": "医药",
                "subcategories": [
                    {"name": "个股", "boards": [{"code": "603259", "name": "药明康德"}]}
                ],
            }
        ],
    }
    (static / "board_classification.json").write_text(
        json.dumps(classification, ensure_ascii=False), encoding="utf-8"
    )
    (project / "build_search_index.py").write_text("# fixture rebuild marker\n", encoding="utf-8")
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(data / "kline.db", backup / "kline.db")
    (backup / "BACKUP_INFO.txt").write_text("snapshot fixture\n", encoding="utf-8")
    return project


def _snapshot(path: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for item in path.rglob("*"):
        if item.is_file():
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
            result[str(item.relative_to(path))] = (item.stat().st_size, item.stat().st_mtime_ns, digest)
    return result


def test_json_contract_is_read_only_and_skips_large_kline_count(tmp_path: Path) -> None:
    project = _fixture(tmp_path)
    before = _snapshot(project)

    completed = _run(project, "-Json", "-Deep", "-WarnBackupAgeDays", "7")
    after = _snapshot(project)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert before == after
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == 1
    assert payload["summary"]["status"] == "OK"
    assert payload["summary"]["exit_code"] == 0
    assert payload["search_index"]["classification_coverage"] == 1
    assert payload["backup"]["manifest_path"].endswith("BACKUP_INFO.txt")
    kline_tables = payload["assets"]["kline_db"]["sqlite"]["tables"]
    kline_table = next(table for table in kline_tables if table["name"] == "kline")
    assert kline_table["rows"] is None
    assert kline_table["row_count_skipped"] is True
    codes = {finding["code"] for finding in payload["findings"]}
    assert "KLINE_DB_SQLITE_READ_ONLY" in codes
    assert "SEARCH_INDEX_CLASSIFICATION_COVERED" in codes
    assert "BACKUP_SQLITE_READ_ONLY" in codes


def test_missing_assets_have_stable_codes_and_warning_or_critical_exit(tmp_path: Path) -> None:
    project = tmp_path / "empty-project"
    (project / "data").mkdir(parents=True)
    (project / "static").mkdir(parents=True)

    completed = _run(project, "-Json")

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    findings = {finding["code"]: finding for finding in payload["findings"]}
    assert findings["KLINE_DB_MISSING"]["status"] == "CRITICAL"
    assert findings["SEARCH_INDEX_MISSING"]["status"] == "WARN"
    assert findings["BACKUP_ROOT_MISSING"]["status"] == "CRITICAL"
    assert payload["summary"]["exit_code"] == 2


def test_invalid_search_index_is_critical_but_rebuildability_is_reported(tmp_path: Path) -> None:
    project = tmp_path / "invalid-index"
    (project / "data").mkdir(parents=True)
    (project / "static").mkdir(parents=True)
    (project / "build_search_index.py").write_text("# fixture rebuild marker\n", encoding="utf-8")
    _write_sqlite(project / "data" / "kline.db")
    (project / "static" / "search_index.json").write_text("{not json", encoding="utf-8")

    completed = _run(project, "-Json")

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    codes = {finding["code"] for finding in payload["findings"]}
    assert "SEARCH_INDEX_INVALID" in codes
    assert "SEARCH_INDEX_REBUILD_AVAILABLE" in codes


def test_text_mode_is_human_readable(tmp_path: Path) -> None:
    project = tmp_path / "text-project"
    (project / "data").mkdir(parents=True)
    completed = _run(project)

    assert completed.returncode == 2
    assert completed.stdout.startswith("Data asset diagnostic: CRITICAL")
    assert "KLINE_DB_MISSING" in completed.stdout


def test_script_contains_only_read_only_sqlite_probe() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert "mode=ro" in source
    assert "pragma query_only=on" in source
    for forbidden in ("wal_checkpoint", "vacuum", "remove-item", "copy-item"):
        assert forbidden not in source
    assert 'checkpoint_at = $null' in source
