import json
from pathlib import Path
from typing import Dict, List

from .config import DB_PATH, PROJECT_ROOT
from .chat import GroundedChatService
from .retrieval import HybridRetriever


DEFAULT_GOLDEN_SET = PROJECT_ROOT / "evaluation" / "golden_questions.jsonl"


def evaluate(path: Path = DEFAULT_GOLDEN_SET) -> Dict[str, object]:
    retriever = HybridRetriever(DB_PATH)
    cases: List[Dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(json.loads(line))
    details: List[Dict[str, object]] = []
    title_hits = authority_hits = answerability_hits = 0
    for case in cases:
        result = retriever.search(str(case["query"]), 5, case.get("profile") or {})
        top = result["results"][0] if result["results"] else {}
        title_ok = str(case["expected_title_contains"]) in str(top.get("title", ""))
        authority_ok = top.get("authority_tier") == case["expected_authority"]
        answerability_ok = result["answerability"] in case["allowed_answerability"]
        title_hits += int(title_ok)
        authority_hits += int(authority_ok)
        answerability_hits += int(answerability_ok)
        details.append({
            "id": case["id"],
            "title_ok": title_ok,
            "authority_ok": authority_ok,
            "answerability_ok": answerability_ok,
            "actual_title": top.get("title"),
            "actual_authority": top.get("authority_tier"),
            "actual_answerability": result["answerability"],
        })
    total = len(cases) or 1
    return {
        "cases": len(cases),
        "title_hit_at_1": round(title_hits / total, 3),
        "authority_accuracy_at_1": round(authority_hits / total, 3),
        "answerability_accuracy": round(answerability_hits / total, 3),
        "all_passed": title_hits == authority_hits == answerability_hits == len(cases),
        "details": details,
    }


def evaluate_chat(path: Path = DEFAULT_GOLDEN_SET) -> Dict[str, object]:
    """Contract evaluation; factual answer quality still requires human labels."""
    cases: List[Dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(json.loads(line))
    service = GroundedChatService(HybridRetriever(DB_PATH))
    details: List[Dict[str, object]] = []
    citation_hits = refusal_hits = experience_hits = 0
    for case in cases:
        result = service.ask(str(case["query"]), profile=case.get("profile") or {}, top_k=5)
        evidence_ids = {str(item["evidence_id"]) for item in result["evidence"]}
        citations = result["citations"]
        citation_ok = all(item in evidence_ids and f"[{item}]" in result["answer"] for item in citations)
        citation_ok = citation_ok and (bool(citations) or result["answerability"].startswith("insufficient"))
        refusal_expected = result["answerability"].startswith("insufficient") or result["answerability"] == "unverified"
        refusal_ok = (not result["claims"] and not citations) if refusal_expected else True
        authority_by_id = {item["evidence_id"]: item["authority_tier"] for item in result["evidence"]}
        peer_claims = [
            claim for claim in result["claims"]
            if any(authority_by_id.get(evidence_id) == "peer_experience" for evidence_id in claim.get("evidence_ids", []))
        ]
        experience_ok = not peer_claims or (
            "学生经验" in result["answer"]
            and all(claim.get("certainty") == "experience" for claim in peer_claims)
        )
        citation_hits += int(citation_ok)
        refusal_hits += int(refusal_ok)
        experience_hits += int(experience_ok)
        details.append({
            "id": case["id"],
            "citation_contract_ok": citation_ok,
            "refusal_contract_ok": refusal_ok,
            "peer_label_ok": experience_ok,
            "answerability": result["answerability"],
            "composer": result["composer"],
        })
    total = len(cases) or 1
    return {
        "cases": len(cases),
        "composer": service.status(),
        "citation_contract_accuracy": round(citation_hits / total, 3),
        "refusal_contract_accuracy": round(refusal_hits / total, 3),
        "peer_label_accuracy": round(experience_hits / total, 3),
        "all_contracts_passed": citation_hits == refusal_hits == experience_hits == len(cases),
        "limitation": "This checks grounding contracts, not human-rated factual completeness or answer usefulness.",
        "details": details,
    }
