from typing import Dict, List, Optional


SYSTEM_RULES = [
    "Only use supplied evidence; do not rely on model memory.",
    "Cite evidence IDs in every factual paragraph.",
    "Label peer_experience as student experience, never as an official rule.",
    "Preserve uncertainty words such as likely, inferred, expected, and unknown.",
    "If official evidence is required but missing, say what must be confirmed and abstain.",
    "Respect cohort, major, campus, and effective-date applicability.",
]

GENERATABLE_MODES = {
    "supported",
    "supported_with_context",
    "supported_freshness_unverified",
    "mixed_sources_review_required",
    "experience_only",
}


def build_context_packet(
    retrieval: Dict[str, object], conversation_history: Optional[List[Dict[str, object]]] = None
) -> Dict[str, object]:
    status = str(retrieval.get("answerability", "insufficient"))
    can_generate = status in GENERATABLE_MODES
    evidence: List[Dict[str, object]] = []
    for index, result in enumerate(retrieval.get("results", []), start=1):
        evidence.append({
            "evidence_id": f"S{index}",
            "title": result["title"],
            "chunk_type": result.get("chunk_type", "document"),
            "authority_tier": result["authority_tier"],
            "assertion_policy": result["assertion_policy"],
            "page_number": result["page_number"],
            "heading_path": result["heading_path"],
            "source_url": result["source_url"],
            "file_path": result.get("file_path"),
            "media_type": result.get("media_type"),
            "image_thumb": result.get("image_thumb"),
            "published_at": result["published_at"],
            "effective_from": result["effective_from"],
            "uploaded_at": result["uploaded_at"],
            "date_status": result["date_status"],
            "student_level": result["student_level"],
            "uncertainty": result["uncertainty"],
            "text": result["text"],
        })
    return {
        "query": retrieval.get("query"),
        "conversation_history": (conversation_history or [])[-8:],
        "can_generate": can_generate,
        "response_mode": status,
        "insufficient_reason": retrieval.get("insufficient_reason"),
        "system_rules": SYSTEM_RULES,
        "evidence": evidence,
        "model_contract_version": "campus-grounding-v2",
    }


def build_citation_metadata(context_packet: Dict[str, object]) -> Dict[str, object]:
    """Build the {S1: {title, file_path, type, image_thumb, url, page, authority, ...}}
    map that the frontend renders as source cards."""
    metadata: Dict[str, object] = {}
    for item in context_packet.get("evidence", []):
        evidence_id = str(item.get("evidence_id"))
        metadata[evidence_id] = {
            "evidence_id": evidence_id,
            "title": item.get("title"),
            "file_path": item.get("file_path"),
            "type": item.get("media_type"),
            "image_thumb": item.get("image_thumb"),
            "url": item.get("source_url") if item.get("media_type") == "link" else None,
            "page": item.get("page_number"),
            "authority": item.get("authority_tier"),
            "assertion_policy": item.get("assertion_policy"),
            "chunk_type": item.get("chunk_type"),
            "excerpt": (str(item.get("text") or "")[:300]),
        }
    return metadata


def build_visual_evidence(retrieval: Dict[str, object]) -> List[Dict[str, object]]:
    """Build frontend-ready image/QR source cards from visual evidence.

    These are supplementary "related image" cards: they never make a query
    answerable (answerability is decided by textual evidence only) but the
    frontend renders them under "相关图片佐证" so users can look at the image.
    """
    cards: List[Dict[str, object]] = []
    for index, item in enumerate(retrieval.get("visual_evidence", []), start=1):
        cards.append({
            "evidence_id": f"V{index}",
            "title": item.get("title"),
            "file_path": item.get("file_path"),
            "type": item.get("media_type"),
            "image_thumb": item.get("image_thumb"),
            "url": item.get("source_url") if item.get("media_type") == "link" else None,
            "authority": item.get("authority_tier"),
            "assertion_policy": item.get("assertion_policy"),
            "chunk_type": item.get("chunk_type"),
            "excerpt": (str(item.get("text") or "")[:200]),
        })
    return cards
