#!/usr/bin/env python3
"""Create and restore board-app data snapshots safely.

The PowerShell wrappers in this directory intentionally keep the public CLI
small.  All data-sensitive work lives here so the same implementation can be
tested without touching the checkout's real ``data`` or ``vault`` trees.

Export is read-only with respect to the project: SQLite databases are copied
through :meth:`sqlite3.Connection.backup`, which gives a consistent snapshot
without a checkpoint (and therefore without changing the source database).
Restore validates a signed-by-hash manifest, runs SQLite ``quick_check`` in
read-only mode, creates a same-volume protection copy, then atomically swaps
staged files into place.  It never silently deletes vault files: the default
vault mode is ``merge``.  ``exact`` is an explicit operation and moves the old
vault into the protection copy before installing the snapshot tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


FORMAT = "board-app-data-snapshot"
MANIFEST_VERSION = 2
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
DEFAULT_FILES = (
    "data/kline.db",
    "data/stock_data.db",
    "data/annotation_index.sqlite",
    "data/session_index.sqlite",
    "data/signals.json",
)
DEFAULT_DIRECTORIES = ("vault/TradingVault",)
SQLITE_PATHS = {
    "data/kline.db",
    "data/stock_data.db",
    "data/annotation_index.sqlite",
    "data/session_index.sqlite",
}


class SnapshotError(RuntimeError):
    """Expected user-facing snapshot validation or safety failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise_rel(value: str | os.PathLike[str]) -> str:
    """Return a safe, slash-separated relative path.

    Snapshot paths are deliberately platform-neutral.  Rejecting traversal
    here protects both export destinations and restore targets.
    """

    raw = str(value).replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or not raw or any(part in ("", ".", "..") for part in pure.parts):
        raise SnapshotError(f"unsafe relative snapshot path: {value!s}")
    return pure.as_posix()


def _safe_join(root: Path, relative: str) -> Path:
    rel = _normalise_rel(relative)
    path = root.joinpath(*PurePosixPath(rel).parts)
    # Resolve only for the containment check; the path itself may not yet
    # exist.  ``relative`` has already rejected traversal, so this also
    # guards against surprising Windows drive/UNC syntax.
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SnapshotError(f"path escapes root: {relative}") from exc
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError:
        # A few network filesystems do not expose fsync for read-only handles;
        # the atomic rename still provides the important visibility guarantee.
        pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sqlite_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SnapshotError(f"SQLite file does not exist: {path}")
    # as_uri() handles Windows drive letters and spaces correctly.  Opening
    # with mode=ro is a defence-in-depth guarantee that validation cannot
    # create journals or otherwise mutate the source.
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        raise SnapshotError(f"cannot open SQLite read-only: {path}: {exc}") from exc


def _sqlite_quick_check(path: Path) -> None:
    connection = _sqlite_ro(path)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise SnapshotError(f"SQLite quick_check failed for {path}: {exc}") from exc
    finally:
        connection.close()
    if not row or str(row[0]).lower() != "ok":
        detail = row[0] if row else "no result"
        raise SnapshotError(f"SQLite quick_check is not ok for {path}: {detail}")


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_connection = _sqlite_ro(source)
    destination_connection: sqlite3.Connection | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination_connection = sqlite3.connect(str(destination))
        source_connection.backup(destination_connection)
        destination_connection.commit()
        destination_connection.close()
        destination_connection = None
        _fsync_file(destination)
    except sqlite3.Error as exc:
        raise SnapshotError(f"SQLite backup failed for {source}: {exc}") from exc
    finally:
        source_connection.close()
        if destination_connection is not None:
            destination_connection.close()


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    _fsync_file(destination)


def _assert_export_destination(root: Path, destination: Path) -> None:
    root_resolved = root.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved == root_resolved:
        raise SnapshotError("snapshot destination must not be the project root")
    try:
        destination_resolved.relative_to(root_resolved)
    except ValueError:
        return
    # A backup under the checkout (for example data/backup/) is compatible
    # with the historical CLI.  Do reject a destination inside a recursively
    # copied source directory, otherwise the staging tree would be copied back
    # into itself while walking the vault.
    for relative in DEFAULT_DIRECTORIES:
        source_directory = _safe_join(root, relative).resolve()
        try:
            destination_resolved.relative_to(source_directory)
        except ValueError:
            continue
        raise SnapshotError(
            f"snapshot destination must not be inside copied directory: {relative}"
        )


def _manifest_item(path: str, kind: str, output: Path) -> dict[str, Any]:
    return {
        "path": _normalise_rel(path),
        "kind": kind,
        "size": output.stat().st_size,
        "sha256": _sha256(output),
    }


def export_snapshot(project_root: Path, destination: Path) -> dict[str, Any]:
    root = project_root.resolve()
    destination = destination.resolve()
    _assert_export_destination(root, destination)
    if destination.exists() and any(destination.iterdir()):
        raise SnapshotError(f"destination must be new or empty: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    stage = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=False)
    items: list[dict[str, Any]] = []
    directories: list[str] = []
    missing: list[str] = []
    try:
        for relative in DEFAULT_FILES:
            source = _safe_join(root, relative)
            if not source.is_file():
                missing.append(relative)
                continue
            output = _safe_join(stage, relative)
            if relative in SQLITE_PATHS:
                _sqlite_backup(source, output)
                kind = "sqlite"
            else:
                _copy_file(source, output)
                kind = "file"
            items.append(_manifest_item(relative, kind, output))

        for relative_root in DEFAULT_DIRECTORIES:
            source_root = _safe_join(root, relative_root)
            if not source_root.is_dir():
                missing.append(relative_root)
                continue
            directories.append(relative_root)
            # Include every directory so empty vault folders survive an exact
            # restore; file entries carry their own relative paths as well.
            for directory in sorted((p for p in source_root.rglob("*") if p.is_dir())):
                project_relative = directory.relative_to(root).as_posix()
                directories.append(_normalise_rel(project_relative))
            for source in sorted(p for p in source_root.rglob("*") if p.is_file()):
                relative = _normalise_rel(source.relative_to(root).as_posix())
                output = _safe_join(stage, relative)
                _copy_file(source, output)
                items.append(_manifest_item(relative, "file", output))

        manifest: dict[str, Any] = {
            "format": FORMAT,
            "version": MANIFEST_VERSION,
            "generated_at": _utc_now(),
            "sqlite_backup_api": True,
            "items": sorted(items, key=lambda item: item["path"]),
            "directories": sorted(set(directories)),
            "missing": sorted(missing),
        }
        manifest_path = stage / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _fsync_file(manifest_path)
        (stage / MANIFEST_HASH_NAME).write_text(
            f"{_sha256(manifest_path)}  {MANIFEST_NAME}\n", encoding="ascii"
        )
        _fsync_file(stage / MANIFEST_HASH_NAME)
        _fsync_directory(stage)

        if destination.exists():
            # It was verified empty above.  Removing only that empty directory
            # allows an atomic final rename without touching user files.
            destination.rmdir()
        os.replace(stage, destination)
        _fsync_directory(destination.parent)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    return {
        "destination": str(destination),
        "manifest": str(destination / MANIFEST_NAME),
        "items": len(items),
        "missing": missing,
        "sha256": _sha256(destination / MANIFEST_NAME),
    }


def _load_manifest(source: Path) -> tuple[dict[str, Any], str]:
    manifest_path = source / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SnapshotError(f"snapshot manifest is required: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"invalid snapshot manifest: {manifest_path}: {exc}") from exc
    if manifest.get("format") != FORMAT or int(manifest.get("version", 0)) != MANIFEST_VERSION:
        raise SnapshotError("unsupported snapshot manifest format/version")
    if not isinstance(manifest.get("items"), list):
        raise SnapshotError("snapshot manifest items must be a list")

    manifest_hash_path = source / MANIFEST_HASH_NAME
    if not manifest_hash_path.is_file():
        raise SnapshotError(f"snapshot manifest hash is required: {manifest_hash_path}")
    try:
        expected = manifest_hash_path.read_text(encoding="ascii").strip().split()[0]
    except (OSError, IndexError) as exc:
        raise SnapshotError(f"invalid snapshot manifest hash: {manifest_hash_path}") from exc
    actual = _sha256(manifest_path)
    if expected.lower() != actual.lower():
        raise SnapshotError("snapshot manifest SHA256 mismatch")
    return manifest, _sha256(manifest_path)


def _verify_manifest(source: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in manifest["items"]:
        if not isinstance(raw_item, dict):
            raise SnapshotError("snapshot manifest contains a non-object item")
        relative = _normalise_rel(str(raw_item.get("path", "")))
        if relative in seen:
            raise SnapshotError(f"duplicate snapshot item: {relative}")
        seen.add(relative)
        kind = str(raw_item.get("kind", "file"))
        if kind not in {"file", "sqlite"}:
            raise SnapshotError(f"unsupported snapshot item kind for {relative}: {kind}")
        path = _safe_join(source, relative)
        if not path.is_file():
            raise SnapshotError(f"snapshot item is missing: {relative}")
        expected_size = int(raw_item.get("size", -1))
        expected_hash = str(raw_item.get("sha256", ""))
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        if expected_size != actual_size or expected_hash.lower() != actual_hash.lower():
            raise SnapshotError(f"snapshot item hash/size mismatch: {relative}")
        if kind == "sqlite":
            _sqlite_quick_check(path)
        verified.append({"path": relative, "kind": kind, "source": path})
    return verified


def _is_vault_path(relative: str) -> bool:
    return relative == "vault/TradingVault" or relative.startswith("vault/TradingVault/")


def _target_sidecars(path: Path) -> list[Path]:
    return [Path(f"{path}-wal"), Path(f"{path}-shm")]


def _check_restore_safety(
    project_root: Path, verified: list[dict[str, Any]], *, stopped: bool
) -> list[str]:
    risks: list[str] = []
    sqlite_targets: set[Path] = set()
    for item in verified:
        if item["kind"] == "sqlite":
            sqlite_targets.add(_safe_join(project_root, item["path"]))
    for target in sorted(sqlite_targets):
        sidecars = [sidecar for sidecar in _target_sidecars(target) if sidecar.exists()]
        if sidecars:
            risks.append(
                f"SQLite sidecar present for {target}: "
                + ", ".join(str(path) for path in sidecars)
            )
        if target.exists():
            try:
                connection = _sqlite_ro(target)
                row = connection.execute("PRAGMA quick_check(1)").fetchone()
                connection.close()
                if not row or str(row[0]).lower() != "ok":
                    risks.append(f"target SQLite quick_check is not ok: {target}")
            except SnapshotError as exc:
                risks.append(f"target SQLite may be active or unreadable: {exc}")
    if not stopped:
        risks.append("restore requires explicit stopped-state declaration (-Stopped)")
    return risks


def _protection_root(project_root: Path) -> Path:
    # Keep protection copies on the project volume, but outside data/vault so
    # an exact vault swap cannot accidentally move the protection tree itself.
    return project_root / ".snapshot-recovery" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _backup_existing(path: Path, project_root: Path, protection: Path) -> None:
    if not path.exists():
        return
    relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    backup = _safe_join(protection, relative)
    if backup.exists():
        return
    if path.is_dir():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, backup, dirs_exist_ok=False)
    else:
        _copy_file(path, backup)


def _move_sidecar(path: Path, project_root: Path, protection: Path) -> None:
    if not path.exists():
        return
    relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    backup = _safe_join(protection, relative)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, backup)


def _move_tree_to_protection(path: Path, project_root: Path, protection: Path) -> None:
    """Atomically move an existing tree into the same-volume recovery root."""
    if not path.exists():
        return
    relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    backup = _safe_join(protection, relative)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise SnapshotError(f"protection target already exists: {backup}")
    os.replace(path, backup)


def _stage_items(
    verified: list[dict[str, Any]], staging: Path, directories: Iterable[str]
) -> None:
    for relative in directories:
        _safe_join(staging, relative).mkdir(parents=True, exist_ok=True)
    for item in verified:
        destination = _safe_join(staging, item["path"])
        _copy_file(item["source"], destination)
        if destination.stat().st_size != item["source"].stat().st_size:
            raise SnapshotError(f"staging copy size mismatch: {item['path']}")
        if _sha256(destination) != _sha256(item["source"]):
            raise SnapshotError(f"staging copy hash mismatch: {item['path']}")


def _atomic_replace_file(staged: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, target)
    _fsync_directory(target.parent)


def restore_snapshot(
    project_root: Path,
    source: Path,
    *,
    confirm_value: str | None,
    stopped: bool,
    vault_mode: str,
    dry_run: bool,
) -> dict[str, Any]:
    root = project_root.resolve()
    source = source.resolve()
    if not source.is_dir():
        raise SnapshotError(f"snapshot source directory does not exist: {source}")
    if vault_mode not in {"merge", "exact"}:
        raise SnapshotError(f"unsupported vault mode: {vault_mode}")
    manifest, manifest_hash = _load_manifest(source)
    verified = _verify_manifest(source, manifest)
    risks = _check_restore_safety(root, verified, stopped=stopped)

    expected_token = "RESTORE_EXACT" if vault_mode == "exact" else "RESTORE"
    if not dry_run:
        if confirm_value != expected_token:
            raise SnapshotError(
                f"non-interactive restore requires -ConfirmValue {expected_token!r}"
            )
        # WAL/SHM sidecars are an expected recoverable risk when the operator
        # has explicitly declared the writers stopped: they are moved into
        # the protection copy immediately before replacement.  Any other
        # risk (or the missing stopped declaration) remains a hard refusal.
        fatal_risks = [
            risk
            for risk in risks
            if not risk.startswith("SQLite sidecar present for ")
        ]
        if fatal_risks:
            raise SnapshotError("restore safety checks failed: " + "; ".join(fatal_risks))

    plan = {
        "source": str(source),
        "project_root": str(root),
        "manifest_sha256": manifest_hash,
        "vault_mode": vault_mode,
        "dry_run": dry_run,
        "items": [item["path"] for item in verified],
        "safety_risks": risks,
    }
    if dry_run:
        return plan

    protection = _protection_root(root)
    staging = root / f".snapshot-staging-{uuid.uuid4().hex}"
    protection.mkdir(parents=True, exist_ok=False)
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _stage_items(verified, staging, manifest.get("directories", []))
        sqlite_targets = {
            _safe_join(root, item["path"])
            for item in verified
            if item["kind"] == "sqlite"
        }
        for target in sorted(sqlite_targets):
            _backup_existing(target, root, protection)
            for sidecar in _target_sidecars(target):
                _move_sidecar(sidecar, root, protection)

        vault_items = [item for item in verified if _is_vault_path(item["path"])]
        other_items = [item for item in verified if not _is_vault_path(item["path"])]
        if vault_mode == "exact" and vault_items:
            vault_root = _safe_join(root, "vault/TradingVault")
            staged_vault_root = _safe_join(staging, "vault/TradingVault")
            if vault_root.exists():
                # The recovery root is deliberately on the project volume, so
                # the original vault can be preserved by one atomic rename.
                # This avoids a copy-then-delete window for an exact restore.
                _move_tree_to_protection(vault_root, root, protection)
            vault_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_vault_root, vault_root)
        else:
            for item in vault_items:
                target = _safe_join(root, item["path"])
                if target.exists():
                    _backup_existing(target, root, protection)
                _atomic_replace_file(_safe_join(staging, item["path"]), target)

        for item in other_items:
            target = _safe_join(root, item["path"])
            if target.exists():
                _backup_existing(target, root, protection)
            _atomic_replace_file(_safe_join(staging, item["path"]), target)

        restore_record = {
            "format": FORMAT,
            "restored_at": _utc_now(),
            "manifest_sha256": manifest_hash,
            "vault_mode": vault_mode,
            "protection_root": str(protection),
            "items": [item["path"] for item in verified],
        }
        (protection / "restore.json").write_text(
            json.dumps(restore_record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _fsync_file(protection / "restore.json")
        plan.update({"protection_root": str(protection), "restored": len(verified)})
        return plan
    except Exception:
        # Keep both staging and protection copies for a human/operator to
        # inspect.  Nothing here removes user data or attempts a risky
        # automatic rollback after a partial atomic swap.
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="create a read-only snapshot")
    export.add_argument("--project-root", required=True, type=Path)
    export.add_argument("--dest", required=True, type=Path)

    restore = subparsers.add_parser("restore", help="validate and restore a snapshot")
    restore.add_argument("--project-root", required=True, type=Path)
    restore.add_argument("--src", required=True, type=Path)
    restore.add_argument("--confirm-value")
    restore.add_argument("--stopped", action="store_true")
    restore.add_argument("--vault-mode", choices=("merge", "exact"), default="merge")
    restore.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export":
            result = export_snapshot(args.project_root, args.dest)
        else:
            result = restore_snapshot(
                args.project_root,
                args.src,
                confirm_value=args.confirm_value,
                stopped=bool(args.stopped),
                vault_mode=args.vault_mode,
                dry_run=bool(args.dry_run),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except SnapshotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"ERROR: unexpected snapshot failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
