"""Read-only/restore contract tests for the data snapshot PowerShell CLI.

Every test uses temporary project trees.  No test points the scripts at the
checkout's real ``data`` or ``vault`` directories and no destructive command is
ever run against workspace data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "scripts" / "export_data_snapshot.ps1"
IMPORT = ROOT / "scripts" / "import_data_snapshot.ps1"
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_ps(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    executable = _powershell()
    if not executable:
        pytest.skip("PowerShell is required for snapshot CLI tests")
    return subprocess.run(
        [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def _create_project(path: Path, *, marker: str = "source") -> None:
    (path / "data").mkdir(parents=True)
    (path / "vault" / "TradingVault" / "nested").mkdir(parents=True)
    connection = sqlite3.connect(path / "data" / "kline.db")
    connection.execute("CREATE TABLE prices (marker TEXT)")
    connection.execute("INSERT INTO prices VALUES (?)", (marker,))
    connection.commit()
    connection.close()
    (path / "data" / "signals.json").write_text(
        json.dumps({"marker": marker}), encoding="utf-8"
    )
    (path / "vault" / "TradingVault" / "nested" / "note.md").write_text(
        marker, encoding="utf-8"
    )


def _db_marker(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("SELECT marker FROM prices").fetchone()[0])
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _export(project: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    return _run_ps(
        EXPORT,
        "-ProjectRoot",
        str(project),
        "-DestDir",
        str(destination),
    )


def test_export_uses_relative_manifest_and_does_not_mutate_source(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _create_project(project)
    db = project / "data" / "kline.db"
    before_db = _sha256(db)
    before_signal = _sha256(project / "data" / "signals.json")
    destination = tmp_path / "snapshot"

    result = _export(project, destination)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _sha256(db) == before_db
    assert _sha256(project / "data" / "signals.json") == before_signal
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "board-app-data-snapshot"
    assert manifest["sqlite_backup_api"] is True
    paths = {item["path"] for item in manifest["items"]}
    assert "data/kline.db" in paths
    assert "data/signals.json" in paths
    assert "vault/TradingVault/nested/note.md" in paths
    assert "kline.db" not in paths
    assert not (destination / "data" / "kline.db-wal").exists()
    assert _db_marker(destination / "data" / "kline.db") == "source"
    expected_manifest_hash = _sha256(destination / "manifest.json")
    assert expected_manifest_hash in (destination / "manifest.sha256").read_text()


def test_import_defaults_to_dry_run_and_rejects_tampered_snapshot(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _create_project(source, marker="source")
    _create_project(target, marker="target")
    destination = tmp_path / "snapshot"
    exported = _export(source, destination)
    assert exported.returncode == 0, exported.stdout + exported.stderr

    target_db_before = _sha256(target / "data" / "kline.db")
    dry_run = _run_ps(IMPORT, "-ProjectRoot", str(target), "-SrcDir", str(destination))
    assert dry_run.returncode == 0, dry_run.stdout + dry_run.stderr
    assert "DRY-RUN" in dry_run.stdout
    assert _sha256(target / "data" / "kline.db") == target_db_before
    assert _db_marker(target / "data" / "kline.db") == "target"

    (destination / "data" / "signals.json").write_text("tampered", encoding="utf-8")
    rejected = _run_ps(IMPORT, "-ProjectRoot", str(target), "-SrcDir", str(destination))
    assert rejected.returncode != 0
    assert _db_marker(target / "data" / "kline.db") == "target"


def test_merge_restore_requires_noninteractive_token_and_preserves_vault_extras(
    tmp_path: Path,
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _create_project(source, marker="source")
    _create_project(target, marker="target")
    extra = target / "vault" / "TradingVault" / "keep-me.md"
    extra.write_text("preserve", encoding="utf-8")
    destination = tmp_path / "snapshot"
    exported = _export(source, destination)
    assert exported.returncode == 0, exported.stdout + exported.stderr

    no_token = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-Confirm",
    )
    assert no_token.returncode != 0
    assert _db_marker(target / "data" / "kline.db") == "target"

    restored = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-Confirm",
        "-ConfirmValue",
        "RESTORE",
        "-Stopped",
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert _db_marker(target / "data" / "kline.db") == "source"
    assert extra.read_text(encoding="utf-8") == "preserve"
    assert list((target / ".snapshot-recovery").rglob("restore.json"))


def test_stopped_restore_moves_sidecar_into_protection_copy(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _create_project(source, marker="source")
    _create_project(target, marker="target")
    destination = tmp_path / "snapshot"
    exported = _export(source, destination)
    assert exported.returncode == 0, exported.stdout + exported.stderr

    sidecar = target / "data" / "kline.db-wal"
    sidecar.write_bytes(b"stale-sidecar")
    unsafe = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-Confirm",
        "-ConfirmValue",
        "RESTORE",
    )
    assert unsafe.returncode != 0
    assert sidecar.exists()

    restored = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-Confirm",
        "-ConfirmValue",
        "RESTORE",
        "-Stopped",
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert not sidecar.exists()
    protected = list((target / ".snapshot-recovery").rglob("kline.db-wal"))
    assert protected and protected[0].read_bytes() == b"stale-sidecar"


def test_exact_vault_mode_is_explicit_and_protects_removed_files(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _create_project(source, marker="source")
    _create_project(target, marker="target")
    extra = target / "vault" / "TradingVault" / "only-in-target.md"
    extra.write_text("keep in recovery", encoding="utf-8")
    destination = tmp_path / "snapshot"
    exported = _export(source, destination)
    assert exported.returncode == 0, exported.stdout + exported.stderr

    wrong_token = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-VaultMode",
        "Exact",
        "-Confirm",
        "-ConfirmValue",
        "RESTORE",
        "-Stopped",
    )
    assert wrong_token.returncode != 0
    assert extra.exists()

    restored = _run_ps(
        IMPORT,
        "-ProjectRoot",
        str(target),
        "-SrcDir",
        str(destination),
        "-VaultMode",
        "Exact",
        "-Confirm",
        "-ConfirmValue",
        "RESTORE_EXACT",
        "-Stopped",
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert not extra.exists()
    protected = list((target / ".snapshot-recovery").rglob("only-in-target.md"))
    assert protected and protected[0].read_text(encoding="utf-8") == "keep in recovery"


def test_exact_restore_moves_vault_atomically_instead_of_deleting_source_tree():
    source = (ROOT / "scripts" / "data_snapshot.py").read_text(encoding="utf-8")
    assert "_move_tree_to_protection(vault_root" in source
    assert "shutil.rmtree(vault_root)" not in source
