import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List

from .chunking import chunk_document, chunk_faq
from .config import AI_ASSISTED_CHUNKING, DB_PATH, PROCESSED_DIR, QA_DIR, ensure_dirs
from .db import connect, incremental_sync, rebuild
from .extractors import load_documents
from .models import ChunkRecord, DocumentRecord


def _write_jsonl(path: Path, records: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _chunk_all(documents: List[DocumentRecord], faq_units: List[Dict[str, str]]) -> Dict[str, List[ChunkRecord]]:
    """Chunk every document; returns {document_id: [ChunkRecord]}."""
    chunks_by_doc: Dict[str, List[ChunkRecord]] = {}
    faq_document = next(
        (document for document in documents if document.source_kind == "peer_faq"), None
    )
    for document in documents:
        if faq_document is not None and document.document_id == faq_document.document_id:
            chunks_by_doc[document.document_id] = chunk_faq(faq_document, faq_units)
        else:
            chunks_by_doc[document.document_id] = chunk_document(document)
    return chunks_by_doc


def build_knowledge_base(
    db_path: Path = DB_PATH,
    force: bool = False,
    quiet: bool = False,
) -> Dict[str, object]:
    """Incrementally rebuild the index.

    - First build (or `force=True`): full parse + chunk + rebuild.
    - Subsequent builds: checksum-diff incremental sync (only changed/new docs
      are re-parsed; unchanged docs are left untouched).
    """
    ensure_dirs()
    started = time.time()
    documents, faq_units = load_documents()
    chunks_by_doc = _chunk_all(documents, faq_units)

    ai_enhancement: Dict[str, object] = {"enabled": AI_ASSISTED_CHUNKING, "enhanced_chunks": 0, "total": 0}
    if AI_ASSISTED_CHUNKING:
        try:
            from .ai_chunking import enhance_chunks
            all_chunks = [chunk for chunks in chunks_by_doc.values() for chunk in chunks]
            ai_enhancement = enhance_chunks(all_chunks)
        except Exception:
            ai_enhancement = {"enabled": True, "enhanced_chunks": 0, "total": 0, "error": "ai_enhance_failed"}

    _write_jsonl(PROCESSED_DIR / "documents.jsonl", [document.to_dict() for document in documents])
    all_chunks = [chunk for chunks in chunks_by_doc.values() for chunk in chunks]
    _write_jsonl(PROCESSED_DIR / "chunks.jsonl", [chunk.to_dict() for chunk in all_chunks])

    if force or not db_path.exists():
        rebuild(db_path, documents, all_chunks)
        stats = {
            "added": len(documents),
            "changed": 0,
            "removed": 0,
            "unchanged": 0,
            "documents": len(documents),
            "chunks": len(all_chunks),
        }
        mode = "full"
    else:
        stats = incremental_sync(db_path, documents, chunks_by_doc, force=False)
        mode = "incremental"

    result = {
        "status": "ok",
        "mode": mode,
        "database": str(db_path),
        "faq_units": len(faq_units),
        "authority_tiers": dict(Counter(document.authority_tier for document in documents)),
        "parse_status": dict(Counter(document.parse_status for document in documents)),
        "build_ms": int((time.time() - started) * 1000),
        "ai_enhancement": ai_enhancement,
        **stats,
    }
    return result


def corpus_stats(db_path: Path = DB_PATH) -> Dict[str, object]:
    if not db_path.exists():
        return {"documents": 0, "chunks": 0, "authority_tiers": {}, "parse_status": {}}
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


# ═══════════════════════════════════════════════════════════════
# 知识库注入：把纯文本自动整理为 RAG 问答条目（追加 qa/新生常见问题.md 后重建索引）
# 输入约定：
#   - 每条用空行分隔；支持「问题：xxx / 回答：yyy」或「Q1: xxx + 正文」
#   - 无显式问题的纯文本：首句（问句优先）作为问题，其余为回答
# ═══════════════════════════════════════════════════════════════
def parse_inject_units(text: str) -> List[Dict[str, str]]:
    units: List[Dict[str, str]] = []
    blocks = re.split(r"\n\s*\n", text or "")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        question = None
        answer_lines: List[str] = []
        for line in lines:
            m = re.match(r"^(?:问题|问)\s*[:：]\s*(.*)$", line)
            if m and question is None:
                question = m.group(1).strip()
                continue
            m2 = re.match(r"^Q\d+\s*[:：]\s*(.*)$", line)
            if m2 and question is None:
                question = m2.group(1).strip()
                continue
            m3 = re.match(r"^(?:回答|答)\s*[:：]\s*(.*)$", line)
            if m3 and not answer_lines:
                answer_lines.append(m3.group(1).strip())
                continue
            answer_lines.append(line)
        if question is None:
            first = lines[0]
            if ("？" in first or "?" in first) and len(lines) > 1:
                question = first
                answer_lines = lines[1:]
            else:
                question = first[:24] + ("…" if len(first) > 24 else "")
                answer_lines = lines
        answer = "\n".join(answer_lines).strip()
        if not answer:
            answer = question
        units.append({"question": question, "answer": answer})
    return units


def inject_knowledge(text: str, db_path: Path = DB_PATH) -> Dict[str, object]:
    """解析文本 → 追加到独立文件 qa/问答补充.md（不污染新生常见问题.md 等 QA 展示文件）→ 增量重建索引。
    RAG 自动收录 qa/ 下所有非彩蛋 md，新文件无需额外配置。"""
    units = parse_inject_units(text)
    if not units:
        return {"ok": False, "error": "没有可解析的内容", "added": 0}
    faq_path = QA_DIR / "问答补充.md"
    existing = faq_path.read_text(encoding="utf-8") if faq_path.exists() else ""
    max_n = 0
    for m in re.finditer(r"## Q(\d+)\s*:", existing):
        max_n = max(max_n, int(m.group(1)))
    blocks = []
    for i, unit in enumerate(units, start=max_n + 1):
        blocks.append(f"## Q{i}: {unit['question']}\n\n{unit['answer']}\n")
    with faq_path.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write("\n" + "\n".join(blocks))
    # 增量同步只覆盖已注册文档，新 md 文件必须全量重建才会被 RAG 收录
    result = build_knowledge_base(db_path, force=True)
    return {"ok": True, "added": len(units), "questions": [u["question"] for u in units], "build": result}
