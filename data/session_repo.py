# -*- coding: utf-8 -*-
"""会话草稿 SQLite 索引（机读真相源）；vault 为人读副本。"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import DATA_DIR

SESSION_INDEX_DB = DATA_DIR / "session_index.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SessionRepo:
    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path or SESSION_INDEX_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            c = sqlite3.connect(str(self.db_path), check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return self._local.conn

    def _init(self):
        c = self._conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        c.commit()

    def upsert(self, session: Dict[str, Any]) -> None:
        now = _now()
        session.setdefault("created_at", now)
        session["updated_at"] = now
        c = self._conn()
        c.execute(
            """
            INSERT INTO sessions (id, title, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                session["id"],
                session.get("title") or "",
                session.get("status") or "drafting",
                json.dumps(session, ensure_ascii=False),
                session.get("created_at", now),
                session["updated_at"],
            ),
        )
        c.commit()

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT payload_json FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT payload_json FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [json.loads(r["payload_json"]) for r in rows]

    def list_summaries(self, limit: int = 50) -> List[Dict[str, Any]]:
        out = []
        for s in self.list(limit=limit):
            out.append(
                {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "status": s.get("status"),
                    "created_at": s.get("created_at"),
                    "updated_at": s.get("updated_at"),
                    "chart_count": len(s.get("charts") or []),
                    "cause_count": len(s.get("causes") or []),
                    "open_effects": sum(
                        1
                        for e in (s.get("effects") or [])
                        if e.get("phase") != "closed"
                    ),
                }
            )
        return out

    def delete(self, session_id: str) -> bool:
        c = self._conn()
        cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        c.commit()
        return cur.rowcount > 0

    def get_active_id(self) -> Optional[str]:
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key='active_session_id'"
        ).fetchone()
        return row["value"] if row else None

    def set_active_id(self, session_id: Optional[str]) -> None:
        c = self._conn()
        if session_id is None:
            c.execute("DELETE FROM meta WHERE key='active_session_id'")
        else:
            c.execute(
                """
                INSERT INTO meta (key, value) VALUES ('active_session_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (session_id,),
            )
        c.commit()


_repo: Optional[SessionRepo] = None


def get_session_repo() -> SessionRepo:
    global _repo
    if _repo is None:
        _repo = SessionRepo()
    return _repo
