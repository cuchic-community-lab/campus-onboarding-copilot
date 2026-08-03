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
from .config import (
    ANSWERABILITY_COVERAGE_FLOOR,
    ANSWERABILITY_MIN_EVIDENCE,
    ANSWERABILITY_SCORE_FLOOR,
)


SYNONYMS = {
    "宿舍": ["寝室", "生活区", "床位"],
    "寝室": ["宿舍", "生活区", "床位"],
    "选课": ["课程", "选修课", "选课系统"],
    "报到": ["入学", "新生", "信息采集"],
    "交通": ["摆渡车", "公交", "出行"],
    "成绩": ["绩点", "gpa", "学分"],
    "gpa": ["绩点", "成绩", "学分绩"],
    "绩点": ["gpa", "成绩", "学分绩"],
    "吃饭": ["食堂", "餐饮", "饭卡"],
    "床": ["床铺", "床垫"],
    "多大": ["尺寸", "规格", "长", "宽"],
    "尺寸": ["规格", "长", "宽"],
    "保研": ["推免", "推荐免试", "免试攻读"],
    "推免": ["保研", "推荐免试", "免试攻读"],
    "推荐免试": ["保研", "推免", "免试攻读"],
}

DOMAIN_ENTITY_GROUPS = {
    "recommendation_admission": ("保研", "推免", "推荐免试", "免试攻读"),
}

POLICY_TERMS = ("必须", "规定", "办法", "申请", "截止", "毕业", "选课", "成绩", "请假", "办理")
LIFESTYLE_TERMS = ("宿舍", "食堂", "快递", "生活费", "好吃", "外卖", "交通", "社团", "军训", "摆渡车", "游泳")

CONCEPT_TERMS = (
    "信息采集", "美团外卖", "补退选", "每学期", "几人间", "选课系统",
    "宿舍", "寝室", "选课", "退课", "规定", "操作", "步骤", "快递", "地址",
    "外卖", "食堂", "学分", "最低", "游泳", "必修", "考试", "评分", "报到",
    "军训", "请假", "校历", "摆渡车", "交通", "绩点", "gpa", "社团", "四级",
    "床垫", "床铺", "尺寸", "规格", "多大", "长", "宽", "床",
    "推荐免试", "免试攻读", "推免", "保研", "保留学籍",
    # More specific entities so a query like "游泳池水深" prefers 游泳池
    # over the generic 游泳 when deciding answerability.
    "游泳池", "泳池", "游泳馆", "水深", "跑道", "操场",
)

DIMENSION_QUERY_TERMS = ("多大", "尺寸", "规格", "长宽", "多长", "多宽", "厘米", "cm")
DIMENSION_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:m|米|cm|厘米|mm|毫米)|\d+\s*[x×*]\s*\d+)",
    re.IGNORECASE,
)

# Query tokens that carry no retrieval meaning; the semantic-misalignment gate
# ignores them so "游泳池水深多少米" keeps 水深/游泳池 as its evidence terms.
_STOP_TERMS = frozenset({
    "多少", "怎么", "如何", "什么", "为什么", "是否", "能不能", "可以吗", "怎么去",
    "怎么办", "哪里", "哪儿", "哪个", "哪些", "几号", "几点", "好久", "多久",
    "有没有", "是不是", "一个", "这个", "那个", "什么时间", "什么条件", "注意事项",
    "要求", "安排", "需要", "应该", "请问", "我想", "知道", "了解", "问一下",
    "吗", "呢", "的", "了", "啊", "吧",
})

# Attribute/question words are not entities: they describe the ask, so they
# must not count toward core-entity coverage (e.g. "床垫尺寸多大" keeps 床垫).
_ATTRIBUTE_TERMS = frozenset({
    "多大", "尺寸", "规格", "多少", "最低", "最高", "如何", "怎么办",
    "什么条件", "注意事项", "安排", "要求",
})


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


def _requested_domain_entities(query: str) -> List[str]:
    lowered = query.lower()
    return [
        group for group, aliases in DOMAIN_ENTITY_GROUPS.items()
        if any(alias in lowered for alias in aliases)
    ]


# Verbs/prepositions that can precede the concrete noun in "可以点X吗/有没有X".
_SPECIFIC_NOUN_STRIP = (
    "可以", "能不能", "能否", "能", "点", "叫", "用", "吃", "喝", "买", "玩",
    "上", "去", "坐", "看", "听", "写", "拿", "带", "办", "申请",
)


def _specific_noun(query: str) -> Optional[str]:
    """Extract the concrete thing a yes/no or recommendation question asks about.

    Examples: "学校有无人机社团吗" -> 无人机社团; "可以点美团外卖吗" -> 美团外卖;
    "食堂的招牌菜推荐一下" -> 招牌菜. Returns None for non-existence/recommendation
    questions (how/what/where…), which don't need this extra gate.
    """
    q = query.strip()
    m = re.search(r"有(?:没有)?([\u4e00-\u9fff]{2,12})吗[？?]?$", q)
    if not m:
        m = re.search(r"有没有([\u4e00-\u9fff]{2,12})", q)
    if not m:
        m = re.search(r"(?:可以|能不能|能否|能)([\u4e00-\u9fff]{2,12})吗[？?]?$", q)
    if not m:
        m = re.search(r"([\u4e00-\u9fff]{2,10})(?:推荐|好吃|好喝)", q)
    if not m:
        return None
    noun = m.group(1)
    for prefix in _SPECIFIC_NOUN_STRIP:
        if noun.startswith(prefix):
            noun = noun[len(prefix):]
            break
    return noun or None


_GENERIC_NOUN_SUFFIXES = ("机会", "情况", "怎么样", "什么", "时候", "地方", "东西", "吗", "呢", "啊", "吧", "的", "了")


def _specific_noun_supported(specific_noun: str, top_blobs: List[str]) -> bool:
    """True if the concrete noun (or its content core) appears in the evidence.

    "保研机会" is satisfied by evidence containing 保研; "无人机社团" is NOT
    satisfied by generic 社团 evidence because the specific part 无人机 is what
    the user actually asked about.
    """
    noun = specific_noun
    for suffix in _GENERIC_NOUN_SUFFIXES:
        if noun.endswith(suffix) and len(noun) > len(suffix):
            noun = noun[: -len(suffix)]
            break
    segments = [s for s in re.split(r"[的地得]", noun) if s]
    noun = max(segments, key=len) if segments else noun
    return bool(noun) and any(noun in blob for blob in top_blobs)


def _domain_entity_match(groups: List[str], text: str) -> float:
    if not groups:
        return 0.0
    matched = 0
    for group in groups:
        aliases = DOMAIN_ENTITY_GROUPS[group]
        matched += int(any(alias in text for alias in aliases))
    return matched / len(groups)


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
    local_path = str(row["document_local_path"] or "") if "document_local_path" in row.keys() else ""
    media_type = str(row["document_media_type"] or "") if "document_media_type" in row.keys() else ""
    source_kind = str(row["document_source_kind"] or "") if "document_source_kind" in row.keys() else ""
    file_path = None
    image_thumb = None
    if local_path:
        normalized = local_path.replace("\\", "/")
        if normalized.startswith("/"):
            file_path = normalized
        else:
            file_path = "/" + normalized
        if media_type == "image" or source_kind == "qr_resource" or media_type == "qr":
            image_thumb = file_path
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
        "file_path": file_path,
        "media_type": media_type,
        "image_thumb": image_thumb,
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
                      d.date_status AS document_date_status,
                      d.local_path AS document_local_path,
                      d.media_type AS document_media_type,
                      d.source_kind AS document_source_kind
               FROM chunks c JOIN documents d USING(document_id)"""
        ).fetchall()

    def search(self, query: str, top_k: int = 6, profile: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        profile = profile or {}
        query_concepts = _query_concepts(query)
        requested_entities = _requested_domain_entities(query)
        availability_query = any(term in query for term in ("有吗", "有没有", "机会", "是否"))
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
                              d.local_path AS document_local_path,
                              d.media_type AS document_media_type,
                              d.source_kind AS document_source_kind,
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
            tags = " ".join(json.loads(row["tags_json"] or "[]"))
            full_blob = " ".join([
                row["title"] or "", row["heading_path"] or "", tags, row["text"] or "",
            ]).lower()
            anchor_blob = " ".join([row["title"] or "", row["heading_path"] or "", anchor])
            concept_coverage = _coverage(query_concepts, full_blob)
            anchor_coverage = _coverage(query_concepts, anchor_blob)
            domain_entity_match = _domain_entity_match(requested_entities, full_blob)
            domain_entity_multiplier = 1.0
            if requested_entities and not domain_entity_match:
                domain_entity_multiplier = 0.35
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
            if (
                availability_query
                and domain_entity_match
                and row["chunk_type"] == "faq"
                and re.search(r"(?:比例|人数|\d+(?:\.\d+)?%)", row["text"] or "")
            ):
                # Availability questions benefit from a direct observed outcome
                # alongside policy documents that only establish a mechanism.
                task_multiplier *= 1.45
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
                + 0.08 * domain_entity_match
            ) * task_multiplier * applicability * domain_entity_multiplier
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
                "domain_entity_match": domain_entity_match,
                "relevance_score": round(relevance_score, 6),
                "authority_confidence": authority_confidence,
                "trust_tiebreaker": round(trust_tiebreaker, 6),
                "authority_multiplier": 1.0,
                "assertion_multiplier": 1.0,
                "task_multiplier": round(task_multiplier, 4),
                "applicability_multiplier": applicability,
                "domain_entity_multiplier": domain_entity_multiplier,
            }))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results: List[Dict[str, object]] = []
        visual_evidence: List[Dict[str, object]] = []
        document_counts: Counter = Counter()
        added_chunk_ids: set = set()

        def _is_visual_row(row: sqlite3.Row) -> bool:
            media = (
                row["document_media_type"]
                if "document_media_type" in row.keys()
                else row["media_type"] if "media_type" in row.keys() else ""
            )
            return (
                row["chunk_type"] in {"visual_reference", "qr_entry"}
                or media in {"image", "qr"}
            )

        def _append(score: float, row: sqlite3.Row, explanation: Dict[str, float], per_doc_limit: int) -> None:
            if row["chunk_id"] in added_chunk_ids:
                return
            if document_counts[row["document_id"]] >= per_doc_limit:
                return
            item = _row_to_result(row)
            item["score"] = round(score, 6)
            item["score_explanation"] = explanation
            added_chunk_ids.add(row["chunk_id"])
            if _is_visual_row(row):
                visual_evidence.append(item)
            else:
                results.append(item)
            document_counts[row["document_id"]] += 1

        # Pass 1 — document diversity: at most one text chunk per document so
        # citations can span multiple different sources (入学指南.pdf + 新生常见
        # 问题问答库 + 官方政策文件 …). structured_fact rows keep two because
        # spreadsheet records are atomic and may need adjacent rows. Reserve two
        # slots so Pass 2 can backfill a second chunk from the top document(s);
        # otherwise single-source questions would only ever see one evidence.
        diversity_cap = max(1, top_k - 2)
        for score, row, explanation in ranked:
            if len(results) >= diversity_cap:
                break
            limit = 2 if row["chunk_type"] == "structured_fact" else 1
            _append(score, row, explanation, limit)

        # Pass 2 — backfill: fill the remaining slots with a second chunk from
        # the same document (capped at 2) so single-source questions still
        # collect enough evidence to pass MIN_EVIDENCE.
        if len(results) < top_k:
            for score, row, explanation in ranked:
                if len(results) >= top_k:
                    break
                if row["chunk_type"] == "structured_fact" or _is_visual_row(row):
                    continue
                if document_counts[row["document_id"]] != 1:
                    continue
                _append(score, row, explanation, 2)

        # Visuals are a separate supplementary channel: collect them regardless
        # of how full the text slots are so image/QR cards still appear.
        for score, row, explanation in ranked:
            if _is_visual_row(row) and document_counts[row["document_id"]] == 0:
                _append(score, row, explanation, 5)

        intent = _intent(query)
        if results:
            # v1.2: evidence is counted by concept coverage (quality), not only
            # relative score — a strong corroborating chunk from a second source
            # must actually cover the query's concepts to count. Counting across
            # all top_k results (not only [:3]) so backfilled second chunks from
            # the top document are also credited. Weak hits with low coverage
            # don't inflate the count.
            decision_floor = float(results[0]["score"]) * 0.65
            decision_results = [
                item for item in results[:top_k]
                if float(item.get("score_explanation", {}).get("concept_coverage", 0.0) or 0.0) >= ANSWERABILITY_COVERAGE_FLOOR
            ]
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

        # ---- Strict answerability gates (unknown判定强化) ----
        # 检索不足时宁可拒答也不编造。判定规则：
        #  1. 无任何检索结果            -> insufficient_no_evidence
        #  2. top1 绝对分数低于阈值      -> insufficient_low_score
        #  3. top1 概念覆盖低于阈值      -> insufficient_low_coverage
        #  4. 有效证据数不足            -> insufficient_evidence_count
        #  5. 数值型问题(多少/几/米/厘米)但证据不含任何数值 -> insufficient_numeric_evidence
        insufficient_reason = None
        numeric_query = bool(
            re.search(r"(?:多少|几|多大|多长|多宽|多高|几米|几人间|厘米|cm|mm|元|学分|小时|分钟|几天|几个|几名)", query)
        ) and bool(
            re.search(r"(?:多少|几|多大|多长|多宽|多高|厘米|cm|mm|元|学分|小时|分钟|天|个|名)", query)
        )
        if not results:
            status = "insufficient_no_evidence"
            insufficient_reason = "no_retrieved_evidence"
        else:
            top_score = float(results[0]["score"])
            top_coverage = float(
                results[0].get("score_explanation", {}).get("concept_coverage", 0.0) or 0.0
            )
            # A numeric question must find a number *with a measure unit* in the
            # top evidence; plain numbered list markers (e.g. "6）") don't count.
            # v1.2: check across the top-3 chunks, not only the top chunk, so
            # "最低需要选多少学分" can be backed by 选课服务手册's "最低6分".
            numeric_texts = " ".join(str(r.get("text", "")) for r in results[:3])
            has_numeric_evidence = bool(
                re.search(
                    r"(?:[0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十百千万两])"
                    r"\s*(?:米|m\b|厘米|cm|mm|毫米|元|人|人间|天|小时|分钟|分|秒|学分|个|名|次|%|％|楼|层|间|床|度|℃)",
                    numeric_texts,
                )
            )
            # Semantic-misalignment gate: the query's *core entity* must appear
            # in at least one of the top-3 evidence blobs. Prefer CONCEPT_TERMS
            # whitelist hits; otherwise fall back to the longest 3-gram of the
            # query. This catches "游泳池水深多少米" being answered by generic
            # swimming-course text that never mentions 游泳池/水深.
            query_blob = expand_query(query).lower()
            # Prefer the LONGEST whitelist term so "游泳池水深" is matched by
            # 游泳池 rather than the generic 游泳 (which would wrongly pass).
            # Single-char whitelist terms (床/长/宽) are too noisy: they match
            # as substrings of unrelated words and dilute the weighted score.
            # Attribute/question words (多大/尺寸/规格/多少…) describe the ask
            # but are not entities; excluding them keeps the gate meaningful.
            core_terms = sorted(
                (t for t in CONCEPT_TERMS
                 if len(t) >= 2 and t not in _ATTRIBUTE_TERMS and t in query_blob),
                key=len,
                reverse=True,
            )
            if not core_terms:
                candidates = sorted(
                    (t for t in chinese_search_terms(query_blob).split()
                     if len(t) >= 3 and t not in _STOP_TERMS),
                    key=len,
                    reverse=True,
                )
                core_terms = candidates[:2] if candidates else []
            # Keep all whitelist hits (not just top-2) so multi-word queries
            # like "宿舍是几人间" (几人间+宿舍+寝室) get a fair weighted score.
            top_blobs = [
                " ".join([
                    str(r.get("title", "")), str(r.get("heading_path", "")),
                    " ".join(r.get("tags", [])), str(r.get("text", "")),
                ]).lower()
                for r in results[:3]
            ]
            # Weighted core-term coverage: longer (more specific) terms matter
            # more. "游泳池水深多少米" gets 游泳池+泳池+游泳; only generic 游泳
            # hits the swimming-course evidence -> low weighted coverage -> the
            # answer would be off-topic, so refuse. "宿舍是几人间" hits 宿舍+寝室
            # -> sufficient -> answer.
            core_weight_total = sum(len(t) for t in core_terms)
            core_hit_total = sum(
                len(t) for t in core_terms
                if any(t in blob for blob in top_blobs)
            ) if core_terms else 0
            core_coverage = (core_hit_total / core_weight_total) if core_weight_total else 1.0
            core_hit = core_coverage >= 0.5
            # Specific-noun gate (v1.2): for yes/no or recommendation questions
            # ("学校有无人机社团吗", "食堂的招牌菜推荐一下"), the concrete thing
            # the user asks about must itself appear in the evidence. A generic
            # whitelist match (社团/食堂) alone would otherwise answer a question
            # the corpus never covers.
            specific_noun = _specific_noun(query)
            specific_noun_missing = bool(specific_noun) and not _specific_noun_supported(
                specific_noun, top_blobs
            )
            if top_score < ANSWERABILITY_SCORE_FLOOR:
                status = "insufficient_low_score"
                insufficient_reason = f"top1_score_below_threshold({top_score:.4f}<{ANSWERABILITY_SCORE_FLOOR})"
            elif top_coverage < ANSWERABILITY_COVERAGE_FLOOR:
                status = "insufficient_low_coverage"
                insufficient_reason = f"top1_concept_coverage_below_threshold({top_coverage:.4f}<{ANSWERABILITY_COVERAGE_FLOOR})"
            elif numeric_query and not has_numeric_evidence:
                status = "insufficient_numeric_evidence"
                insufficient_reason = "numeric_query_without_numeric_evidence"
            elif not core_hit:
                status = "insufficient_core_entity_missing"
                insufficient_reason = "query_core_entity_absent_from_evidence"
            elif specific_noun_missing:
                status = "insufficient_specific_entity"
                insufficient_reason = f"query_specific_entity_absent_from_evidence:{specific_noun}"
            elif len(decision_results) < ANSWERABILITY_MIN_EVIDENCE:
                status = "insufficient_evidence_count"
                insufficient_reason = f"evidence_count_below_threshold({len(decision_results)}<{ANSWERABILITY_MIN_EVIDENCE})"
            elif intent == "policy_or_procedure" and not has_official:
                status = "insufficient_official_evidence"
                insufficient_reason = "policy_query_without_official_evidence"
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
            "insufficient_reason": insufficient_reason,
            "requires_uncertainty_label": uncertain,
            "results": results,
            "visual_evidence": visual_evidence,
        }
