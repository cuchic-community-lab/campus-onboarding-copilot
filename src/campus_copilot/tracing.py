import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from .config import TRACE_DB_PATH


EMAIL_PATTERN = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LONG_ID_PATTERN = re.compile(r"(?<![\dA-Za-z])(?:\d{8,17}[\dXx]|[A-Za-z]\d{8,17})(?![\dA-Za-z])")
SENSITIVE_KEYS = {
    "api_key", "authorization", "cookie", "password", "secret", "token",
    "access_code", "credential",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact_text(value: str) -> str:
    value = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    value = PHONE_PATTERN.sub("[REDACTED_PHONE]", value)
    return LONG_ID_PATTERN.sub("[REDACTED_ID]", value)


def sanitize(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)[:20000]
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        clean: Dict[str, object] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            clean[str(key)] = "[REDACTED_SECRET]" if normalized in SENSITIVE_KEYS else sanitize(item)
        return clean
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))[:20000]


def anonymous_session_id(session_id: str) -> str:
    if not session_id:
        return ""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20]


def trace_candidate(item: Dict[str, object], rank: int) -> Dict[str, object]:
    explanation = item.get("score_explanation") or {}
    return {
        "rank": rank,
        "chunk_id": item.get("chunk_id") or item.get("document_id"),
        "title": item.get("title"),
        "heading_path": item.get("heading_path"),
        "authority_tier": item.get("authority_tier"),
        "assertion_policy": item.get("assertion_policy"),
        "retrieval_origin": item.get("retrieval_origin", "local_knowledge"),
        "score": item.get("score"),
        "score_explanation": explanation,
        "evidence_coverage": item.get("evidence_coverage"),
        "source_url": item.get("source_url"),
        "snippet": str(item.get("text") or "")[:360],
    }


class TraceStore(Protocol):
    enabled: bool

    def record(self, trace: Dict[str, object]) -> str:
        ...

    def list_recent(self, limit: int = 20) -> List[Dict[str, object]]:
        ...

    def get(self, trace_id: str) -> Optional[Dict[str, object]]:
        ...


class NullTraceStore:
    enabled = False

    def record(self, trace: Dict[str, object]) -> str:
        return str(trace.get("trace_id") or "")

    def list_recent(self, limit: int = 20) -> List[Dict[str, object]]:
        return []

    def get(self, trace_id: str) -> Optional[Dict[str, object]]:
        return None


class SQLiteTraceStore:
    enabled = True

    def __init__(self, path: Path, retention_days: int = 30):
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
                """CREATE TABLE IF NOT EXISTS rag_traces (
                       trace_id TEXT PRIMARY KEY,
                       created_at TEXT NOT NULL,
                       anonymous_session_id TEXT,
                       question_type TEXT,
                       answerability TEXT,
                       composer TEXT,
                       status TEXT NOT NULL,
                       total_latency_ms INTEGER,
                       query_preview TEXT NOT NULL,
                       payload_json TEXT NOT NULL
                   )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_traces_created_at ON rag_traces(created_at DESC)"
            )

    def purge_expired(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM rag_traces WHERE created_at < ?", (cutoff,))
            return int(cursor.rowcount)

    def record(self, trace: Dict[str, object]) -> str:
        prepared = dict(trace)
        trace_id = str(prepared.get("trace_id") or "trace_" + uuid.uuid4().hex)
        created_at = str(prepared.get("created_at") or _utc_now())
        raw_session = str(prepared.pop("session_id", "") or "")
        prepared["trace_id"] = trace_id
        prepared["created_at"] = created_at
        prepared["anonymous_session_id"] = anonymous_session_id(raw_session)
        clean = sanitize(prepared)
        assert isinstance(clean, dict)
        query = clean.get("query") if isinstance(clean.get("query"), dict) else {}
        response = clean.get("response") if isinstance(clean.get("response"), dict) else {}
        retrieval = clean.get("retrieval") if isinstance(clean.get("retrieval"), dict) else {}
        llm = clean.get("llm") if isinstance(clean.get("llm"), dict) else {}
        timings = clean.get("timings_ms") if isinstance(clean.get("timings_ms"), dict) else {}
        status = "error" if clean.get("error") else str(response.get("status") or "completed")
        payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO rag_traces
                   (trace_id, created_at, anonymous_session_id, question_type, answerability,
                    composer, status, total_latency_ms, query_preview, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id,
                    created_at,
                    str(clean.get("anonymous_session_id") or ""),
                    str(query.get("question_type") or ""),
                    str(retrieval.get("answerability") or ""),
                    str(llm.get("composer_used") or ""),
                    status,
                    int(timings.get("total") or 0),
                    str(query.get("original") or "")[:500],
                    payload,
                ),
            )
        self.purge_expired()
        return trace_id

    def list_recent(self, limit: int = 20) -> List[Dict[str, object]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT trace_id, created_at, anonymous_session_id, question_type,
                          answerability, composer, status, total_latency_ms, query_preview
                   FROM rag_traces ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, trace_id: str) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM rag_traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None


def configured_trace_store() -> TraceStore:
    enabled = os.getenv("CAMPUS_TRACE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    if not enabled:
        return NullTraceStore()
    try:
        retention_days = int(os.getenv("CAMPUS_TRACE_RETENTION_DAYS", "30"))
    except ValueError:
        retention_days = 30
    return SQLiteTraceStore(TRACE_DB_PATH, retention_days)
