import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Set

from .models import ChunkRecord, DocumentRecord


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    media_type TEXT,
    source_kind TEXT,
    authority_tier TEXT,
    assertion_policy TEXT,
    issuer TEXT,
    description TEXT,
    tags_json TEXT,
    uploaded_at TEXT,
    published_at TEXT,
    effective_from TEXT,
    effective_to TEXT,
    date_status TEXT,
    cohort TEXT,
    academic_year TEXT,
    student_level TEXT,
    major TEXT,
    campus TEXT,
    checksum TEXT,
    parse_status TEXT,
    privacy_risk TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    chunk_type TEXT,
    sequence INTEGER,
    heading_path TEXT,
    page_number INTEGER,
    tags_json TEXT,
    authority_tier TEXT,
    assertion_policy TEXT,
    source_url TEXT,
    cohort TEXT,
    academic_year TEXT,
    student_level TEXT,
    major TEXT,
    campus TEXT,
    uncertainty_json TEXT,
    content_hash TEXT,
    search_terms TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    chunk_id UNINDEXED,
    search_terms,
    title,
    heading_path,
    tags,
    tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def chinese_search_terms(text: str) -> str:
    import re

    text = text.lower()
    terms = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend(list(run))
        terms.extend(run[index:index + 2] for index in range(max(0, len(run) - 1)))
        terms.extend(run[index:index + 3] for index in range(max(0, len(run) - 2)))
    return " ".join(terms)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def rebuild(path: Path, documents: List[DocumentRecord], chunks: List[ChunkRecord]) -> None:
    if path.exists():
        path.unlink()
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
        for document in documents:
            connection.execute(
                """INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document.document_id, document.title, document.source_url, document.local_path,
                    document.media_type, document.source_kind, document.authority_tier,
                    document.assertion_policy, document.issuer, document.description,
                    json.dumps(document.tags, ensure_ascii=False), document.uploaded_at,
                    document.published_at, document.effective_from, document.effective_to,
                    document.date_status, document.cohort, document.academic_year,
                    document.student_level, document.major, document.campus, document.checksum,
                    document.parse_status, document.privacy_risk,
                ),
            )
        for chunk in chunks:
            tags = json.dumps(chunk.tags, ensure_ascii=False)
            uncertainty = json.dumps(chunk.uncertainty, ensure_ascii=False)
            search_terms = chinese_search_terms(" ".join([chunk.title, chunk.heading_path, chunk.text, " ".join(chunk.tags)]))
            connection.execute(
                """INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    chunk.chunk_id, chunk.document_id, chunk.title, chunk.text, chunk.chunk_type,
                    chunk.sequence, chunk.heading_path, chunk.page_number, tags,
                    chunk.authority_tier, chunk.assertion_policy, chunk.source_url,
                    chunk.cohort, chunk.academic_year, chunk.student_level,
                    chunk.major, chunk.campus, uncertainty,
                    chunk.content_hash, search_terms,
                ),
            )
            connection.execute(
                "INSERT INTO chunk_fts(chunk_id, search_terms, title, heading_path, tags) VALUES (?,?,?,?,?)",
                (chunk.chunk_id, search_terms, chunk.title, chunk.heading_path, " ".join(chunk.tags)),
            )
        connection.execute("INSERT INTO index_meta(key,value) VALUES ('schema_version','1')")
        connection.execute("INSERT INTO index_meta(key,value) VALUES ('document_count',?)", (str(len(documents)),))
        connection.execute("INSERT INTO index_meta(key,value) VALUES ('chunk_count',?)", (str(len(chunks)),))
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Incremental (checksum-diff) index maintenance
# ---------------------------------------------------------------------------

def read_checksums(path: Path) -> Dict[str, str]:
    """Return {document_id: checksum} of the existing index, if any."""
    if not path.exists():
        return {}
    connection = connect(path)
    try:
        return {
            row["document_id"]: row["checksum"]
            for row in connection.execute("SELECT document_id, checksum FROM documents")
        }
    finally:
        connection.close()


def _delete_fts_chunk(connection: sqlite3.Connection, chunk_id: str) -> None:
    """Remove one chunk from the FTS virtual table.

    chunk_id is an UNINDEXED column holding a stable id, so a standard DELETE
    (supported by fts5) is used instead of the special 'delete' command —
    the latter requires the full original row content and fails with
    "SQL logic error" when only a rowid is supplied.
    """
    connection.execute("DELETE FROM chunk_fts WHERE chunk_id = ?", (chunk_id,))


def _insert_document(connection: sqlite3.Connection, document: DocumentRecord) -> None:
    connection.execute(
        """INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            document.document_id, document.title, document.source_url, document.local_path,
            document.media_type, document.source_kind, document.authority_tier,
            document.assertion_policy, document.issuer, document.description,
            json.dumps(document.tags, ensure_ascii=False), document.uploaded_at,
            document.published_at, document.effective_from, document.effective_to,
            document.date_status, document.cohort, document.academic_year,
            document.student_level, document.major, document.campus, document.checksum,
            document.parse_status, document.privacy_risk,
        ),
    )


def _insert_chunk(connection: sqlite3.Connection, chunk: ChunkRecord) -> None:
    tags = json.dumps(chunk.tags, ensure_ascii=False)
    uncertainty = json.dumps(chunk.uncertainty, ensure_ascii=False)
    search_terms = chinese_search_terms(" ".join([chunk.title, chunk.heading_path, chunk.text, " ".join(chunk.tags)]))
    connection.execute(
        """INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            chunk.chunk_id, chunk.document_id, chunk.title, chunk.text, chunk.chunk_type,
            chunk.sequence, chunk.heading_path, chunk.page_number, tags,
            chunk.authority_tier, chunk.assertion_policy, chunk.source_url,
            chunk.cohort, chunk.academic_year, chunk.student_level,
            chunk.major, chunk.campus, uncertainty,
            chunk.content_hash, search_terms,
        ),
    )
    connection.execute(
        "INSERT INTO chunk_fts(chunk_id, search_terms, title, heading_path, tags) VALUES (?,?,?,?,?)",
        (chunk.chunk_id, search_terms, chunk.title, chunk.heading_path, " ".join(chunk.tags)),
    )


def _delete_document_rows(connection: sqlite3.Connection, document_id: str) -> None:
    chunk_ids = [row["chunk_id"] for row in connection.execute(
        "SELECT chunk_id FROM chunks WHERE document_id = ?", (document_id,)
    )]
    for chunk_id in chunk_ids:
        _delete_fts_chunk(connection, chunk_id)
    connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))


def incremental_sync(
    path: Path,
    documents: List[DocumentRecord],
    chunks_by_doc: Dict[str, List[ChunkRecord]],
    force: bool = False,
) -> Dict[str, object]:
    """Apply a checksum-diff sync to an existing index.

    Only documents whose checksum changed (or that are new / force-rebuilt) are
    re-parsed and re-chunked; unchanged documents are left untouched in the DB,
    so the expensive parsing/chunking work is strictly incremental. The FTS
    virtual table is maintained incrementally as well.

    - documents:    full desired document set (new state).
    - chunks_by_doc: only documents that need (re)chunking in this pass.
    - force:         if True, treat every document as changed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_checksums(path)
    desired = {document.document_id: document.checksum for document in documents}

    added: List[DocumentRecord] = []
    changed: List[DocumentRecord] = []
    unchanged: List[DocumentRecord] = []
    removed_ids: List[str] = []
    for document in documents:
        if document.document_id not in existing:
            added.append(document)
        elif force or existing[document.document_id] != document.checksum:
            changed.append(document)
        else:
            unchanged.append(document)
    for document_id in existing:
        if document_id not in desired:
            removed_ids.append(document_id)

    connection = connect(path)
    try:
        connection.execute("BEGIN")
        for document_id in removed_ids:
            _delete_document_rows(connection, document_id)
        for document in changed:
            _delete_document_rows(connection, document.document_id)
            _insert_document(connection, document)
            for chunk in chunks_by_doc.get(document.document_id, []):
                _insert_chunk(connection, chunk)
        for document in added:
            _insert_document(connection, document)
            for chunk in chunks_by_doc.get(document.document_id, []):
                _insert_chunk(connection, chunk)
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key,value) VALUES ('document_count',?)",
            (str(len(documents)),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key,value) VALUES ('chunk_count',?)",
            (str(_total_chunk_count(connection, chunks_by_doc, documents)),),
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key,value) VALUES ('schema_version','1')"
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key,value) VALUES ('last_sync_at', ?)",
            (__import__("datetime").datetime.now().isoformat(timespec="seconds"),),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed_ids),
        "unchanged": len(unchanged),
        "documents": len(documents),
        "chunks": sum(len(chunks_by_doc.get(d.document_id, [])) for d in documents),
    }


def _total_chunk_count(
    connection: sqlite3.Connection,
    chunks_by_doc: Dict[str, List[ChunkRecord]],
    documents: List[DocumentRecord],
) -> int:
    """chunks already in DB for untouched docs + new chunks inserted this pass."""
    existing = 0
    for document in documents:
        if document.document_id in chunks_by_doc:
            continue
        row = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document.document_id,)
        ).fetchone()
        existing += int(row[0])
    return existing + sum(len(chunks) for chunks in chunks_by_doc.values())
