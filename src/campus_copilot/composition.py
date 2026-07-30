import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple
from urllib import request


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


class ExtractiveComposer:
    """Auditable no-key fallback; it summarizes only retrieved passages."""

    name = "extractive_fallback"

    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        if not context_packet.get("can_generate"):
            if context_packet.get("response_mode") == "insufficient_contextual_evidence":
                answer = "我没有找到既符合当前追问对象、又能直接回答上一问题的材料，因此不能把其他相似内容当成答案。请补充对应人群的资料或向学校确认。"
                unresolved = ["缺少同时匹配原问题主题和当前追问对象的证据"]
            else:
                answer = "目前知识库中没有足够的可核实材料来回答这个问题。请以学校最新通知或向对应部门确认为准。"
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
        for item in evidence:
            evidence_id = str(item["evidence_id"])
            authority = str(item.get("authority_tier", "unverified"))
            if authority == "peer_experience":
                prefix = "学生经验（不是学校官方规定）"
                certainty = "experience"
            elif authority in {"official_policy", "official_guidance"}:
                prefix = "现有学校材料"
                certainty = "official"
            else:
                prefix = "待核实资料"
                certainty = "unverified"
            text = _excerpt(str(item.get("text", "")))
            paragraph = f"{prefix}《{item.get('title', '未命名来源')}》提到：{text} [{evidence_id}]"
            paragraphs.append(paragraph)
            citations.append(evidence_id)
            claims.append({"text": text, "evidence_ids": [evidence_id], "certainty": certainty})
            if item.get("date_status") != "verified" and not item.get("effective_from"):
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
    return normalized


class OpenAICompatibleComposer:
    """Small dependency-free adapter for OpenAI-compatible chat endpoints."""

    name = "openai_compatible"

    def __init__(self, config: ProviderConfig):
        self.config = config

    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        system = (
            "你是新生入学助手。你只能依据提供的 evidence 回答，不能使用模型记忆补充事实。"
            "每个事实段落必须包含形如 [S1] 的引用。学生经验必须明确写成学生经验，不能说成学校规定。"
            "保留原文中的不确定性和时间边界。can_generate 为 false 时只能拒答并说明缺少什么。"
            "仅输出 JSON 对象，字段必须为 answer、citations、claims、unresolved。"
            "claims 中每项必须包含 text、evidence_ids、certainty；certainty 只能是 official、experience、unverified。"
        )
        user = json.dumps(context_packet, ensure_ascii=False, separators=(",", ":"))
        payload = json.dumps({
            "model": self.config.model,
            "temperature": 0.1,
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
    if not isinstance(citations, list) or not all(isinstance(item, str) for item in citations):
        errors.append("citations_must_be_string_list")
        citations = []
    if not isinstance(claims, list):
        errors.append("claims_must_be_list")
        claims = []
    if not isinstance(unresolved, list):
        errors.append("unresolved_must_be_list")

    evidence = {str(item["evidence_id"]): item for item in context_packet.get("evidence", [])}
    for citation in citations:
        if citation not in evidence:
            errors.append(f"unknown_citation:{citation}")
        elif f"[{citation}]" not in str(answer):
            errors.append(f"citation_missing_from_answer:{citation}")

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
