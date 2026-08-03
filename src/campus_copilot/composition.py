import json
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple
from urllib import request
from urllib.error import HTTPError, URLError
PROVIDER_PRESETS = {
    "makers": {
        "base_url": "https://ai-gateway.edgeone.link/v1",
        "model": "@makers/deepseek-v4-flash",
        "requires_api_key": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
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
def _elapsed_ms(started: float) -> float:
    """Wall-clock milliseconds since ``started`` (time.monotonic)."""
    return round((time.monotonic() - started) * 1000, 1)
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
    """Auditable no-key fallback; it summarizes only retrieved passages.
    Output is structured markdown (结论 → 分点说明 → [Sx]) and pulls from
    multiple different sources when available, so even without an API key the
    answer is detailed, formatted, and shows which documents back each point.
    """
    name = "extractive_fallback"
    @staticmethod
    def _source_label(item: Dict[str, object]) -> Tuple[str, str]:
        authority = str(item.get("authority_tier", "unverified"))
        if authority == "peer_experience":
            return "学生经验（不是学校官方规定）", "experience"
        if authority in {"official_policy", "official_guidance"}:
            return "现有学校材料", "official"
        return "待核实资料", "unverified"
    def generate(self, context_packet: Dict[str, object]) -> Dict[str, object]:
        if not context_packet.get("can_generate"):
            topic = str(context_packet.get("query") or "这个问题").strip()
            if context_packet.get("response_mode") == "insufficient_contextual_evidence":
                # v1.3 软拒答：追问上下文不足时同样引导「问师哥师姐」，不硬性拒答。
                answer = (
                    "知识库中暂时没有找到既符合当前追问对象、又能直接回答上一问题的可靠材料。"
                    "你可以① 点击下方「问师哥师姐」，让学长学姐给你权威解答；② 换个问法试试。"
                    "以学校最新通知为准。"
                )
                unresolved = ["缺少同时匹配原问题主题和当前追问对象的证据"]
            else:
                answer = (
                    f"知识库里暂时没有找到关于「{topic}」的可靠材料。"
                    "你可以① 点击下方「问师哥师姐」，让学长学姐给你权威解答；② 换个问法试试。"
                    "以学校最新通知为准。"
                )
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
        # Multi-source: use up to 6 evidence items (top_k default) so citations
        # and citation_metadata stay aligned.
        evidence = eligible[:6]
        if not evidence:
            return {
                "answer": "检索到了相关资源，但当前内容不能直接作为回答依据。请查看学校最新通知或联系对应部门。",
                "citations": [],
                "claims": [],
                "unresolved": ["检索结果当前不可直接断言"],
            }
        lines: List[str] = []
        citations: List[str] = []
        claims: List[Dict[str, object]] = []
        unresolved: List[str] = []
        # 结论（加粗）：最相关证据的核心内容
        top = evidence[0]
        top_label, top_certainty = self._source_label(top)
        conclusion = _excerpt(str(top.get("text", "")), 220)
        lines.append(f"**结论**：{conclusion} [{top['evidence_id']}]")
        citations.append(str(top["evidence_id"]))
        claims.append({"text": conclusion, "evidence_ids": [str(top["evidence_id"])], "certainty": top_certainty})
        if top.get("date_status") != "verified" and not top.get("effective_from"):
            unresolved.append(f"{top.get('title', '该资料')}的生效时间尚未核实")
        # 分点说明：每条证据一个要点，带来源标号 [Sx]
        lines.append("")
        lines.append("**分点说明**：")
        for index, item in enumerate(evidence, start=1):
            label, certainty = self._source_label(item)
            text = _excerpt(str(item.get("text", "")), 200)
            lines.append(f"{index}. {label}《{item.get('title', '未命名来源')}》{text} [{item['evidence_id']}]")
            citations.append(str(item["evidence_id"]))
            claims.append({"text": text, "evidence_ids": [str(item["evidence_id"])], "certainty": certainty})
            if item.get("date_status") != "verified" and not item.get("effective_from"):
                unresolved.append(f"{item.get('title', '该资料')}的生效时间尚未核实")
        if context_packet.get("response_mode") == "experience_only":
            unresolved.append("当前结论主要来自往届学生经验，具体安排可能随届次变化")
        return {
            "answer": "\n".join(lines),
            "citations": list(dict.fromkeys(citations)),
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
            "answer 必须使用 markdown 排版：开头用 **结论**：…（加粗）给出直接结论，"
            "随后用 **分点说明** 和 1. 2. 3. 列表分点展开，每个事实点后必须带形如 [S1] 的引用标号。"
            "尽量引用多个不同来源（S1/S2/S3…），不要只依赖单一来源；citations 必须包含 answer 中出现的全部 [Sx]。"
            "引用编号只能从 evidence 中实际存在的 evidence_id（S1/S2/S3…）中选择，禁止编造证据中不存在的编号。"
            "学生经验必须明确写成学生经验，不能说成学校规定。"
            "保留原文中的不确定性和时间边界。"
            "当 can_generate 为 false 或证据不足以支撑结论时，属于拒答场景：必须输出 claims=[]、citations=[]，"
            "answer 使用软性拒答文案（例如：知识库里暂时没有找到关于「用户问题」的可靠材料，"
            "你可以点击下方「问师哥师姐」让学长学姐给你权威解答，或换个问法试试，以学校最新通知为准），"
            "并在 unresolved 中说明缺少什么信息；拒答时严禁输出任何 claims、citations 或 [Sx] 引用。"
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
    def probe(self, timeout: float = 8.0, max_tokens: int = 1) -> Tuple[bool, str, float]:
        """Minimal-cost live check against the configured provider.
        Reuses the same urllib call path as generate() but sends the smallest
        possible payload (max_tokens=1, user says "OK"), so a front-end status
        light costs only a few dozen tokens. Returns (ok, detail, latency_ms);
        detail is one of model_ok / auth_failed / network_error / timeout /
        model_error / invalid_json.
        """
        payload = json.dumps({
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": "OK"}],
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
        started = time.monotonic()
        try:
            with request.urlopen(outbound, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            data["choices"][0]["message"]["content"]  # any content => model answered
        except HTTPError as exc:
            # HTTPError is a URLError subclass, so it must be caught first.
            detail = "auth_failed" if exc.code in (401, 403) else "model_error"
            return False, detail, _elapsed_ms(started)
        except TimeoutError:
            return False, "timeout", _elapsed_ms(started)
        except URLError:
            return False, "network_error", _elapsed_ms(started)
        except OSError:
            return False, "network_error", _elapsed_ms(started)
        except (ValueError, KeyError, IndexError, TypeError):
            # ValueError covers json.JSONDecodeError (non-JSON / wrong shape).
            return False, "invalid_json", _elapsed_ms(started)
        return True, "model_ok", _elapsed_ms(started)
REFUSAL_MARKERS = (
    "没有足够的可核实材料",
    "知识库中暂时没有找到",
    "知识库里暂时没有找到",
    "无法回答",
    "不能回答",
    "无法提供",
    "无法确认",
    "拒绝回答",
    "暂不能回答",
    "不能直接作为回答依据",
)
def _looks_like_refusal(answer: str) -> bool:
    """Heuristic: is ``answer`` a refusal text rather than a factual answer?
    Used only to *loosen* validation (a refusal may legitimately carry no
    claims/citations), never to tighten it, so a false positive is harmless.
    """
    return isinstance(answer, str) and any(marker in answer for marker in REFUSAL_MARKERS)
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
    # v1.3 拒答场景宽容：can_generate=false 或 answer 本身是拒答语时，允许
    # claims/citations 为空；即使 LLM 残留了不存在的引用编号（unknown_citation）、
    # 未打 experience 标签的 peer claim，也一律不算失败——上层会清空拒答引用，
    # 保证未知问题走 LLM 合规拒答而不是回退 extractive_fallback。
    if not context_packet.get("can_generate") or _looks_like_refusal(str(answer or "")):
        return not errors, errors
    evidence = {str(item["evidence_id"]): item for item in context_packet.get("evidence", [])}
    for citation in citations:
        if citation not in evidence:
            errors.append(f"unknown_citation:{citation}")
        elif f"[{citation}]" not in str(answer):
            errors.append(f"citation_missing_from_answer:{citation}")
    if evidence and not citations:
        errors.append("grounded_answer_requires_citation")
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
