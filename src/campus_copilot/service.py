import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

from .chunking import chunk_document, chunk_faq
from .config import DB_PATH, PROCESSED_DIR, RAW_DIR
from .db import connect, rebuild
from .extractors import load_documents
from .models import ChunkRecord, DocumentRecord


def _write_jsonl(path: Path, records: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_knowledge_base(db_path: Path = DB_PATH) -> Dict[str, object]:
    documents, faq_units = load_documents()
    faq_document = next(document for document in documents if document.source_kind == "peer_faq")
    chunks: List[ChunkRecord] = chunk_faq(faq_document, faq_units)
    for document in documents:
        if document.document_id == faq_document.document_id:
            continue
        chunks.extend(chunk_document(document))
    _write_jsonl(PROCESSED_DIR / "documents.jsonl", [document.to_dict() for document in documents])
    _write_jsonl(PROCESSED_DIR / "chunks.jsonl", [chunk.to_dict() for chunk in chunks])
    rebuild(db_path, documents, chunks)
    return {
        "database": str(db_path),
        "documents": len(documents),
        "chunks": len(chunks),
        "faq_units": len(faq_units),
        "authority_tiers": dict(Counter(document.authority_tier for document in documents)),
        "parse_status": dict(Counter(document.parse_status for document in documents)),
    }


def corpus_stats(db_path: Path = DB_PATH) -> Dict[str, object]:
    connection = connect(db_path)
    try:
        documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        authority = {row[0]: row[1] for row in connection.execute("SELECT authority_tier, COUNT(*) FROM documents GROUP BY authority_tier")}
        parse_status = {row[0]: row[1] for row in connection.execute("SELECT parse_status, COUNT(*) FROM documents GROUP BY parse_status")}
        return {"documents": documents, "chunks": chunks, "authority_tiers": authority, "parse_status": parse_status}
    finally:
        connection.close()


def audit_corpus(db_path: Path = DB_PATH) -> Dict[str, object]:
    connection = connect(db_path)
    try:
        missing_dates = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE published_at IS NULL AND effective_from IS NULL"
        ).fetchone()[0]
        metadata_only = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE parse_status NOT IN ('parsed','resource_only')"
        ).fetchone()[0]
        privacy_review = [dict(row) for row in connection.execute(
            "SELECT title, local_path, privacy_risk FROM documents WHERE privacy_risk != 'low'"
        )]
        unassertable_chunks = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE assertion_policy IN ('do_not_assert','do_not_assert_until_ocr')"
        ).fetchone()[0]
        return {
            "missing_verified_publication_or_effective_date": missing_dates,
            "documents_without_searchable_body": metadata_only,
            "privacy_review_items": privacy_review,
            "unassertable_chunks": unassertable_chunks,
            "ready_for_unqualified_generation": False,
            "reason": "Source dates and official coverage require curation; generation must follow per-source assertion policy.",
        }
    finally:
        connection.close()
