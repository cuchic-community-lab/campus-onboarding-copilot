import json
from pathlib import Path
from typing import Dict, List

from .config import DB_PATH, PROJECT_ROOT
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
