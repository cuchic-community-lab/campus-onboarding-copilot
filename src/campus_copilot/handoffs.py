import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import TRACE_DB_PATH
from .tracing import redact_text


EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
TRACE_ID = re.compile(r"^trace_[0-9a-f]{32}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SQLiteHandoffStore:
    """Opt-in human follow-ups kept apart from redacted RAG traces."""

    def __init__(self, path: Path = TRACE_DB_PATH, retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = max(1, min(int(retention_days), 365))
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS human_handoffs (
                       handoff_id TEXT PRIMARY KEY,
                       created_at TEXT NOT NULL,
                       trace_id TEXT NOT NULL,
                       email TEXT NOT NULL,
                       query TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'pending'
                   )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_handoffs_created_at ON human_handoffs(created_at DESC)"
            )

    @staticmethod
    def validate_email(email: str) -> str:
        normalized = email.strip().lower()
        if len(normalized) > 254 or not EMAIL.fullmatch(normalized):
            raise ValueError("invalid_email")
        return normalized

    def submit(self, email: str, query: str, trace_id: str) -> Dict[str, str]:
        normalized_email = self.validate_email(email)
        clean_query = redact_text(query.strip())[:500]
        if not clean_query:
            raise ValueError("query_required")
        if not TRACE_ID.fullmatch(trace_id):
            raise ValueError("invalid_trace_id")
        handoff_id = "handoff_" + uuid.uuid4().hex
        created_at = _utc_now()
        with self._lock, self._connect() as connection:
            trace = connection.execute(
                "SELECT 1 FROM rag_traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if trace is None:
                raise ValueError("trace_not_found")
            connection.execute(
                """INSERT INTO human_handoffs
                   (handoff_id, created_at, trace_id, email, query, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (handoff_id, created_at, trace_id, normalized_email, clean_query),
            )
        self.purge_expired()
        return {"handoff_id": handoff_id, "created_at": created_at, "status": "pending"}

    def purge_expired(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM human_handoffs WHERE created_at < ?", (cutoff,)
            )
            return int(cursor.rowcount)

    def list_recent(self, limit: int = 20) -> List[Dict[str, object]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT handoff_id, created_at, trace_id, email, query, status
                   FROM human_handoffs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, handoff_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT handoff_id, created_at, trace_id, email, query, status
                   FROM human_handoffs WHERE handoff_id = ?""",
                (handoff_id,),
            ).fetchone()
        return dict(row) if row else None
