import re
from typing import Dict, Iterable, List, Set


CREDENTIAL_OBJECT_TERMS = (
    "毕业证", "毕业证书", "学位证", "学位证书", "双证", "证书", "学信网",
)
JOINT_PROGRAM_TERMS = ("中外合办", "中外合作办学", "合作办学")
CREDENTIAL_WORDING_TERMS = (
    "字样", "标注", "写着", "印有", "显示", "版式", "样式", "一样吗", "区别",
    "没有不同", "完全一致", "没有区别", "相同",
)
VISUAL_COMMUNICATION_TERMS = ("视觉传达设计", "视觉传达", "视传")
EXCLUSIVITY_TERMS = (
    "只有", "仅有", "仅开设", "只开设", "没有普通", "无普通", "非中外合办",
    "非中外合作办学", "普通版本", "普通专业",
)
EXHAUSTIVE_SCOPE_TERMS = (
    "招生专业", "专业一览", "专业目录", "全部专业", "开设专业", "本科专业",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    return any(term.lower() in compact for term in terms)


def required_aspects(query: str, question_type: str) -> Set[str]:
    if question_type == "program_offering":
        aspects = {"program", "joint_program"}
        if _contains_any(query, EXCLUSIVITY_TERMS):
            aspects.add("exclusive_scope")
        return aspects
    if question_type != "credential_wording":
        return set()
    aspects = {"credential"}
    if _contains_any(query, JOINT_PROGRAM_TERMS):
        aspects.add("joint_program")
    if _contains_any(query, CREDENTIAL_WORDING_TERMS):
        aspects.add("wording")
    return aspects


def covered_aspects(text: str, question_type: str) -> Set[str]:
    if question_type == "program_offering":
        aspects: Set[str] = set()
        if _contains_any(text, VISUAL_COMMUNICATION_TERMS):
            aspects.add("program")
        if _contains_any(text, JOINT_PROGRAM_TERMS):
            aspects.add("joint_program")
        if _contains_any(text, EXCLUSIVITY_TERMS) or _contains_any(text, EXHAUSTIVE_SCOPE_TERMS):
            aspects.add("exclusive_scope")
        return aspects
    if question_type != "credential_wording":
        return set()
    aspects: Set[str] = set()
    if _contains_any(text, CREDENTIAL_OBJECT_TERMS):
        aspects.add("credential")
    if _contains_any(text, JOINT_PROGRAM_TERMS):
        aspects.add("joint_program")
    if _contains_any(text, CREDENTIAL_WORDING_TERMS):
        aspects.add("wording")
    return aspects


def score_evidence_coverage(query: str, question_type: str, result: Dict[str, object]) -> Dict[str, object]:
    text = " ".join(
        str(result.get(field, ""))
        for field in ("title", "heading_path", "text")
    )
    required = required_aspects(query, question_type)
    covered = covered_aspects(text, question_type)
    if not required:
        return {
            "required_aspects": [],
            "covered_aspects": [],
            "coverage_ratio": 1.0,
            "covers_question_object": True,
            "direct_answer": True,
        }
    ratio = len(required.intersection(covered)) / len(required)
    object_aspect = "program" if question_type == "program_offering" else "credential"
    covers_object = object_aspect in covered
    if question_type == "program_offering":
        direct_answer = required.issubset(covered)
    else:
        direct_answer = covers_object and "wording" in covered
    return {
        "required_aspects": sorted(required),
        "covered_aspects": sorted(covered),
        "coverage_ratio": round(ratio, 4),
        "covers_question_object": covers_object,
        "direct_answer": direct_answer,
    }


def rank_and_filter_evidence(
    query: str,
    question_type: str,
    results: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    ranked: List[Dict[str, object]] = []
    authority_rank = {
        "official_web": 4,
        "official_policy": 4,
        "official_guidance": 3,
        "public_web": 2,
        "peer_experience": 1,
    }
    for result in results:
        item = dict(result)
        coverage = score_evidence_coverage(query, question_type, item)
        explanation = dict(item.get("score_explanation", {}))
        explanation["question_coverage"] = coverage
        item["score_explanation"] = explanation
        if question_type == "credential_wording":
            covered = set(coverage["covered_aspects"])
            if not coverage["covers_question_object"] or not covered.intersection({"joint_program", "wording"}):
                continue
        if question_type == "program_offering":
            covered = set(coverage["covered_aspects"])
            if "program" not in covered or "joint_program" not in covered:
                continue
        item["evidence_coverage"] = coverage
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            1 if item["evidence_coverage"]["direct_answer"] else 0,
            float(item["evidence_coverage"]["coverage_ratio"]),
            authority_rank.get(str(item.get("authority_tier")), 0),
            float(item.get("score", 0.0)),
        ),
        reverse=True,
    )
    return ranked
