import json
import sqlite3
from pathlib import Path
from typing import Iterable, List

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
