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
                    "你可以① 点击下方「转师哥师姐答疑」，让师哥师姐给你解答；② 换个问法试试。"
                    "以学校最新通知为准。"
                )
                unresolved = ["缺少同时匹配原问题主题和当前追问对象的证据"]
            else:
                answer = (
                    f"知识库里暂时没有找到关于「{topic}」的可靠材料。"
                    "你可以① 点击下方「转师哥师姐答疑」，让师哥师姐给你解答；② 换个问法试试。"
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
            # v1.6 强相关导航型证据（如「中传2026-2027年度校历」resource chunk，
            # assertion_policy=navigation_only）：只给出资源定位，不断言内容。
            navigation = [
                item for item in context_packet.get("evidence", [])
                if item.get("assertion_policy") == "navigation_only"
            ]
            if navigation:
                lines: List[str] = ["**为你找到相关资源**："]
                citations: List[str] = []
                for index, item in enumerate(navigation, start=1):
                    eid = str(item.get("evidence_id", f"S{index}"))
                    title = str(item.get("title") or "未命名资源")
                    lines.append(f"{index}. 《{title}》 [{eid}]")
                    file_path = item.get("file_path") or item.get("url")
                    if file_path:
                        lines.append(f"    打开：{file_path}")
                    citations.append(eid)
                lines.append("")
                lines.append("以上资源来自本地知识库，点击上方来源卡片可直接打开查看。")
                return {
                    "answer": "\n".join(lines),
                    "citations": list(dict.fromkeys(citations)),
                    "claims": [],
                    "unresolved": ["当前为导航型资源，具体内容以来源页面为准"],
                }
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
        # v1.7 生成链路改造：删掉"结论→分点→[Sx]"强制模板与"只能依据 evidence"
        # 的命令式约束，改为宽松师哥师姐人设——相关才引用、不相关明说、没把握就拒。
        # judgment/web_search_needed 为可选字段（意图声明不是判决），解析失败由
        # 后端启发式兜底，绝不让缺失成为硬失败。
        system = (
            "你是新生入学智能助手，帮新生解答校园生活、学业政策相关问题。"
            "语气自然亲切、简洁清晰，像一个了解学校情况的可靠助手，不端着、不机械。\n"
            "\n"
            "【先判断，再回答】\n"
            "1. 看下面的 evidence 材料，先判断它们和用户的问题相不相关：\n"
            "   - 相关 → 用材料回答。结论先行，可以自然地说\"据入学指南\"\"按学校规定\"，但不要逐条抄材料。\n"
            "   - 不相关 → 直接说明：\"这个问题材料里暂时没有相关的内容，我不太确定。\""
            "不要提到自己搜到了什么材料（不要暴露检索到的话题、文件名或细节），"
            "直接引导用户换个问法，或建议转师哥师姐答疑。"
            "如果问题与咱们学校完全无关（比如问其他学校、其他城市、其他公司），即使你知道常识答案，"
            "也不要展开回答，直接说明没有相关资料，并引导用户换个问法或转师哥师姐答疑。\n"
            "   - 材料不够 / 没有把握 → 明确说\"这个我暂时不确定，材料里没有\"，不要编造；"
            "建议用户点击「转师哥师姐答疑」，或查看学校最新通知。\n"
            "2. 引用是可选的，但如果写了 [S1] 这类编号，必须真实对应下面 evidence 里的编号，禁止编造编号。\n"
            "3. 学生经验（peer_experience）要自然说明是往届经验，不要说成学校规定。\n"
            "4. 材料里的时间、条件、待核实内容，如实保留，不要抹掉不确定性。\n"
            "5. 用户问题涉及联网信息（如近期新闻、最新通知）且本地材料明显不足时，可以声明需要联网搜索。"
            "特别是天气、实时新闻、票价这类需要实时/外部信息的问题，本地材料没有时，"
            "应输出 web_search_needed=true（judgment 可为 need_web），而不是直接拒答。\n"
            "   注意：校历、作息时间、教学安排、培养方案、报到安排等是学校的固定文件，不属于实时信息——"
            "本地材料（evidence）里有相关内容时，直接用本地材料回答，即使问题带着\"今年\"\"最新\"\"哪个\""
            "这样的字眼，也不要触发联网搜索；只有本地确实没有相关校历/安排时才考虑联网。\n"
            "\n"
            "【组织方式】\n"
            "- 结论先行：先直接给新生最想知道的答案。\n"
            "- 用自然亲切的口吻，可以加\"建议你\"\"到时候可以\"这类表达。\n"
            "- 可以分点，也可以不分点，怎么清楚怎么来；不要为了凑格式硬套\"结论/分点说明\"。\n"
            "- 回答控制在 2-4 段以内，别啰嗦。\n"
            "- 材料是问答条目/同学经验（faq、peer）时，用自己的话转述成回答语气"
            "（比如\"据往届师哥师姐的经验，XX\"），不要把材料的原文整段照搬，"
            "材料文字是素材不是台词。\n"
            "\n"
            "【输出】\n"
            "输出 JSON：{\"answer\": \"...\", \"citations\": [...], \"claims\": [...], \"unresolved\": [...],"
            " \"judgment\": \"answer|refuse|need_web\", \"web_search_needed\": true/false}\n"
            "judgment 和 web_search_needed 是可选字段，写不出来就省略，不要为了凑字段硬编。\n"
            "claims 中每项可包含 text、evidence_ids、certainty；certainty 可以是 official、experience、unverified。"
        )
        glossary_hint = str(context_packet.get("glossary_hint") or "").strip()
        if glossary_hint:
            system = system + "\n" + glossary_hint
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

    def rewrite_search_query(self, query: str, timeout: float = 8.0, max_tokens: int = 64) -> str:
        """Rewrite a user question into a concise search-engine query (v1.6).

        Lightweight one-shot call (short max_tokens, short timeout) that turns
        colloquial phrasing into keyword-friendly text, e.g.
        「园区摆渡车怎么坐」→「海南陵水黎安国际教育创新试验区 摆渡车」.
        Returns the rewritten string, or the original query when the model
        returns nothing usable. Raises on network/parse errors so the module
        level rewrite_search_query() can silently fall back.
        """
        payload = json.dumps({
            "model": self.config.model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": (
                    "你是搜索引擎查询改写助手。把用户口语化的问题改写成适合搜索引擎的"
                    "简洁关键词查询：去口语/语气词，保留核心实体与关键词，可用空格分隔。"
                    "只输出改写后的查询文本本身，不要解释、不要引号、不要 JSON。"
                )},
                {"role": "user", "content": query},
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
        with request.urlopen(outbound, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        rewritten = str(content or "").strip().strip('"').strip()
        return rewritten or query
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
    # v1.7 宽松人设下 LLM 的自然拒答/不相关表达（启发式兜底用）
    "不太相关",
    "不太沾边",
    "不相关",
    "暂时不确定",
    "不确定",
    "材料里没有",
    "没有找到相关",
    "找不到相关",
    "没有这方面的材料",
    "没有相关资料",
    "这个问题我答不上来",
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
    """v1.7 校验精简：只保留结构完整性 + unknown_citation（防编造）两条硬校验。

    删除 v1.3 的繁琐规则：拒答整体跳过、citation_missing_from_answer、
    grounded_answer_requires_citation、peer_claim_not_labeled_experience、
    official_claim_without_official_evidence 全部降级为警告（返回 errors 但不判失败，
    由调用方写 composer_warning 日志）。拒答场景同样过引用白名单——LLM 说"材料不相关"
    时可能引用 S3 证明不相关，不能被一刀切跳过。
    """
    errors: List[str] = []
    warnings: List[str] = []
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
    # 核心防编造校验：引用编号必须真实存在于 evidence（所有场景，含拒答）。
    for citation in citations:
        if citation not in evidence:
            errors.append(f"unknown_citation:{citation}")
        elif f"[{citation}]" not in str(answer):
            warnings.append(f"citation_missing_from_answer:{citation}")
    if evidence and not citations:
        warnings.append("grounded_answer_requires_citation")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            warnings.append(f"claim_not_object:{index}")
            continue
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            warnings.append(f"claim_missing_evidence:{index}")
            continue
        cited = [evidence.get(str(item)) for item in evidence_ids]
        if any(item is None for item in cited):
            errors.append(f"claim_unknown_evidence:{index}")
            continue
        certainty = claim.get("certainty")
        if any(item.get("authority_tier") == "peer_experience" for item in cited) and certainty != "experience":
            warnings.append(f"peer_claim_not_labeled_experience:{index}")
        if certainty == "official" and not any(
            item.get("authority_tier") in {"official_policy", "official_guidance"}
            and item.get("assertion_policy") not in {"navigation_only", "do_not_assert", "do_not_assert_until_ocr"}
            for item in cited
        ):
            warnings.append(f"official_claim_without_official_evidence:{index}")
    if warnings:
        # 警告只进日志不进错误；保留在返回值里供调用方记录 composer_warning。
        composition["_warnings"] = warnings
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


def rewrite_search_query(query: str, timeout: float = 8.0) -> str:
    """LLM rewrite of a web-search query (v1.6); never raises.

    Applies only to the outbound web-search query — the local retrieval path is
    untouched. Falls back silently to the input query when the provider is
    unconfigured, the call fails/times out, or the model returns nothing
    usable, so web search never blocks on a missing/broken LLM key.
    """
    config = ProviderConfig.from_env()
    if not config.enabled:
        return query
    try:
        return OpenAICompatibleComposer(config).rewrite_search_query(query, timeout=timeout)
    except Exception:
        return query
