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
        question_type = str((context_packet.get("answer_plan") or {}).get("question_type", "campus_fact"))
        if not context_packet.get("can_generate"):
            route = str((context_packet.get("answer_plan") or {}).get("fallback_route", ""))
            if question_type == "credential_wording":
                answer = (
                    "我这次没有找到直接说明毕业证具体字样的有效来源，所以不能拿只提到“中外合作办学”的"
                    "相似材料替你判断。学校官网或招生问答如果没有证书样张，最稳妥的是向招生办确认具体印刷内容。"
                )
                unresolved = ["缺少直接说明毕业证版式或印刷字样的证据"]
            elif question_type == "program_offering":
                answer = (
                    "我这次没有找到同时覆盖视觉传达设计专业和完整招生范围的学校官方来源，所以不能把只提到"
                    "“中外合作办学”的相似内容当成答案。专业介绍页只能确认该版本存在，不能单独证明没有普通版本。"
                )
                unresolved = ["缺少能证明专业开设范围的官方专业目录或明确说明"]
            elif context_packet.get("response_mode") == "insufficient_contextual_evidence":
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
        evidence_limit = 3 if (
            eligible and eligible[0].get("chunk_type") == "structured_fact"
        ) or question_type in {"arrival_preparation", "credential_wording", "program_offering"} else (
            2 if question_type == "institution_structure" else 1
        )
        evidence = eligible[:evidence_limit]
        if not evidence:
            return {
                "answer": "检索到了相关资源，但当前内容不能直接作为回答依据。请查看学校最新通知或联系对应部门。",
                "citations": [],
                "claims": [],
                "unresolved": ["检索结果当前不可直接断言"],
            }

        if question_type == "institution_structure":
            item = next(
                (value for value in evidence if "校内部门单位" in str(value.get("text", ""))),
                evidence[0],
            )
            evidence_id = str(item["evidence_id"])
            text = str(item.get("text", ""))
            categories = [
                name for name in ("党群机构", "行政机构", "教学单位", "直（附）属单位")
                if name in text
            ]
            if categories:
                category_text = "、".join(categories)
                paragraphs = [
                    f"从学校信息公开页面看，中传的校内单位主要分为四类：{category_text}。"
                    "官网页面还列出了党政办公室、教务处等具体单位。"
                    f" [{evidence_id}]"
                ]
                citations = [evidence_id]
                claims = [{
                    "text": f"中传校内单位分为{category_text}",
                    "evidence_ids": [evidence_id],
                    "certainty": "official",
                }]
                unresolved: List[str] = []
                relation = next((
                    value for value in evidence
                    if value is not item and "一套班子三块牌子" in str(value.get("text", ""))
                ), None)
                if relation:
                    relation_id = str(relation["evidence_id"])
                    paragraphs.append(
                        "海南这边，学校2022年的公开报道把国际传媒教育学院、海南国际学院和境外学生教育中心"
                        "表述为“一套班子三块牌子”。也就是三块牌子共用一套管理班子；至于现在的具体分工，"
                        f"还应以学院最新发布为准。 [{relation_id}]"
                    )
                    citations.append(relation_id)
                    claims.append({
                        "text": "国际传媒教育学院、海南国际学院和境外学生教育中心实行一套班子三块牌子",
                        "evidence_ids": [relation_id],
                        "certainty": "official",
                    })
                    unresolved.extend(str(value) for value in relation.get("uncertainty", []) if str(value))
                return {
                    "answer": "\n\n".join(paragraphs),
                    "citations": citations,
                    "claims": claims,
                    "unresolved": unresolved,
                }

        if question_type == "arrival_preparation":
            official = next((item for item in evidence if item.get("authority_tier") == "official_web"), None)
            public = next((item for item in evidence if item.get("authority_tier") == "public_web"), None)
            paragraphs: List[str] = []
            citations: List[str] = []
            claims: List[Dict[str, object]] = []
            unresolved: List[str] = []
            if official:
                official_id = str(official["evidence_id"])
                source_text = str(official.get("text", ""))
                item_names = [
                    name for name in ("录取通知书", "个人档案", "户口迁移证", "党、团组织关系", "照片")
                    if name in source_text
                ]
                list_text = "、".join(item_names)
                paragraphs.append(
                    f"先把报到材料准备齐：{list_text}。这份依据来自2022级海南校区入学须知，"
                    f"可以用来提前准备，但你这一届仍要以当年最新通知为准。 [{official_id}]"
                )
                citations.append(official_id)
                claims.append({
                    "text": f"2022级海南校区入学须知列有{list_text}",
                    "evidence_ids": [official_id],
                    "certainty": "official",
                })
                unresolved.extend(str(value) for value in official.get("uncertainty", []) if str(value))
            else:
                unresolved.append("尚未取得学校官网可用的报到材料清单")
            if public:
                public_id = str(public["evidence_id"])
                public_text = str(public.get("text", ""))
                temperature = re.search(r"年平均气温\s*([0-9.]+)\s*℃", public_text)
                sunshine = re.search(r"年日照时数(?:约)?\s*([0-9]+)\s*(?:h|小时)", public_text, re.IGNORECASE)
                climate_facts = []
                if temperature:
                    climate_facts.append(f"年平均气温约{temperature.group(1)}℃")
                if sunshine:
                    climate_facts.append(f"年日照约{sunshine.group(1)}小时")
                if re.search(r"5\s*[至～~-]\s*10月|5月至10月", public_text):
                    climate_facts.append("雨季主要集中在5月至10月")
                fact_text = "、".join(climate_facts) or "光照和降雨具有明显的热带岛屿气候特征"
                paragraphs.append(
                    f"生活用品方面，陵水气候资料显示当地{fact_text}。"
                    "因此建议带防晒用品、轻薄速干衣物和便携雨具；这是根据气候作出的生活建议，不是学校强制清单。"
                    f" [{public_id}]"
                )
                citations.append(public_id)
                claims.append({
                    "text": f"陵水{fact_text}，适合准备防晒、轻薄衣物和雨具",
                    "evidence_ids": [public_id],
                    "certainty": "public",
                })
                unresolved.extend(str(value) for value in public.get("uncertainty", []) if str(value))
            return {
                "answer": "\n\n".join(paragraphs),
                "citations": citations,
                "claims": claims,
                "unresolved": list(dict.fromkeys(unresolved)),
            }

        if question_type == "credential_wording":
            direct = next((
                item for item in evidence
                if bool((item.get("evidence_coverage") or {}).get("direct_answer"))
            ), None)
            official = next((
                item for item in evidence
                if item.get("authority_tier") in {"official_web", "official_policy", "official_guidance"}
                and "joint_program" in set((item.get("evidence_coverage") or {}).get("covered_aspects", []))
            ), None)
            paragraphs: List[str] = []
            citations: List[str] = []
            claims: List[Dict[str, object]] = []
            unresolved = ["暂未找到学校官网展示的证书样张，具体印刷字样仍可向招生办确认"]
            if direct:
                direct_id = str(direct["evidence_id"])
                paragraphs.append(
                    "从目前能核对到的公开问答看，中外合作办学专业的毕业证、学位证与其他专业“没有不同，"
                    "完全一致”。按这段问答的表述，不应额外出现“中外合作办学”字样；不过该页面是第三方"
                    f"转载的2024年招生问答，不是证书样张。 [{direct_id}]"
                )
                citations.append(direct_id)
                claims.append({
                    "text": "公开问答称中外合作办学专业毕业证、学位证与其他专业没有不同、完全一致",
                    "evidence_ids": [direct_id],
                    "certainty": "public",
                })
            else:
                paragraphs.append(
                    "现在还不能仅凭学校名称或“中外合作办学”相关页面判断毕业证上有没有这几个字；"
                    "现有来源没有直接说明证书版式或印刷字样。"
                )
            if official:
                official_id = str(official["evidence_id"])
                paragraphs.append(
                    "学校官网能够确认的是：达到相应毕业和学位条件后，授予中国传媒大学毕业证书和学士学位。"
                    f"这能证明颁发的证书类型，但单独不能证明证书上具体印什么。 [{official_id}]"
                )
                citations.append(official_id)
                claims.append({
                    "text": "达到条件后授予中国传媒大学毕业证书和学士学位",
                    "evidence_ids": [official_id],
                    "certainty": "official",
                })
            return {
                "answer": "\n\n".join(paragraphs),
                "citations": citations,
                "claims": claims,
                "unresolved": unresolved,
            }

        if question_type == "program_offering":
            direct = next((
                item for item in evidence
                if bool((item.get("evidence_coverage") or {}).get("direct_answer"))
            ), None)
            existence = next((
                item for item in evidence
                if {"program", "joint_program"}.issubset(set(
                    (item.get("evidence_coverage") or {}).get("covered_aspects", [])
                ))
            ), None)
            if direct:
                evidence_id = str(direct["evidence_id"])
                return {
                    "answer": (
                        "是的。就这份学校官方专业范围来看，视觉传达设计在海南国际学院以中外合作办学形式开设，"
                        f"没有列出普通版本。 [{evidence_id}]"
                    ),
                    "citations": [evidence_id],
                    "claims": [{
                        "text": "视觉传达设计在所引官方范围内仅以中外合作办学形式开设",
                        "evidence_ids": [evidence_id],
                        "certainty": "official",
                    }],
                    "unresolved": [],
                }
            if existence:
                evidence_id = str(existence["evidence_id"])
                return {
                    "answer": (
                        "学校官网能确认海南国际学院有视觉传达设计（中外合作办学）专业。"
                        "但这份专业介绍只能证明这个版本存在，不能单独证明没有非中外合作办学版本。"
                        f"要回答“只有吗”，还需要学校完整的招生专业目录或明确说明。 [{evidence_id}]"
                    ),
                    "citations": [evidence_id],
                    "claims": [{
                        "text": "海南国际学院开设视觉传达设计（中外合作办学）专业",
                        "evidence_ids": [evidence_id],
                        "certainty": "official",
                    }],
                    "unresolved": ["现有来源不能证明是否不存在普通版本"],
                }

        paragraphs: List[str] = []
        citations: List[str] = []
        claims: List[Dict[str, object]] = []
        unresolved: List[str] = []
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
            elif authority == "official_web":
                prefix = "学校官网显示，"
                certainty = "official"
            elif authority == "public_web":
                prefix = (
                    "公开资料显示，" if question_type == "general_guidance"
                    else "结合公开的陵水环境资料，"
                )
                certainty = "public"
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
                if item.get("date_status") == "live_page":
                    pass
                elif item.get("date_status") == "historical_reference":
                    unresolved.append(f"{item.get('title', '该资料')}是历史发布，当前届次仍需核对最新通知")
                elif authority == "peer_experience":
                    unresolved.append(f"{item.get('title', '该学生资料')}的对应届次尚未完整标明")
                elif authority not in {"public_web"}:
                    unresolved.append(f"{item.get('title', '该资料')}的生效时间尚未核实")
            unresolved.extend(str(value) for value in item.get("uncertainty", []) if str(value))

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
    def normalize_reference(item: object) -> object:
        if isinstance(item, dict) and item.get("evidence_id"):
            item = item["evidence_id"]
        match = re.fullmatch(r"\[?(S\d+)\]?", str(item).strip())
        return match.group(1) if match else item

    normalized = dict(value)
    unresolved = normalized.get("unresolved")
    if unresolved is None:
        normalized["unresolved"] = []
    elif isinstance(unresolved, str):
        normalized["unresolved"] = (
            [] if unresolved.strip().lower() in {"", "无", "暂无", "none", "n/a"}
            else [unresolved]
        )
    elif isinstance(unresolved, list):
        normalized["unresolved"] = [
            item for item in unresolved
            if str(item).strip().lower() not in {"", "无", "暂无", "none", "n/a"}
        ]
    citations = normalized.get("citations")
    if isinstance(citations, list):
        normalized["citations"] = [normalize_reference(item) for item in citations]
    elif isinstance(citations, str):
        normalized["citations"] = list(dict.fromkeys(re.findall(r"S\d+", citations)))
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
                normalized_claim["evidence_ids"] = [normalize_reference(item) for item in evidence_ids]
            normalized_claims.append(normalized_claim)
        normalized["claims"] = normalized_claims
    return normalized


def _resolve_evidence_references(
    value: Dict[str, object], context_packet: Dict[str, object]
) -> Dict[str, object]:
    """Map provider-returned evidence titles back to stable evidence IDs."""
    references: Dict[str, str] = {}
    for item in context_packet.get("evidence", []):
        evidence_id = str(item.get("evidence_id", ""))
        if not evidence_id:
            continue
        references[evidence_id] = evidence_id
        references[f"[{evidence_id}]"] = evidence_id
        references[str(item.get("title", ""))] = evidence_id
        if item.get("source_url"):
            references[str(item["source_url"])] = evidence_id

    def resolve(reference: object) -> str:
        if isinstance(reference, dict):
            resolved_ids = {
                candidate
                for key in ("evidence_id", "source_url", "url", "title", "source")
                if reference.get(key)
                for candidate in [resolve(reference[key])]
                if candidate in references.values()
            }
            if len(resolved_ids) == 1:
                return next(iter(resolved_ids))
        raw = str(reference).strip()
        if raw in references:
            return references[raw]
        prefixed_id = re.match(r"^\[?(S\d+)(?:\]|\b)", raw)
        if prefixed_id and prefixed_id.group(1) in references:
            return prefixed_id.group(1)
        cleaned = raw.strip("[]【】《》（）()\"' ")
        if cleaned in references:
            return references[cleaned]
        title_matches = {
            evidence_id
            for title, evidence_id in references.items()
            if title and not re.fullmatch(r"\[?S\d+\]?", title)
            and (cleaned in title or title in cleaned)
        }
        return next(iter(title_matches)) if len(title_matches) == 1 else raw

    resolved = dict(value)
    if isinstance(resolved.get("citations"), list):
        resolved["citations"] = list(dict.fromkeys(
            resolve(item) for item in resolved["citations"]
        ))
    claims = resolved.get("claims")
    if isinstance(claims, list):
        updated_claims: List[object] = []
        for claim in claims:
            if not isinstance(claim, dict):
                updated_claims.append(claim)
                continue
            updated = dict(claim)
            if isinstance(updated.get("evidence_ids"), list):
                updated["evidence_ids"] = list(dict.fromkeys(
                    resolve(item) for item in updated["evidence_ids"]
                ))
            updated_claims.append(updated)
        resolved["claims"] = updated_claims
    return resolved


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
            "official_web 是学校官网实时取得的材料；public_web 是公开网络背景，只能支撑环境事实和实用建议，不能支撑学校制度。"
            "公开网页转载的招生问答只能标为public参考，不能冒充学校官网；如果它直接回答证书样式，可以有限引用并保留核验边界。"
            "绝对不要把 public_web 支撑的建议说成学长学姐经验；只有 peer_experience 才能称为学生经验。"
            "遇到 arrival_preparation，要先分清‘学校要求带的材料’和‘结合陵水环境建议带的生活用品’，不要混成一份官方清单。"
            "回答 institution_structure 时，不得在证据没有明说的情况下推断‘二级学院’、‘与其他学院平行’或‘教学单位之一’等层级关系。"
            "旧年份入学须知只能表述为往届官方要求，并提醒核对当前届次；可以依据气候证据合理建议防晒、轻薄速干衣物和雨具，但要说成建议。"
            "往届人数或比例不能表达成每届固定名额；政策存在也不能推导出某个班的具体名额。"
            "如果 evidence 没有最低排名，绝对不能把往届比例反推成‘班级前多少名/百分之多少就能保研’。"
            "保留必要的时间边界，但不要反复使用免责声明。can_generate 为 false 时只能说明缺少什么和下一步去哪类来源查。"
            "严格遵循 answer_plan 中的 question_type、guidance 和 response_shape。"
            "仅输出 JSON 对象，字段必须为 answer、citations、claims、unresolved。"
            "claims 中每项必须包含 text、evidence_ids、certainty；certainty 只能是 official、experience、public、unverified。"
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
        normalized = _normalize_model_response(_extract_json(str(content)))
        return _resolve_evidence_references(normalized, context_packet)


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
    visible = display_answer(str(answer or ""))
    if answer_plan.get("question_type") == "historical_outcome":
        evidence_has_rank = any(
            any(term in str(item.get("text", "")) for term in ("最低排名", "排名前", "班级前", "排到第"))
            for item in evidence.values()
        )
        inferred_rank = bool(re.search(
            r"(?:(?:名额|排名|班级前)[^。]{0,24}\d+(?:\.\d+)?%|排到第\s*\d+)",
            visible,
        ))
        if inferred_rank and not evidence_has_rank:
            errors.append("historical_outcome_inferred_rank_without_evidence")
    if answer_plan.get("question_type") == "arrival_preparation":
        practical_terms = ("防晒", "速干", "轻薄", "雨具", "雨伞", "雨衣", "防潮", "炎热", "潮湿", "高温", "多雨")
        gives_environment_advice = any(term in visible for term in practical_terms)
        public_ids = {
            evidence_id for evidence_id, item in evidence.items()
            if item.get("authority_tier") == "public_web"
        }
        if gives_environment_advice and not public_ids.intersection(str(item) for item in citations):
            errors.append("arrival_environment_advice_without_public_web_citation")
    if answer_plan.get("question_type") == "institution_structure":
        hierarchy_terms = ("二级学院", "与其他学院平行", "教学单位之一")
        for term in hierarchy_terms:
            if term in visible and not any(term in str(item.get("text", "")) for item in evidence.values()):
                errors.append(f"institution_hierarchy_inferred_without_evidence:{term}")
    if answer_plan.get("question_type") == "credential_wording":
        makes_wording_claim = bool(re.search(
            r"(?:没有不同|完全一致|没有区别|不(?:会|应)[^。]{0,24}(?:中外合作办学|中外合办)[^。]{0,12}字样|"
            r"没有[^。]{0,24}(?:中外合作办学|中外合办)[^。]{0,12}字样)",
            visible,
        ))
        direct_ids = {
            evidence_id for evidence_id, item in evidence.items()
            if bool((item.get("evidence_coverage") or {}).get("direct_answer"))
        }
        if makes_wording_claim and not direct_ids.intersection(str(item) for item in citations):
            errors.append("credential_wording_claim_without_direct_evidence")
    if answer_plan.get("question_type") == "program_offering":
        makes_exclusive_claim = bool(re.search(
            r"(?:只有|仅有|仅开设|只开设|没有[^。]{0,16}(?:普通|非中外合作|非中外合办))",
            visible,
        ))
        direct_ids = {
            evidence_id for evidence_id, item in evidence.items()
            if bool((item.get("evidence_coverage") or {}).get("direct_answer"))
        }
        if makes_exclusive_claim and not direct_ids.intersection(str(item) for item in citations):
            errors.append("program_exclusivity_claim_without_scoped_evidence")
    peer_language = any(term in visible for term in ("学长学姐", "往届学生经验", "学生经验"))
    cited_peer = any(
        evidence.get(str(citation), {}).get("authority_tier") == "peer_experience"
        for citation in citations
    )
    if peer_language and not cited_peer:
        errors.append("student_experience_language_without_peer_evidence")
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
        if certainty not in {"official", "experience", "public", "unverified"}:
            errors.append(f"claim_invalid_certainty:{index}")
        if any(item.get("authority_tier") == "peer_experience" for item in cited) and certainty != "experience":
            errors.append(f"peer_claim_not_labeled_experience:{index}")
        if certainty == "official" and not any(
            item.get("authority_tier") in {"official_policy", "official_guidance", "official_web"}
            and item.get("assertion_policy") not in {"navigation_only", "do_not_assert", "do_not_assert_until_ocr"}
            for item in cited
        ):
            errors.append(f"official_claim_without_official_evidence:{index}")
        if cited and all(item.get("authority_tier") == "public_web" for item in cited) and certainty != "public":
            errors.append(f"public_web_claim_not_labeled_public:{index}")
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
