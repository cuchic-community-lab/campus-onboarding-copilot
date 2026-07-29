import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import quote, urljoin

from .config import HOME_URL, RAW_DIR, RAW_FILES_DIR
from .models import DocumentRecord
from .source_policy import classify_file, classify_link, faq_decision


def stable_id(prefix: str, value: str) -> str:
    return prefix + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_document_metadata(record: DocumentRecord) -> None:
    sample = (record.content or "")[:3000]
    if "学生手册" in record.title:
        # A handbook is a compendium of many rules with different dates. A
        # random date found in its table of contents is not the handbook date.
        record.published_at = None
        record.date_status = "mixed_compendium"
    spaced_date = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
    dates = spaced_date.findall(sample)
    if dates and record.authority_tier in {"official_policy", "official_guidance"} and record.date_status != "mixed_compendium":
        year, month, day = dates[-1]
        record.published_at = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        record.date_status = "document_date"
    elif record.authority_tier == "official_guidance" and record.date_status != "mixed_compendium":
        month_match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", (record.title + " " + sample[:800]))
        if month_match:
            record.published_at = f"{int(month_match.group(1)):04d}-{int(month_match.group(2)):02d}"
            record.date_status = "document_month"
    academic = re.search(r"(20\d{2})\s*[-—至]\s*(20\d{2})\s*学年", record.title + " " + sample[:1000])
    if academic:
        record.academic_year = f"{academic.group(1)}-{academic.group(2)}"
    title_and_sample = record.title + " " + sample[:1200]
    undergraduate_hits = len(re.findall(r"本科", title_and_sample))
    graduate_hits = len(re.findall(r"研究生", title_and_sample))
    if undergraduate_hits and undergraduate_hits >= graduate_hits:
        record.student_level = "undergraduate"
    elif graduate_hits:
        record.student_level = "graduate"
    elif "新生" in record.tags:
        record.student_level = "undergraduate"


def _extract_pdf(path: Path) -> Tuple[str, List[Dict[str, object]], str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return "", [], "metadata_only_missing_pypdf"
    try:
        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append({"page_number": index, "text": text})
        content = "\n\n".join(str(page["text"]) for page in pages if page["text"])
        if not content:
            return "", pages, "ocr_required"
        return content, pages, "parsed"
    except Exception:
        return "", [], "parse_error"


def _extract_docx(path: Path) -> Tuple[str, str]:
    try:
        from docx import Document  # type: ignore
    except ImportError:
        return "", "metadata_only_missing_python_docx"
    try:
        doc = Document(str(path))
        text = "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
        return text, "parsed" if text else "empty"
    except Exception:
        return "", "parse_error"


def _extract_xlsx(path: Path, privacy_risk: str) -> Tuple[str, str]:
    if privacy_risk != "low":
        return "", "excluded_privacy_review"
    try:
        from openpyxl import load_workbook  # type: ignore
    except ImportError:
        return "", "metadata_only_missing_openpyxl"
    try:
        book = load_workbook(str(path), read_only=True, data_only=True)
        lines: List[str] = []
        for sheet in book.worksheets:
            lines.append("工作表：" + sheet.title)
            for row in sheet.iter_rows(values_only=True):
                values = [str(value).strip() for value in row if value not in (None, "")]
                if values:
                    lines.append(" | ".join(values))
                if len(lines) >= 500:
                    break
        return "\n".join(lines), "parsed" if lines else "empty"
    except Exception:
        return "", "parse_error"


def load_faq_units(path: Path = RAW_DIR / "qa-response.json") -> Tuple[str, List[Dict[str, str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    markdown = str(payload.get("content", ""))
    category = "General"
    question = ""
    answer_lines: List[str] = []
    units: List[Dict[str, str]] = []

    def flush() -> None:
        nonlocal answer_lines
        if question:
            answer = "\n".join(answer_lines).strip()
            units.append({"category": category, "question": question, "answer": answer})
        answer_lines = []

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            flush()
            category = re.sub(r"^##\s+", "", line).strip()
            question = ""
        elif line.startswith("### "):
            flush()
            question = re.sub(r"^###\s+", "", line).strip()
        elif question and line not in {"---"}:
            answer_lines.append(line)
    flush()
    return markdown, units


def load_documents() -> Tuple[List[DocumentRecord], List[Dict[str, str]]]:
    manifest = json.loads((RAW_DIR / "files.json").read_text(encoding="utf-8"))
    documents: List[DocumentRecord] = []
    for item in manifest.get("files", []):
        title = str(item.get("name", "Untitled"))
        media_type = str(item.get("type", "unknown"))
        tags = list(item.get("tags", []))
        decision = classify_file(title, media_type, tags)
        relative = str(item.get("path", "")).replace("\\", "/")
        local = RAW_FILES_DIR / Path(relative).name
        source_url = urljoin(HOME_URL, quote(relative, safe="/"))
        record = DocumentRecord(
            document_id=stable_id("doc", source_url),
            title=title,
            source_url=source_url,
            local_path=str(local),
            media_type=media_type,
            source_kind=decision.source_kind,
            authority_tier=decision.authority_tier,
            assertion_policy=decision.assertion_policy,
            issuer=decision.issuer,
            description=str(item.get("desc", "")),
            tags=tags,
            uploaded_at=item.get("uploadTime"),
            privacy_risk=decision.privacy_risk,
        )
        if local.exists():
            record.checksum = sha256_file(local)
            if media_type == "pdf":
                record.content, record.pages, record.parse_status = _extract_pdf(local)
            elif media_type == "word":
                record.content, record.parse_status = _extract_docx(local)
            elif media_type == "excel":
                record.content, record.parse_status = _extract_xlsx(local, record.privacy_risk)
            elif media_type == "image":
                record.parse_status = "ocr_required"
            else:
                record.parse_status = "metadata_only"
        else:
            record.parse_status = "not_downloaded"
        _normalize_document_metadata(record)
        documents.append(record)

    for link in manifest.get("links", []):
        title = str(link.get("title", "Untitled link"))
        url = str(link.get("url", ""))
        tags = list(link.get("tags", []))
        decision = classify_link(title, url, tags)
        documents.append(DocumentRecord(
            document_id=stable_id("link", url),
            title=title,
            source_url=url,
            local_path="",
            media_type="link",
            source_kind=decision.source_kind,
            authority_tier=decision.authority_tier,
            assertion_policy=decision.assertion_policy,
            issuer=decision.issuer,
            description=str(link.get("desc", "")),
            tags=tags,
            parse_status="resource_only",
            content=(title + "\n" + str(link.get("desc", ""))).strip(),
            student_level="undergraduate" if "新生" in tags or "选课" in tags else "all",
        ))

    markdown, faq_units = load_faq_units()
    faq = faq_decision()
    documents.append(DocumentRecord(
        document_id=stable_id("faq", HOME_URL + "#qa"),
        title="新生入学 Q&A 百问百答",
        source_url=HOME_URL + "#qa",
        local_path=str(RAW_DIR / "qa-response.json"),
        media_type="markdown",
        source_kind=faq.source_kind,
        authority_tier=faq.authority_tier,
        assertion_policy=faq.assertion_policy,
        issuer=faq.issuer,
        description="师哥师姐经验问答；非官方表述",
        tags=["新生", "Q&A", "学生经验"],
        cohort="2026",
        student_level="undergraduate",
        parse_status="parsed",
        content=markdown,
        checksum=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    ))
    return documents, faq_units
