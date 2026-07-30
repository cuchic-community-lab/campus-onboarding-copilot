import hashlib
import re
from typing import Dict, Iterable, List, Optional, Tuple

from .models import ChunkRecord, DocumentRecord


UNCERTAINTY_MARKERS = (
    "大概率", "推测", "可能", "未知", "据说", "据观察", "预计", "恐系", "理应"
)


def _clean_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    return re.sub(r"[ \t]+", " ", text).strip()


def _chunk_id(document_id: str, sequence: int, text: str) -> str:
    value = f"{document_id}:{sequence}:{text}"
    return "chunk-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _make_chunk(
    document: DocumentRecord,
    text: str,
    sequence: int,
    chunk_type: str,
    heading_path: str = "",
    page_number: Optional[int] = None,
) -> ChunkRecord:
    normalized = _clean_markdown(text)
    uncertainty = [marker for marker in UNCERTAINTY_MARKERS if marker in normalized]
    undergraduate_hits = len(re.findall(r"本科", normalized))
    graduate_hits = len(re.findall(r"研究生", normalized))
    student_level = document.student_level
    if undergraduate_hits and undergraduate_hits >= graduate_hits:
        student_level = "undergraduate"
    elif graduate_hits:
        student_level = "graduate"
    return ChunkRecord(
        chunk_id=_chunk_id(document.document_id, sequence, normalized),
        document_id=document.document_id,
        title=document.title,
        text=normalized,
        chunk_type=chunk_type,
        sequence=sequence,
        heading_path=heading_path,
        page_number=page_number,
        tags=document.tags,
        authority_tier=document.authority_tier,
        assertion_policy=document.assertion_policy,
        source_url=document.source_url,
        cohort=document.cohort,
        academic_year=document.academic_year,
        student_level=student_level,
        major=document.major,
        campus=document.campus,
        uncertainty=uncertainty,
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def chunk_faq(document: DocumentRecord, units: List[Dict[str, str]]) -> List[ChunkRecord]:
    chunks: List[ChunkRecord] = []
    for index, unit in enumerate(units):
        text = f"问题：{unit['question']}\n回答：{unit['answer']}"
        chunks.append(_make_chunk(document, text, index, "faq", unit.get("category", "")))
    return chunks


def _paragraphs(text: str) -> List[str]:
    lines = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
    return [line for line in lines if line not in {"---"}]


def chunk_paragraphs(
    document: DocumentRecord,
    text: str,
    page_number: Optional[int] = None,
    target_chars: int = 550,
    max_chars: int = 850,
) -> List[ChunkRecord]:
    paragraphs = _paragraphs(text)
    chunks: List[ChunkRecord] = []
    buffer: List[str] = []
    heading = ""

    def flush() -> None:
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        if body:
            chunks.append(_make_chunk(document, body, len(chunks), "document", heading, page_number))
        buffer.clear()

    for paragraph in paragraphs:
        if re.match(r"^(第[一二三四五六七八九十\d]+[章节步]|[一二三四五六七八九十\d]+[、.．])", paragraph):
            heading = paragraph[:80]
        prospective = "\n".join(buffer + [paragraph])
        if buffer and len(prospective) > max_chars:
            overlap = buffer[-1] if len(buffer[-1]) <= 140 else ""
            flush()
            if overlap:
                buffer.append(overlap)
        buffer.append(paragraph)
        if len("\n".join(buffer)) >= target_chars and paragraph.endswith(("。", "！", "？", ":", "：")):
            flush()
    flush()
    return chunks


def chunk_structured_rows(document: DocumentRecord) -> List[ChunkRecord]:
    """Keep each spreadsheet record atomic while preserving its sheet name."""
    chunks: List[ChunkRecord] = []
    sheet = ""
    row_parts: List[str] = []

    def flush() -> None:
        if not row_parts:
            return
        row = " ".join(row_parts).strip()
        row_parts.clear()
        # Standalone sequence numbers and empty spreadsheet rows are not facts.
        if "|" not in row or not re.search(r"\d", row):
            return
        row = re.sub(r"\s*\|\s*=DISPIMG\(.*\)\s*$", "", row)
        text = f"{sheet}\n{row}" if sheet else row
        chunks.append(_make_chunk(document, text, len(chunks), "structured_fact", sheet))

    for raw_line in document.content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("工作表："):
            flush()
            sheet = line.removeprefix("工作表：").strip()
            continue
        if re.match(r"^\d+\s*\|", line):
            flush()
            row_parts.append(line)
        elif row_parts and not line.isdigit():
            row_parts.append(line)
    flush()
    return chunks


def chunk_document(document: DocumentRecord) -> List[ChunkRecord]:
    if document.source_kind in {"official_link", "community_link", "form"}:
        if not document.content:
            return []
        return [_make_chunk(document, document.content, 0, "resource")]
    if document.parse_status != "parsed":
        return []
    if document.media_type == "excel":
        structured = chunk_structured_rows(document)
        if structured:
            return structured
    if document.pages:
        result: List[ChunkRecord] = []
        for page in document.pages:
            page_chunks = chunk_paragraphs(
                document,
                str(page.get("text", "")),
                page_number=int(page.get("page_number", 0)) or None,
            )
            for chunk in page_chunks:
                chunk.sequence = len(result)
                chunk.chunk_id = _chunk_id(document.document_id, chunk.sequence, chunk.text)
                result.append(chunk)
        return result
    return chunk_paragraphs(document, document.content)
