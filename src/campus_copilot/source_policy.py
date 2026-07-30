import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceDecision:
    source_kind: str
    authority_tier: str
    assertion_policy: str
    issuer: str
    privacy_risk: str = "low"


OFFICIAL_DOC_PATTERNS = (
    re.compile(r"〔20\d{2}〕\d+号"),
    re.compile(r"关于印发.*(?:办法|规定|通知)"),
)

SENSITIVE_RECORD_TERMS = ("积分", "志愿时长记录", "参与加分", "名单", "成绩记录")
STUDENT_REFERENCE_TERMS = ("入学指南", "家具尺寸统计表")


def _looks_official(title: str, tags: list) -> bool:
    return "官方文件" in tags or any(pattern.search(title) for pattern in OFFICIAL_DOC_PATTERNS)


def classify_file(title: str, media_type: str, tags: list) -> SourceDecision:
    tags = tags or []
    privacy_risk = "review_required" if any(term in title for term in SENSITIVE_RECORD_TERMS) else "low"
    if _looks_official(title, tags):
        return SourceDecision("policy", "official_policy", "assert_with_citation", "中国传媒大学", privacy_risk)
    if any(term in title for term in STUDENT_REFERENCE_TERMS):
        return SourceDecision(
            "student_reference",
            "peer_experience",
            "label_as_experience",
            "Student contributor",
            privacy_risk,
        )
    if any(term in title for term in ("学生手册", "服务手册", "校历", "考试标准", "评分标准")):
        return SourceDecision("official_guidance", "official_guidance", "assert_if_current", "中国传媒大学或相关办学机构", privacy_risk)
    if media_type in {"word", "excel"} and any(term in title for term in ("申请表", "审批表")):
        return SourceDecision("form", "resource", "navigation_only", "Unknown", privacy_risk)
    if media_type == "image":
        return SourceDecision("visual_reference", "unverified", "do_not_assert_until_ocr", "Unknown", privacy_risk)
    return SourceDecision("reference_document", "unverified", "do_not_assert", "Unknown", privacy_risk)


def classify_link(title: str, url: str, tags: list) -> SourceDecision:
    host = (urlparse(url).hostname or "").lower()
    if host == "cuc.edu.cn" or host.endswith(".cuc.edu.cn"):
        return SourceDecision("official_link", "official_guidance", "navigation_only", "中国传媒大学")
    return SourceDecision("community_link", "resource", "navigation_only", "Third party")


def faq_decision() -> SourceDecision:
    return SourceDecision("peer_faq", "peer_experience", "label_as_experience", "Student contributor")


# Confidence is retained for display and near-tie ordering. It must not
# multiplicatively suppress a highly relevant student-authored passage.
AUTHORITY_CONFIDENCE = {
    "official_policy": 1.0,
    "official_guidance": 0.9,
    "peer_experience": 0.8,
    "resource": 0.6,
    "unverified": 0.5,
}
