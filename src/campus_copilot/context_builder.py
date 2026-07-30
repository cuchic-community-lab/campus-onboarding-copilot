from typing import Dict, List, Optional

from .answer_planning import build_answer_plan


SYSTEM_RULES = [
    "Only use supplied evidence; do not rely on model memory.",
    "Cite evidence IDs in every factual paragraph.",
    "Label peer_experience as student experience, never as an official rule.",
    "Preserve uncertainty words such as likely, inferred, expected, and unknown.",
    "If official evidence is required but missing, say what must be confirmed and abstain.",
    "Respect cohort, major, campus, and effective-date applicability.",
    "Official web evidence may support school facts. Public web evidence must be labeled as a public reference and cannot be upgraded into an official school claim.",
    "Never present a historical arrival notice or a public-web recommendation as the current official requirement.",
]

GENERATABLE_MODES = {
    "supported",
    "supported_with_context",
    "supported_freshness_unverified",
    "mixed_sources_review_required",
    "experience_only",
    "web_supported",
    "public_web_supported",
    "web_supported_mixed",
    "supported_with_unresolved_wording",
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
            "published_at": result["published_at"],
            "effective_from": result["effective_from"],
            "uploaded_at": result["uploaded_at"],
            "date_status": result["date_status"],
            "student_level": result["student_level"],
            "uncertainty": result["uncertainty"],
            "text": result["text"],
            "retrieval_origin": result.get("retrieval_origin", "local_knowledge"),
            "web_source_kind": result.get("web_source_kind"),
            "fetched_at": result.get("fetched_at"),
            "evidence_coverage": result.get("evidence_coverage"),
        })
    answer_plan = build_answer_plan(str(retrieval.get("query", "")), status)
    web_search = retrieval.get("web_search") or {}
    answer_plan.update({
        "web_search_executed": bool(web_search.get("executed")),
        "web_search_provider": web_search.get("provider"),
        "web_search_status": web_search.get("status"),
        "web_search_routes": web_search.get("routes", []),
    })
    return {
        "query": retrieval.get("query"),
        "conversation_history": (conversation_history or [])[-8:],
        "can_generate": can_generate,
        "response_mode": status,
        "insufficient_reason": retrieval.get("insufficient_reason"),
        "system_rules": SYSTEM_RULES,
        "answer_plan": answer_plan,
        "evidence": evidence,
        "model_contract_version": "campus-grounding-v4-governed-web",
    }
