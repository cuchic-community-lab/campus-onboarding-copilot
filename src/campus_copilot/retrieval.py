import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .db import chinese_search_terms, connect
from .source_policy import AUTHORITY_CONFIDENCE


SYNONYMS = {
    "宿舍": ["寝室", "生活区", "床位"],
    "寝室": ["宿舍", "生活区", "床位"],
    "选课": ["课程", "选修课", "选课系统"],
    "报到": ["入学", "新生", "信息采集"],
    "交通": ["摆渡车", "公交", "出行"],
    "成绩": ["绩点", "gpa", "学分"],
    "吃饭": ["食堂", "餐饮", "饭卡"],
    "床": ["床铺", "床垫"],
    "多大": ["尺寸", "规格", "长", "宽"],
    "尺寸": ["规格", "长", "宽"],
}

POLICY_TERMS = ("必须", "规定", "办法", "申请", "截止", "毕业", "选课", "成绩", "请假", "军训", "办理")
LIFESTYLE_TERMS = ("宿舍", "食堂", "快递", "生活费", "好吃", "外卖", "交通", "社团")

CONCEPT_TERMS = (
    "信息采集", "美团外卖", "补退选", "每学期", "几人间", "选课系统",
    "宿舍", "寝室", "选课", "退课", "规定", "操作", "步骤", "快递", "地址",
    "外卖", "食堂", "学分", "最低", "游泳", "必修", "考试", "评分", "报到",
    "军训", "请假", "校历", "摆渡车", "交通", "绩点", "gpa", "社团", "四级",
    "床垫", "床铺", "尺寸", "规格", "多大", "长", "宽", "床",
)

DIMENSION_QUERY_TERMS = ("多大", "尺寸", "规格", "长宽", "多长", "多宽", "厘米", "cm")
DIMENSION_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:m|米|cm|厘米|mm|毫米)|\d+\s*[x×*]\s*\d+)",
    re.IGNORECASE,
)


def expand_query(query: str) -> str:
    additions: List[str] = []
    for key, values in SYNONYMS.items():
        if key in query.lower():
            additions.extend(values)
    return " ".join([query] + additions)


class LocalSubwordVectorizer:
    """Deterministic offline similarity baseline; replaceable with embeddings."""

    def __init__(self, dimensions: int = 2048):
        self.dimensions = dimensions

    def features(self, text: str) -> Counter:
        normalized = re.sub(r"\s+", "", expand_query(text).lower())
        grams: List[str] = []
        for size in (1, 2, 3):
            grams.extend(normalized[i:i + size] for i in range(max(0, len(normalized) - size + 1)))
        features: Counter = Counter()
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            features[int.from_bytes(digest, "big") % self.dimensions] += 1
        norm = math.sqrt(sum(value * value for value in features.values())) or 1.0
        for key in list(features):
            features[key] /= norm
        return features

    @staticmethod
    def cosine(left: Counter, right: Counter) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(key, 0.0) for key, value in left.items())


def _intent(query: str) -> str:
    if any(term in query for term in POLICY_TERMS):
        return "policy_or_procedure"
    if any(term in query for term in LIFESTYLE_TERMS):
        return "campus_life"
    return "general"


def _query_concepts(query: str) -> List[str]:
    lowered = expand_query(query).lower()
    concepts: List[str] = []
    for term in sorted(CONCEPT_TERMS, key=len, reverse=True):
        if term in lowered and term not in concepts:
            concepts.append(term)
    if concepts:
        return concepts
    candidates = chinese_search_terms(query).split()
    return list(dict.fromkeys(term for term in candidates if len(term) >= 2))[:10]


def _coverage(concepts: List[str], text: str) -> float:
    if not concepts:
        return 0.0
    total = sum(max(1, len(term)) for term in concepts)
    matched = sum(max(1, len(term)) for term in concepts if term in text.lower())
    return matched / total if total else 0.0


def _fts_query(query: str) -> str:
    terms = chinese_search_terms(expand_query(query)).split()
    unique: List[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if term not in unique and len(term) >= 2:
            unique.append(term)
        if len(unique) >= 18:
            break
    if not unique:
        unique = [term for term in terms if term][:8]
    return " OR ".join('"' + term.replace('"', '') + '"' for term in unique)


def _row_to_result(row: sqlite3.Row) -> Dict[str, object]:
    return {
        "chunk_id": row["chunk_id"],
        "document_id": row["document_id"],
        "title": row["title"],
        "text": row["text"],
        "chunk_type": row["chunk_type"],
        "heading_path": row["heading_path"],
        "page_number": row["page_number"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "authority_tier": row["authority_tier"],
        "assertion_policy": row["assertion_policy"],
        "source_url": row["source_url"],
        "cohort": row["cohort"],
        "academic_year": row["academic_year"],
        "student_level": row["student_level"],
        "major": row["major"],
        "campus": row["campus"],
        "uploaded_at": row["document_uploaded_at"],
        "published_at": row["document_published_at"],
        "effective_from": row["document_effective_from"],
        "date_status": row["document_date_status"],
        "uncertainty": json.loads(row["uncertainty_json"] or "[]"),
    }


class HybridRetriever:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.vectorizer = LocalSubwordVectorizer()

    def _all_chunks(self, connection: sqlite3.Connection) -> List[sqlite3.Row]:
        return connection.execute(
            """SELECT c.*,
                      d.uploaded_at AS document_uploaded_at,
                      d.published_at AS document_published_at,
                      d.effective_from AS document_effective_from,
                      d.date_status AS document_date_status
               FROM chunks c JOIN documents d USING(document_id)"""
        ).fetchall()

    def search(self, query: str, top_k: int = 6, profile: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        profile = profile or {}
        query_concepts = _query_concepts(query)
        procedure_query = any(term in query for term in ("怎么", "如何", "操作", "步骤"))
        rule_query = any(term in query for term in ("规定", "最低", "必须", "退课", "学分"))
        dimension_query = any(term in query.lower() for term in DIMENSION_QUERY_TERMS)
        connection = connect(self.db_path)
        try:
            fts = _fts_query(query)
            lexical_rows: List[sqlite3.Row] = []
            if fts:
                lexical_rows = connection.execute(
                    """SELECT c.*,
                              d.uploaded_at AS document_uploaded_at,
                              d.published_at AS document_published_at,
                              d.effective_from AS document_effective_from,
                              d.date_status AS document_date_status,
                              bm25(chunk_fts) AS fts_rank
                       FROM chunk_fts JOIN chunks c USING(chunk_id)
                       JOIN documents d USING(document_id)
                       WHERE chunk_fts MATCH ? ORDER BY fts_rank LIMIT 40""",
                    (fts,),
                ).fetchall()
            all_rows = self._all_chunks(connection)
        finally:
            connection.close()

        lexical_rank = {row["chunk_id"]: index + 1 for index, row in enumerate(lexical_rows)}
        query_features = self.vectorizer.features(query)
        semantic_scored = []
        for row in all_rows:
            score = self.vectorizer.cosine(query_features, self.vectorizer.features(row["text"] + " " + row["title"]))
            semantic_scored.append((score, row))
        semantic_scored.sort(key=lambda pair: pair[0], reverse=True)
        semantic_rank = {row["chunk_id"]: index + 1 for index, (_, row) in enumerate(semantic_scored[:40])}
        semantic_score = {row["chunk_id"]: score for score, row in semantic_scored[:40]}

        row_by_id = {row["chunk_id"]: row for row in all_rows}
        candidates = set(lexical_rank) | set(semantic_rank)
        ranked: List[Tuple[float, sqlite3.Row, Dict[str, float]]] = []
        for chunk_id in candidates:
            row = row_by_id[chunk_id]
            wanted_level = profile.get("student_level")
            if wanted_level and row["student_level"] not in {None, "", "all", wanted_level}:
                continue
            lexical_rrf = 1.0 / (60 + lexical_rank[chunk_id]) if chunk_id in lexical_rank else 0.0
            semantic_rrf = 1.0 / (60 + semantic_rank[chunk_id]) if chunk_id in semantic_rank else 0.0
            authority_confidence = AUTHORITY_CONFIDENCE.get(row["authority_tier"], 0.5)
            # Compare the query against the semantic anchor (FAQ question or
            # leading rule sentence), not only the full passage. Full-text
            # cosine otherwise systematically favors short generic answers.
            anchor = (row["text"] or "").splitlines()[0][:220]
            anchor_similarity = self.vectorizer.cosine(
                query_features,
                self.vectorizer.features(anchor + " " + (row["heading_path"] or "")),
            )
            full_blob = " ".join([row["title"] or "", row["heading_path"] or "", row["text"] or ""])
            anchor_blob = " ".join([row["title"] or "", row["heading_path"] or "", anchor])
            concept_coverage = _coverage(query_concepts, full_blob)
            anchor_coverage = _coverage(query_concepts, anchor_blob)
            attribute_match = 1.0 if dimension_query and DIMENSION_PATTERN.search(full_blob) else 0.0
            structured_attribute_match = (
                1.0 if attribute_match and row["chunk_type"] == "structured_fact" else 0.0
            )
            task_multiplier = 1.0
            if procedure_query:
                if any(term in (row["title"] or "") for term in ("服务手册", "指南", "标准")):
                    task_multiplier *= 1.12
                elif row["authority_tier"] == "official_policy":
                    task_multiplier *= 0.96
            if rule_query and row["authority_tier"] == "official_policy":
                # This is query-to-source-type fit, not a generic authority
                # boost: a request for a formal rule should prefer its policy
                # text over a handbook copy of the same rule.
                task_multiplier *= 1.4
            if "多少" in query and re.search(r"\d+(?:\.\d+)?\s*(?:学分|元|人|天|小时|分)", row["text"] or ""):
                task_multiplier *= 1.18
            applicability = 1.0
            if profile.get("cohort") and row["cohort"] and profile["cohort"] != row["cohort"]:
                applicability *= 0.75
            if profile.get("major") and row["major"] and profile["major"] != row["major"]:
                applicability *= 0.75
            relevance_score = (
                (lexical_rrf + semantic_rrf)
                + 0.018 * anchor_similarity
                + 0.045 * concept_coverage
                + 0.03 * anchor_coverage
                + 0.06 * attribute_match
                + 0.02 * structured_attribute_match
            ) * task_multiplier * applicability
            trust_tiebreaker = 0.0015 * authority_confidence
            score = relevance_score + trust_tiebreaker
            ranked.append((score, row, {
                "lexical_rrf": round(lexical_rrf, 6),
                "local_vector_rrf": round(semantic_rrf, 6),
                "local_similarity": round(semantic_score.get(chunk_id, 0.0), 4),
                "anchor_similarity": round(anchor_similarity, 4),
                "concept_coverage": round(concept_coverage, 4),
                "anchor_coverage": round(anchor_coverage, 4),
                "attribute_match": attribute_match,
                "structured_attribute_match": structured_attribute_match,
                "relevance_score": round(relevance_score, 6),
                "authority_confidence": authority_confidence,
                "trust_tiebreaker": round(trust_tiebreaker, 6),
                "authority_multiplier": 1.0,
                "assertion_multiplier": 1.0,
                "task_multiplier": round(task_multiplier, 4),
                "applicability_multiplier": applicability,
            }))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results: List[Dict[str, object]] = []
        for score, row, explanation in ranked[:top_k]:
            item = _row_to_result(row)
            item["score"] = round(score, 6)
            item["score_explanation"] = explanation
            results.append(item)

        intent = _intent(query)
        if results:
            decision_floor = float(results[0]["score"]) * 0.65
            decision_results = [item for item in results[:3] if float(item["score"]) >= decision_floor]
        else:
            decision_results = []
        has_official = any(item["authority_tier"] in {"official_policy", "official_guidance"} for item in decision_results)
        has_official_policy = any(item["authority_tier"] == "official_policy" for item in results[:3])
        has_peer = any(item["authority_tier"] == "peer_experience" for item in decision_results)
        freshness_unknown = not has_official_policy and any(
            item["authority_tier"] == "official_guidance"
            and item["assertion_policy"] != "navigation_only"
            and not item["published_at"]
            and not item["effective_from"]
            for item in results[:3]
        )
        uncertain = any(item["uncertainty"] for item in results[:3])
        if not results:
            status = "insufficient"
        elif intent == "policy_or_procedure" and not has_official:
            status = "insufficient_official_evidence"
        elif has_official and has_peer:
            status = "mixed_sources_review_required" if uncertain else "supported_with_context"
        elif has_official:
            status = "supported_freshness_unverified" if freshness_unknown else "supported"
        elif has_peer:
            status = "experience_only"
        else:
            status = "unverified"
        return {
            "query": query,
            "intent": intent,
            "retrieval_mode": "fts5_plus_local_subword_rrf",
            "answerability": status,
            "requires_uncertainty_label": uncertain,
            "results": results,
        }
