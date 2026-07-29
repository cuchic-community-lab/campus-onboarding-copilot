from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class DocumentRecord:
    document_id: str
    title: str
    source_url: str
    local_path: str
    media_type: str
    source_kind: str
    authority_tier: str
    assertion_policy: str
    issuer: str = "Unknown"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    uploaded_at: Optional[str] = None
    published_at: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    date_status: str = "unknown"
    cohort: Optional[str] = None
    academic_year: Optional[str] = None
    student_level: str = "all"
    major: Optional[str] = None
    campus: str = "CUCHIC"
    checksum: Optional[str] = None
    parse_status: str = "pending"
    content: str = ""
    pages: List[Dict[str, object]] = field(default_factory=list)
    privacy_risk: str = "low"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    title: str
    text: str
    chunk_type: str
    sequence: int
    heading_path: str = ""
    page_number: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    authority_tier: str = "unverified"
    assertion_policy: str = "do_not_assert"
    source_url: str = ""
    cohort: Optional[str] = None
    academic_year: Optional[str] = None
    student_level: str = "all"
    major: Optional[str] = None
    campus: str = "CUCHIC"
    uncertainty: List[str] = field(default_factory=list)
    content_hash: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
