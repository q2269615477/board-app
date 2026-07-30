"""
annotation_repo.py — Case / Relation / Reminder 的 SQLite 索引
API 与 Agent 检索真相源；vault md 为人读副本。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import ANNOTATION_INDEX_DB


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AnnotationRepo:
    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path or ANNOTATION_INDEX_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._mutate_lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        c = self._conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                type TEXT,
                symbol TEXT,
                symbol_name TEXT,
                asset_type TEXT,
                period TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cases_symbol_period
                ON cases(symbol, period);
            CREATE INDEX IF NOT EXISTS idx_cases_type ON cases(type);

            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                relation_note TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                owner_type TEXT,
                owner_id TEXT,
                at TEXT,
                message TEXT,
                status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_at ON reminders(at, status);

            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                title TEXT,
                theme TEXT,
                day TEXT,
                status TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS proposal_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                period TEXT,
                price REAL,
                role TEXT,
                verdict TEXT,
                reason TEXT,
                candidate_json TEXT,
                created_at TEXT
            );
            """
        )
        c.commit()

    def upsert_case(self, case: Dict[str, Any]) -> None:
        now = _now_iso()
        case.setdefault("created_at", now)
        case["updated_at"] = now
        c = self._conn()
        c.execute(
            """
            INSERT INTO cases (id, type, symbol, symbol_name, asset_type, period,
                               payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type,
                symbol=excluded.symbol,
                symbol_name=excluded.symbol_name,
                asset_type=excluded.asset_type,
                period=excluded.period,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                case["id"],
                case.get("type"),
                case.get("symbol"),
                case.get("symbol_name"),
                case.get("asset_type"),
                case.get("period"),
                json.dumps(case, ensure_ascii=False),
                case.get("created_at", now),
                case["updated_at"],
            ),
        )
        c.commit()
        self._sync_reminders("case", case["id"], case.get("reminders") or [])

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM cases WHERE id=?", (case_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def list_cases(
        self,
        symbol: str = None,
        period: str = None,
        type_: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT payload_json FROM cases WHERE 1=1"
        params: List[Any] = []
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if period:
            sql += " AND period=?"
            params.append(period)
        if type_:
            sql += " AND type=?"
            params.append(type_)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn().execute(sql, params).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def search_cases(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return self.list_cases(limit=limit)
        like = f"%{q}%"
        rows = self._conn().execute(
            """
            SELECT payload_json FROM cases
            WHERE symbol LIKE ? OR symbol_name LIKE ? OR payload_json LIKE ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def upsert_relation(self, rel: Dict[str, Any]) -> None:
        now = _now_iso()
        rel.setdefault("created_at", now)
        rel["updated_at"] = now
        c = self._conn()
        c.execute(
            """
            INSERT INTO relations (id, relation_note, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                relation_note=excluded.relation_note,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                rel["id"],
                rel.get("relation_note") or "",
                json.dumps(rel, ensure_ascii=False),
                rel.get("created_at", now),
                rel["updated_at"],
            ),
        )
        c.commit()
        self._sync_reminders("relation", rel["id"], rel.get("reminders") or [])

    def get_relation(self, rel_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM relations WHERE id=?", (rel_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def list_relations(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT payload_json FROM relations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def search_relations(self, q: str, limit: int = 50) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        if not q:
            return self.list_relations(limit=limit)
        like = f"%{q}%"
        rows = self._conn().execute(
            """
            SELECT payload_json FROM relations
            WHERE relation_note LIKE ? OR payload_json LIKE ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def _sync_reminders(
        self, owner_type: str, owner_id: str, reminders: List[Dict[str, Any]]
    ) -> None:
        c = self._conn()
        c.execute(
            "DELETE FROM reminders WHERE owner_type=? AND owner_id=?",
            (owner_type, owner_id),
        )
        for rm in reminders:
            rid = rm.get("id") or f"{owner_id}_{rm.get('at', '')}"
            c.execute(
                """
                INSERT OR REPLACE INTO reminders
                (id, owner_type, owner_id, at, message, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    owner_type,
                    owner_id,
                    rm.get("at") or "",
                    rm.get("message") or "",
                    rm.get("status") or "pending",
                ),
            )
        c.commit()

    def list_due_reminders(self, now_iso: str = None) -> List[Dict[str, Any]]:
        now = now_iso or _now_iso()
        rows = self._conn().execute(
            """
            SELECT * FROM reminders
            WHERE status='pending' AND at != '' AND at <= ?
            ORDER BY at ASC
            """,
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── runs ───────────────────────────────────────────────

    def upsert_run(self, run: Dict[str, Any]) -> None:
        now = _now_iso()
        run_id = run["id"]
        c = self._conn()
        existing = c.execute(
            "SELECT 1 FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if existing:
            run["updated_at"] = now
            c.execute(
                """
                UPDATE runs SET title=?, theme=?, day=?, status=?,
                    payload_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    run.get("title", ""),
                    run.get("theme", ""),
                    run.get("day", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                    run.get("status", "open"),
                    json.dumps(run, ensure_ascii=False),
                    now,
                    run_id,
                ),
            )
        else:
            run.setdefault("created_at", now)
            run["updated_at"] = now
            c.execute(
                """
                INSERT INTO runs (id, title, theme, day, status,
                    payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run.get("title", ""),
                    run.get("theme", ""),
                    run.get("day", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                    run.get("status", "open"),
                    json.dumps(run, ensure_ascii=False),
                    run.get("created_at", now),
                    now,
                ),
            )
        c.commit()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def latest_open_run(self) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM runs WHERE status='open' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT payload_json FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def mutate_run(self, run_id: str, mutator) -> Optional[Dict[str, Any]]:
        with self._mutate_lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            result = mutator(run)
            # mutators often modify in-place and return None; fall back to run
            mutated = result if result is not None else run
            self.upsert_run(mutated)
            return mutated

    # ── groups ─────────────────────────────────────────────

    def upsert_group(self, group: Dict[str, Any]) -> None:
        now = _now_iso()
        group_id = group["id"]
        c = self._conn()
        existing = c.execute(
            "SELECT 1 FROM groups WHERE id=?", (group_id,)
        ).fetchone()
        if existing:
            group["updated_at"] = now
            c.execute(
                "UPDATE groups SET payload_json=?, updated_at=? WHERE id=?",
                (json.dumps(group, ensure_ascii=False), now, group_id),
            )
        else:
            group.setdefault("created_at", now)
            group["updated_at"] = now
            c.execute(
                "INSERT INTO groups (id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (
                    group_id,
                    json.dumps(group, ensure_ascii=False),
                    group.get("created_at", now),
                    now,
                ),
            )
        c.commit()

    def get_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM groups WHERE id=?", (group_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def list_groups(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT payload_json FROM groups ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def delete_group(self, group_id: str) -> None:
        self._conn().execute("DELETE FROM groups WHERE id=?", (group_id,))
        self._conn().commit()

    # ── proposal_feedback ──────────────────────────────────

    def record_proposal_feedback(self, feedback: Dict[str, Any]) -> None:
        now = _now_iso()
        c = self._conn()
        c.execute(
            """
            INSERT INTO proposal_feedback
                (symbol, period, price, role, verdict, reason, candidate_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback.get("symbol", ""),
                feedback.get("period", ""),
                feedback.get("price"),
                feedback.get("role", ""),
                feedback.get("verdict", ""),
                feedback.get("reason", ""),
                json.dumps(feedback, ensure_ascii=False),
                now,
            ),
        )
        c.commit()

    def list_proposal_feedback(
        self, verdict: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM proposal_feedback"
        params: List[Any] = []
        if verdict is not None:
            sql += " WHERE verdict=?"
            params.append(verdict)
        sql += " ORDER BY created_at DESC"
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]


_repo: Optional[AnnotationRepo] = None


def get_annotation_repo() -> AnnotationRepo:
    global _repo
    if _repo is None:
        _repo = AnnotationRepo()
    return _repo
