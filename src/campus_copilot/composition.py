import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple
from urllib import request

from .answer_planning import fallback_message


PROVIDER_PRESETS = {
    "makers": {
        "base_url": "https://ai-gateway.edgeone.link/v1",
        "model": "@makers/deepseek-v4-flash",
        "requires_api_key": True,
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    requires_api_key: bool = False
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        provider = os.getenv("CAMPUS_LLM_PROVIDER", "custom").strip().lower() or "custom"
        preset = PROVIDER_PRESETS.get(provider, {})
        return cls(
            provider=provider,
            base_url=os.getenv(
                "CAMPUS_LLM_BASE_URL",
                str(preset.get("base_url", "https://api.openai.com/v1")),
            ).rstrip("/"),
            model=os.getenv("CAMPUS_LLM_MODEL", str(preset.get("model", ""))).strip(),
            api_key=os.getenv("CAMPUS_LLM_API_KEY", "").strip(),
            requires_api_key=bool(preset.get("requires_api_key", False)),
            timeout_seconds=float(os.getenv("CAMPUS_LLM_TIMEOUT", "30")),
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.base_url
            and self.model
            and (self.api_key or not self.requires_api_key)
        )


class AnswerComposer(Protocol):
    name: str

    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        ...


def _excerpt(text: str, limit: int = 300) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if "回答：" in cleaned:
        cleaned = cleaned.split("回答：", 1)[1].strip()
    if len(cleaned) <= limit:
        return cleaned
    shortened = cleaned[:limit]
    boundary = max(shortened.rfind("。"), shortened.rfind("；"), shortened.rfind("！"))
    return (shortened[:boundary + 1] if boundary > limit // 2 else shortened.rstrip()) + "…"


def display_answer(answer: str) -> str:
    """Remove audit citation tokens from the student-facing prose."""
    return re.sub(r"\s*\[S\d+\]", "", answer).strip()


def _focused_excerpt(text: str, query: str, question_type: str) -> str:
    if "保研" in query or "推免" in query:
        cohort = re.search(
            r"(\d{2})级(?:毕业生)?(?:共)?\s*(\d+)\s*人(?:中)?保研\s*(\d+)\s*人",
            text,
        )
        if not cohort:
            cohort = re.search(
                r"(\d{2})级(?:毕业生)?(?:共)?\s*(\d+)\s*人中\s*(\d+)\s*人保研",
                text,
            )
        if cohort:
            year, total_text, admitted_text = cohort.groups()
            total, admitted = int(total_text), int(admitted_text)
            ratio = admitted / total * 100 if total else 0
            return f"{year}级毕业生{total}人中有{admitted}人保研，约占{ratio:.1f}%"
    if question_type == "historical_outcome":
        sentences = re.split(r"[。\n]+", text)
        focused = [
            sentence.strip() for sentence in sentences
            if re.search(r"(?:\d+\s*人|\d+(?:\.\d+)?%|比例|保研|推免)", sentence)
        ]
        if focused:
            return _excerpt("。".join(focused[:2]) + "。", 220)
    return _excerpt(text)


class ExtractiveComposer:
    """Auditable no-key fallback; it summarizes only retrieved passages."""

    name = "extractive_fallback"

    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        if not context_packet.get("can_generate"):
            route = str((context_packet.get("answer_plan") or {}).get("fallback_route", ""))
            if context_packet.get("response_mode") == "insufficient_contextual_evidence":
                answer = "我没有找到既符合当前追问对象、又能直接回答上一问题的材料，因此不能把其他相似内容当成答案。请补充对应人群的资料或向学校确认。"
                unresolved = ["缺少同时匹配原问题主题和当前追问对象的证据"]
            else:
                answer = fallback_message(route)
                unresolved = ["缺少能够支持结论的有效来源"]
            return {
                "answer": answer,
                "citations": [],
                "claims": [],
                "unresolved": unresolved,
            }

        eligible = [
            item for item in context_packet.get("evidence", [])
            if item.get("assertion_policy") not in {"navigation_only", "do_not_assert", "do_not_assert_until_ocr"}
        ]
        evidence_limit = 3 if eligible and eligible[0].get("chunk_type") == "structured_fact" else 1
        evidence = eligible[:evidence_limit]
        if not evidence:
            return {
                "answer": "检索到了相关资源，但当前内容不能直接作为回答依据。请查看学校最新通知或联系对应部门。",
                "citations": [],
                "claims": [],
                "unresolved": ["检索结果当前不可直接断言"],
            }

        paragraphs: List[str] = []
        citations: List[str] = []
        claims: List[Dict[str, object]] = []
        unresolved: List[str] = []
        question_type = str((context_packet.get("answer_plan") or {}).get("question_type", "campus_fact"))
        query = str(context_packet.get("query", ""))
        for index, item in enumerate(evidence):
            evidence_id = str(item["evidence_id"])
            authority = str(item.get("authority_tier", "unverified"))
            if authority == "peer_experience":
                prefix = "根据学生整理的往届信息，"
                certainty = "experience"
            elif authority in {"official_policy", "official_guidance"}:
                prefix = "根据现有学校材料，"
                certainty = "official"
            else:
                prefix = "现有待核实资料显示，"
                certainty = "unverified"
            text = _focused_excerpt(
                str(item.get("text", "")), query, question_type
            )
            lead = ""
            if index == 0 and any(term in query for term in ("有吗", "有没有", "机会吗")):
                lead = "有的。"
            paragraph = f"{lead}{prefix}{text} [{evidence_id}]"
            if authority == "peer_experience" and question_type == "historical_outcome":
                paragraph += " 这是往届实际结果，不代表每一届都有固定比例或固定名额。"
                if "排到" in query or "排名" in query:
                    paragraph += " 现有材料没有给出最低排名，不能据此判断排到第几名就一定可以。"
                    unresolved.append("缺少当届具体名额和最低入选排名")
            paragraphs.append(paragraph)
            citations.append(evidence_id)
            claims.append({"text": text, "evidence_ids": [evidence_id], "certainty": certainty})
            if item.get("date_status") != "verified" and not item.get("effective_from"):
                if authority == "peer_experience":
                    unresolved.append(f"{item.get('title', '该学生资料')}的对应届次尚未完整标明")
                else:
                    unresolved.append(f"{item.get('title', '该资料')}的生效时间尚未核实")

        if context_packet.get("response_mode") == "experience_only":
            unresolved.append("当前结论主要来自往届学生经验，具体安排可能随届次变化")
        return {
            "answer": "\n\n".join(paragraphs),
            "citations": citations,
            "claims": claims,
            "unresolved": list(dict.fromkeys(unresolved)),
        }


def _extract_json(content: str) -> Dict[str, object]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model_did_not_return_json")
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model_response_must_be_object")
    return value


def _normalize_model_response(value: Dict[str, object]) -> Dict[str, object]:
    """Normalize harmless provider shape drift without weakening grounding checks."""
    normalized = dict(value)
    unresolved = normalized.get("unresolved")
    if unresolved is None:
        normalized["unresolved"] = []
    elif isinstance(unresolved, str):
        normalized["unresolved"] = [unresolved] if unresolved.strip() else []
    citations = normalized.get("citations")
    if isinstance(citations, list):
        normalized["citations"] = [
            match.group(1) if (match := re.fullmatch(r"\[?(S\d+)\]?", str(item).strip())) else item
            for item in citations
        ]
    claims = normalized.get("claims")
    if isinstance(claims, list):
        normalized_claims: List[object] = []
        for claim in claims:
            if not isinstance(claim, dict):
                normalized_claims.append(claim)
                continue
            normalized_claim = dict(claim)
            evidence_ids = normalized_claim.get("evidence_ids")
            if isinstance(evidence_ids, list):
                normalized_claim["evidence_ids"] = [
                    match.group(1) if (match := re.fullmatch(r"\[?(S\d+)\]?", str(item).strip())) else item
                    for item in evidence_ids
                ]
            normalized_claims.append(normalized_claim)
        normalized["claims"] = normalized_claims
    return normalized


class OpenAICompatibleComposer:
    """Small dependency-free adapter for OpenAI-compatible chat endpoints."""

    name = "openai_compatible"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        system = (
            "你是一位可靠、耐心、熟悉校园生活的学生大使，不是政策文件朗读器。"
            "使用自然、克制的中文口语，像学长学姐帮助新生；不要自称AI，不要过度使用‘嗯嗯’等语气词。"
            "第一句直接回答学生真正的问题，随后只补充最有帮助的背景，全文通常不超过三段。"
            "不要以文件名、文号、‘现有学校材料提到’或大段政策原文开头，也不要逐条复述法律条款。"
            "你只能依据提供的 evidence 回答事实，不能使用模型记忆补充事实。"
            "不要在自然语言正文中插入 [S1]；通过 citations 和 claims.evidence_ids 返回审计引用，前端会在正文下方展示来源。"
            "学生经验要自然地说明为往届学生经验，不能说成学校规定。"
            "往届人数或比例不能表达成每届固定名额；政策存在也不能推导出某个班的具体名额。"
            "如果 evidence 没有最低排名，绝对不能把往届比例反推成‘班级前多少名/百分之多少就能保研’。"
            "保留必要的时间边界，但不要反复使用免责声明。can_generate 为 false 时只能说明缺少什么和下一步去哪类来源查。"
            "严格遵循 answer_plan 中的 question_type、guidance 和 response_shape。"
            "仅输出 JSON 对象，字段必须为 answer、citations、claims、unresolved。"
            "claims 中每项必须包含 text、evidence_ids、certainty；certainty 只能是 official、experience、unverified。"
        )
        user = json.dumps(context_packet, ensure_ascii=False, separators=(",", ":"))
        payload = json.dumps({
            "model": self.config.model,
            "temperature": 0.25,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        outbound = request.Request(
            f"{self.config.base_url}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        with request.urlopen(outbound, timeout=self.config.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        return _normalize_model_response(_extract_json(str(content)))


def validate_composition(
    composition: Dict[str, object], context_packet: Dict[str, object]
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    answer = composition.get("answer")
    citations = composition.get("citations")
    claims = composition.get("claims")
    unresolved = composition.get("unresolved")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer_required")
    elif context_packet.get("can_generate") and re.match(
        r"^(?:现有学校材料|学生经验（|《|根据《|[^。]{0,40}(?:文件|通知)》?提到)",
        display_answer(answer),
    ):
        errors.append("answer_starts_with_document_frame")
    if not isinstance(citations, list) or not all(isinstance(item, str) for item in citations):
        errors.append("citations_must_be_string_list")
        citations = []
    if not isinstance(claims, list):
        errors.append("claims_must_be_list")
        claims = []
    if not isinstance(unresolved, list):
        errors.append("unresolved_must_be_list")

    evidence = {str(item["evidence_id"]): item for item in context_packet.get("evidence", [])}
    answer_plan = context_packet.get("answer_plan") or {}
    if answer_plan.get("question_type") == "historical_outcome":
        evidence_has_rank = any(
            any(term in str(item.get("text", "")) for term in ("最低排名", "排名前", "班级前", "排到第"))
            for item in evidence.values()
        )
        visible = display_answer(str(answer or ""))
        inferred_rank = bool(re.search(
            r"(?:(?:名额|排名|班级前)[^。]{0,24}\d+(?:\.\d+)?%|排到第\s*\d+)",
            visible,
        ))
        if inferred_rank and not evidence_has_rank:
            errors.append("historical_outcome_inferred_rank_without_evidence")
    for citation in citations:
        if citation not in evidence:
            errors.append(f"unknown_citation:{citation}")

    if context_packet.get("can_generate") and evidence and not citations:
        errors.append("grounded_answer_requires_citation")
    if not context_packet.get("can_generate") and (citations or claims):
        errors.append("refusal_must_not_make_evidence_claims")

    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claim_not_object:{index}")
            continue
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(f"claim_missing_evidence:{index}")
            continue
        cited = [evidence.get(str(item)) for item in evidence_ids]
        if any(item is None for item in cited):
            errors.append(f"claim_unknown_evidence:{index}")
            continue
        certainty = claim.get("certainty")
        if any(item.get("authority_tier") == "peer_experience" for item in cited) and certainty != "experience":
            errors.append(f"peer_claim_not_labeled_experience:{index}")
        if certainty == "official" and not any(
            item.get("authority_tier") in {"official_policy", "official_guidance"}
            and item.get("assertion_policy") not in {"navigation_only", "do_not_assert", "do_not_assert_until_ocr"}
            for item in cited
        ):
            errors.append(f"official_claim_without_official_evidence:{index}")
    return not errors, errors


def configured_composer() -> Tuple[AnswerComposer, Dict[str, object]]:
    config = ProviderConfig.from_env()
    if config.enabled:
        return OpenAICompatibleComposer(config), {
            "mode": "model",
            "adapter": "openai_compatible",
            "provider": config.provider,
            "model": config.model,
            "base_url": config.base_url,
            "credential_configured": bool(config.api_key),
        }
    return ExtractiveComposer(), {
        "mode": "fallback",
        "adapter": "extractive_fallback",
        "provider": config.provider,
        "model": config.model or None,
        "base_url": config.base_url if config.model else None,
        "credential_configured": bool(config.api_key),
        "reason": "missing_api_key" if config.requires_api_key and not config.api_key else "model_not_configured",
    }
