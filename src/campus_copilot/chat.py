import json
import logging
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import DIALOG_LOG_DIR, FREE_ANSWER_MIN_REFERENCE, FREE_ANSWER_MIN_REFERENCE_TYPES, LLM_FREE_ANSWER

from .composition import (
    AnswerComposer,
    ExtractiveComposer,
    _looks_like_refusal,
    configured_composer,
    rewrite_search_query,
    validate_composition,
)
from .context_builder import build_citation_metadata, build_context_packet, build_visual_evidence
from .retrieval import rewrite_query_with_glossary
from .search import search_web_tiered, sort_by_tier


FOLLOW_UP_MARKERS = ("那", "这个", "它", "还有", "上面", "刚才", "呢", "具体", "怎么办", "为什么")
CORRECTION_PATTERNS = (
    re.compile(r"(?:我说的是|我是说|指的是|应该是)\s*([^，。！？；]+)"),
    re.compile(r"不是[^，。！？；]+[，,；;]\s*(?:是|而是)\s*([^，。！？；]+)"),
)

# v1.7 实时信息兜底：LLM 对天气/新闻/票价等实时问题可能倾向拒答而非 need_web，
# query 命中这些词且 judgment=refuse 时，后端强制尝试联网（兜底，非硬约束）。
REALTIME_TERMS = ("天气", "新闻", "最新", "预报", "今天", "明天", "实时", "温度", "台风", "降雨", "雨", "气温")


def _compose_web_answer(query: str, results: List[Dict[str, object]]) -> Dict[str, object]:
    """Assemble a web-search answer deterministically (no second LLM call).

    The internet results are quoted as-is with W1..Wn citations. When the
    search carried site_tier tags and no official source matched, a note is
    appended so the user knows the answer is not from the school (v1.4).
    """
    lines = [f"**联网搜索结果**（关于「{query}」）："]
    for index, item in enumerate(results, start=1):
        title = str(item.get("title") or "").strip()
        snippet = (str(item.get("snippet") or "") or title).strip()
        link = str(item.get("link") or "").strip()
        lines.append(f"{index}. **{title}**：{snippet} [W{index}]")
        if link:
            lines.append(f"   来源：{link}")
    lines.append("")
    has_tier = any("site_tier" in item for item in results)
    has_official = any(str(item.get("site_tier")) == "official" for item in results)
    if has_tier and not has_official:
        lines.append("以下为全网公开信息，非学校官方发布，请以官方为准。")
    lines.append("以上结果来自互联网搜索，并非学校官方信息，仅供参考。你可以点击下方「问师哥师姐」，让学长学姐给你更贴近校园的解答；也可以换个问法试试。以学校最新通知为准。")
    return {
        "answer": "\n".join(lines),
        "citations": [f"W{index}" for index in range(1, len(results) + 1)],
        "claims": [],
        "unresolved": ["联网搜索结果未经学校官方核实，仅供参考"],
    }


def _search_web_results(query: str) -> Tuple[Optional[List[Dict[str, object]]], Optional[str], Optional[str]]:
    """Run the tiered web search and stable-sort results by source priority.

    Query pipeline (v1.6):
      1. glossary rewrite with for_web=True — colloquial terms become full names
         even when rewrite=false for local retrieval ("园区"→"海南陵水黎安国际
         教育创新试验区"), because a web engine has no corpus-frequency signal.
      2. LLM rewrite (rewrite_search_query) — organizes the glossary-rewritten
         text into a short keyword query; silently falls back on failure/missing
         key. Only affects the outbound search query, never local retrieval.

    Returns (results, search_tier_used, search_query_used); the first two are
    None when search is disabled or every stage failed. Shared by the
    can_generate=False path and the LLM-refusal fallback so the search is only
    attempted once per ask.
    """
    glossary_query = rewrite_query_with_glossary(query, for_web=True)
    search_query = rewrite_search_query(glossary_query)
    tiered = search_web_tiered(search_query)
    if not tiered:
        return None, None, search_query
    return sort_by_tier(tiered["results"]), tiered.get("search_tier_used"), search_query


def _build_web_citation_payload(
    query: str, web_results: List[Dict[str, object]]
) -> Tuple[Dict[str, object], List[str], Dict[str, object]]:
    """Compose the web-search answer plus its W-series citations/metadata.

    Returns (composition, citations, citation_metadata); used by both the
    unknown-question path and the LLM-refusal fallback.
    """
    composition = _compose_web_answer(query, web_results)
    citations = [str(item) for item in composition.get("citations", [])]
    citation_metadata = {
        f"W{index}": {
            "evidence_id": f"W{index}",
            "title": item.get("title"),
            "file_path": None,
            "type": "link",
            "image_thumb": None,
            "url": item.get("link"),
            "page": None,
            "authority": "web_search",
            "assertion_policy": None,
            "chunk_type": "web_search",
            "excerpt": (str(item.get("snippet") or "")[:300]),
        }
        for index, item in enumerate(web_results, start=1)
    }
    return composition, citations, citation_metadata


class SessionStore:
    def __init__(self, max_turns: int = 4, max_sessions: int = 500):
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._sessions: "OrderedDict[str, List[Dict[str, object]]]" = OrderedDict()
        self._lock = threading.Lock()
        self.last_updated_at: Optional[str] = None

    def stats(self) -> Dict[str, object]:
        """Lightweight runtime stats for the admin panel (T2)."""
        with self._lock:
            return {
                "count": len(self._sessions),
                "max_sessions": self.max_sessions,
                "max_turns": self.max_turns,
                "last_updated_at": self.last_updated_at,
            }

    def history(self, session_id: str) -> List[Dict[str, object]]:
        with self._lock:
            history = list(self._sessions.get(session_id, []))
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
            return history

    def append(self, session_id: str, query: str, answer: str, citations: List[str]) -> None:
        with self._lock:
            history = self._sessions.setdefault(session_id, [])
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": answer, "citations": citations})
            self._sessions[session_id] = history[-self.max_turns * 2:]
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            self.last_updated_at = datetime.now().isoformat(timespec="seconds")

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self.last_updated_at = datetime.now().isoformat(timespec="seconds")

    def clear_all(self) -> None:
        """Clear every in-memory session (T1).

        Memory only: never touches persistent dialog logs under data/logs/.
        """
        with self._lock:
            self._sessions.clear()
            self.last_updated_at = datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 全局对话自动存档（T3）：data/logs/<session_id>.md
# 与 SessionStore（内存运行时）严格分离——清空会话不影响已落盘日志。
# MD 即纯文本格式，同一文件可直接当 .txt 查看，无需额外转换。
# ---------------------------------------------------------------------------
_HEADER_WRITTEN: "set[str]" = set()
_HEADER_LOCK = threading.Lock()


def _append_dialog_log(
    session_id: str,
    role: str,
    text: str,
    metadata: Optional[Dict[str, object]] = None,
) -> None:
    """Append one turn (user or ai) to data/logs/<session_id>.md.

    Best-effort by design: any failure is logged as a warning and swallowed so
    dialog logging can never block or break Q&A.
    """
    try:
        DIALOG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = DIALOG_LOG_DIR / f"{session_id}.md"
        timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        if metadata:
            # 轮次块后跟空行再放 metadata 注释，保证每块之间有空行分隔（MD 规范）
            block = [
                f"## {timestamp} | {role}",
                str(text),
                "",
                "<!-- metadata: " + json.dumps(metadata, ensure_ascii=False) + " -->",
                "",
                "",
            ]
        else:
            block = [f"## {timestamp} | {role}", str(text), "", ""]
        with _HEADER_LOCK:
            first_time = session_id not in _HEADER_WRITTEN and not path.exists()
            if first_time:
                _HEADER_WRITTEN.add(session_id)
        with open(path, "a", encoding="utf-8") as fh:
            if first_time:
                fh.write(
                    f"# 对话日志：{session_id}\n\n"
                    f"- 创建时间：{timestamp}\n"
                    f"- 来源：XiaohaiGPT 新生问答（campus-onboarding-copilot）\n"
                    f"- 说明：本文件为纯文本 Markdown（MD 即文本格式，可直接当 .txt 查看）\n\n"
                )
            fh.write("\n".join(block))
    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger(__name__).warning("dialog log append failed for %s: %s", session_id, exc)


def contextualize_query(query: str, history: List[Dict[str, object]]) -> str:
    for pattern in CORRECTION_PATTERNS:
        match = pattern.search(query)
        if match:
            return match.group(1).strip()
    user_messages = [str(item.get("content", "")) for item in history if item.get("role") == "user"]
    if not user_messages:
        return query
    compact = re.sub(r"\s+", "", query)
    looks_like_follow_up = any(marker in compact[:12] for marker in FOLLOW_UP_MARKERS)
    if not looks_like_follow_up:
        return query
    return f"{user_messages[-1]}；追问：{query}"


class GroundedChatService:
    def __init__(
        self,
        retriever: object,
        composer: Optional[AnswerComposer] = None,
        sessions: Optional[SessionStore] = None,
        provider_status: Optional[Dict[str, object]] = None,
    ):
        if composer is None:
            composer, detected_status = configured_composer()
            provider_status = provider_status or detected_status
        self.retriever = retriever
        self.composer = composer
        self.sessions = sessions or SessionStore()
        self.provider_status = provider_status or {"mode": "custom", "adapter": composer.name}
        self.fallback = ExtractiveComposer()

    def ask(
        self,
        query: str,
        session_id: Optional[str] = None,
        profile: Optional[Dict[str, str]] = None,
        top_k: int = 6,
    ) -> Dict[str, object]:
        session_id = session_id or uuid.uuid4().hex
        _append_dialog_log(session_id, "user", query)
        history = self.sessions.history(session_id)
        retrieval_query = contextualize_query(query, history)
        retrieval_query = rewrite_query_with_glossary(retrieval_query)
        effective_profile = dict(profile or {})
        if "研究生" in query:
            effective_profile["student_level"] = "graduate"
        elif "本科" in query:
            effective_profile["student_level"] = "undergraduate"
        retrieval = self.retriever.search(retrieval_query, top_k, effective_profile)
        if retrieval_query != query and retrieval.get("results"):
            top_explanation = retrieval["results"][0].get("score_explanation", {})
            coverage = top_explanation.get("concept_coverage")
            if coverage is not None and float(coverage) < 0.6:
                retrieval["answerability"] = "insufficient_contextual_evidence"
                retrieval["insufficient_reason"] = "top_evidence_does_not_cover_enough_of_the_prior_topic"
        retrieval["query"] = query
        context = build_context_packet(retrieval, history)
        can_generate = context.get("can_generate", False)

        # v1.7 生成链路架构：检索负责找、LLM 负责判。
        # LLM_FREE_ANSWER=1（默认上线）= 新链路：检索降为候选召回（top_k 8），
        # 拆除 can_generate 硬阈值，LLM 一次输出 answer+软 judgment 自主判断，
        # 联网由 LLM 意图 + 空结果硬触发兜底。=0 = 旧链路（现状，保留回退）。
        if LLM_FREE_ANSWER:
            return self._ask_free_answer(
                session_id, query, retrieval_query, retrieval, context, history, effective_profile
            )

        warning: Optional[str] = None
        composer_used = self.composer.name
        source = "corpus"
        search_results: Optional[List[Dict[str, object]]] = None
        search_tier_used: Optional[str] = None
        search_query_used: Optional[str] = None
        web_results: Optional[List[Dict[str, object]]] = None
        web_tried = False

        # v1.3：未知问题（语料判定不可生成）优先尝试联网搜索（可选，需
        # SEARCH_API_KEY）。搜索成功则返回联网回答（source=web_search），不硬性
        # 拒答；未配置或失败则走下方 LLM 合规拒答 / 软拒答（source=refusal）。
        if not can_generate:
            web_results, search_tier_used, search_query_used = _search_web_results(query)
            web_tried = True
            if web_results:
                composition, citations, citation_metadata = _build_web_citation_payload(query, web_results)
                self.sessions.append(session_id, query, str(composition["answer"]), citations)
                _append_dialog_log(
                    session_id,
                    "ai",
                    str(composition["answer"]),
                    {"source": "web_search", "answerable": False, "composer": composer_used},
                )
                return {
                    "session_id": session_id,
                    "query": query,
                    "retrieval_query": retrieval_query,
                    "answer": composition["answer"],
                    "answerable": False,
                    "unknown": True,
                    "answerability": retrieval["answerability"],
                    "insufficient_reason": retrieval.get("insufficient_reason"),
                    "citations": citations,
                    "citation_metadata": citation_metadata,
                    "visual_evidence": build_visual_evidence(retrieval),
                    "claims": [],
                    "unresolved": composition.get("unresolved", []),
                    "composer": composer_used,
                    "composer_warning": None,
                    "evidence": [],
                    "conversation_turns": len(history) // 2 + 1,
                    "source": "web_search",
                    "search_results": web_results,
                    "search_tier_used": search_tier_used,
                    "web_search_query": search_query_used,
                }

        # v1.6：强相关导航型证据（resource/official navigation_only，如校历）
        # 直接走抽取式导航回答——这类证据只有资源定位、无正文可断言，交给 LLM
        # 容易输出拒答语并误触发联网；导航回答确定且稳定（source 恒为 corpus）。
        strong_navigation = bool(
            can_generate
            and context.get("response_mode") == "supported_with_context"
            and bool(context.get("evidence"))
            and context["evidence"][0].get("assertion_policy") == "navigation_only"
        )
        try:
            if strong_navigation:
                # 只保留 navigation_only 证据，让 fallback 走导航型回答分支——
                # 否则 eligible 里其它弱相关文本证据（如问答库"校门口打车"）会
                # 抢占答案，答非所问。
                nav_context = dict(context)
                nav_context["evidence"] = [
                    item for item in context["evidence"]
                    if item.get("assertion_policy") == "navigation_only"
                ]
                composition = self.fallback.generate(nav_context)
                composer_used = self.fallback.name
            else:
                composition = self.composer.generate(context)
            valid, errors = validate_composition(composition, context)
            if not valid:
                warning = "model_output_failed_grounding_validation:" + ",".join(errors)
                composition = self.fallback.generate(context)
                composer_used = self.fallback.name
        except Exception:
            warning = "model_provider_unavailable_fallback_used"
            composition = self.fallback.generate(context)
            composer_used = self.fallback.name

        citations = [str(item) for item in composition.get("citations", [])]
        can_generate = context.get("can_generate", False)
        citation_metadata = build_citation_metadata(context)
        # Visual evidence (images/QR) is a separate channel: it is returned even
        # on refusal as "相关图片佐证" cards, but never makes a query answerable.
        visual_evidence = build_visual_evidence(retrieval)
        # v1.3：即使检索判定可生成，LLM 仍可能因证据不足而输出拒答语——此时统一
        # 按拒答处理（answerable=false、source=refusal、清空语料引用），保证前端
        # 语义一致（不会出现 answerable=true 但内容是拒答文案的情况）。
        answered = bool(can_generate) and not _looks_like_refusal(str(composition.get("answer", "")))
        if not answered:
            # v1.4.1：LLM 拒答（含 can_generate=True 但证据不足输出拒答的场景）同样
            # 尝试联网搜索兜底；can_generate=False 时 143 行已搜过（web_tried），
            # 不再重复搜索。搜索成功则用联网回答覆盖（source=web_search、W 系列引用），
            # 搜索无果才保留原拒答（source=refusal）。
            if not web_tried:
                web_results, search_tier_used, search_query_used = _search_web_results(query)
                web_tried = True
            if web_results:
                composition, citations, citation_metadata = _build_web_citation_payload(query, web_results)
                source = "web_search"
                search_results = web_results
            else:
                source = "refusal"
                citations = []
                citation_metadata = {}
        self.sessions.append(session_id, query, str(composition["answer"]), citations)
        _append_dialog_log(
            session_id,
            "ai",
            str(composition["answer"]),
            {"source": source, "answerable": answered, "composer": composer_used},
        )
        return {
            "session_id": session_id,
            "query": query,
            "retrieval_query": retrieval_query,
            "answer": composition["answer"],
            "answerable": answered,
            "unknown": not answered,
            "answerability": retrieval["answerability"],
            "insufficient_reason": retrieval.get("insufficient_reason"),
            "citations": citations,
            "citation_metadata": citation_metadata,
            "visual_evidence": visual_evidence,
            "claims": composition.get("claims", []),
            "unresolved": composition.get("unresolved", []),
            "composer": composer_used,
            "composer_warning": warning,
            "evidence": context["evidence"] if answered else [],
            "conversation_turns": len(history) // 2 + 1,
            "source": source,
            "search_results": search_results,
            "search_tier_used": search_tier_used,
            "web_search_query": search_query_used,
        }

    def _ask_free_answer(
        self,
        session_id: str,
        query: str,
        retrieval_query: str,
        retrieval: Dict[str, object],
        context: Dict[str, object],
        history: List[Dict[str, object]],
        profile: Dict[str, str],
    ) -> Dict[str, object]:
        """v1.7 新生成链路：检索负责找、LLM 负责判（LLM_FREE_ANSWER=1）。

        流程：
          1. 检索空结果 → 硬触发联网（省一次 LLM）；搜索无果 → 软拒答。
          2. 有候选 → LLM 一次输出 answer + 软 judgment（answer/refuse/need_web）。
          3. judgment=need_web / web_search_needed → 博查 → 结果作为 W 证据喂回
             LLM 二次生成；二次生成失败回退确定性 web 组装。
          4. judgment=refuse → answerable=false、source=refusal（保留 citations，
             用于"材料不相关"的说明，不再强制清空）。
          5. 校验只保留 unknown_citation（防编造）；失败回退 ExtractiveComposer。
        judgment 是意图声明不是判决：缺失/解析失败用启发式兜底，绝不报错。
        """
        warning: Optional[str] = None
        composer_used = self.composer.name
        source = "corpus"
        web_tried = False
        web_results: Optional[List[Dict[str, object]]] = None
        search_tier_used: Optional[str] = None
        search_query_used: Optional[str] = None
        evidence = context.get("evidence", [])

        # ---- 检索空结果：硬触发联网（方案 6.2 第 3 条） ----
        if not evidence:
            web_results, search_tier_used, search_query_used = _search_web_results(query)
            web_tried = True
            if web_results:
                composition, citations, citation_metadata = _build_web_citation_payload(query, web_results)
                source = "web_search"
                self.sessions.append(session_id, query, str(composition["answer"]), citations)
                _append_dialog_log(
                    session_id, "ai", str(composition["answer"]),
                    {"source": "web_search", "answerable": False, "composer": composer_used},
                )
                return {
                    "session_id": session_id,
                    "query": query,
                    "retrieval_query": retrieval_query,
                    "answer": composition["answer"],
                    "answerable": False,
                    "unknown": True,
                    "answerability": retrieval["answerability"],
                    "insufficient_reason": retrieval.get("insufficient_reason"),
                    "citations": citations,
                    "citation_metadata": citation_metadata,
                    "visual_evidence": build_visual_evidence(retrieval),
                    "claims": [],
                    "unresolved": composition.get("unresolved", []),
                    "composer": composer_used,
                    "composer_warning": None,
                    "evidence": [],
                    "conversation_turns": len(history) // 2 + 1,
                    "source": "web_search",
                    "search_results": web_results,
                    "search_tier_used": search_tier_used,
                    "web_search_query": search_query_used,
                    "judgment": "need_web",
                }
            # 搜索无果 → 软拒答（引导问师哥师姐）
            composition = {
                "answer": (
                    f"知识库里暂时没有找到关于「{query}」的可靠材料，联网也没有搜到相关信息。"
                    "你可以① 点击下方「问师哥师姐」，让学长学姐给你权威解答；② 换个问法试试。"
                    "以学校最新通知为准。"
                ),
                "citations": [],
                "claims": [],
                "unresolved": ["本地与联网均未检索到有效材料"],
                "judgment": "refuse",
            }
            source = "refusal"
            citations = []
            citation_metadata = {}
            visual_evidence = build_visual_evidence(retrieval)
            self.sessions.append(session_id, query, str(composition["answer"]), citations)
            _append_dialog_log(
                session_id, "ai", str(composition["answer"]),
                {"source": "refusal", "answerable": False, "composer": composer_used},
            )
            return {
                "session_id": session_id,
                "query": query,
                "retrieval_query": retrieval_query,
                "answer": composition["answer"],
                "answerable": False,
                "unknown": True,
                "answerability": retrieval["answerability"],
                "insufficient_reason": retrieval.get("insufficient_reason"),
                "citations": citations,
                "citation_metadata": citation_metadata,
                "visual_evidence": visual_evidence,
                "claims": [],
                "unresolved": composition.get("unresolved", []),
                "composer": composer_used,
                "composer_warning": None,
                "evidence": [],
                "conversation_turns": len(history) // 2 + 1,
                "source": "refusal",
                "search_results": None,
                "search_tier_used": None,
                "web_search_query": None,
                "judgment": "refuse",
            }

        # ---- 有候选：强导航型证据（navigation_only）直接走抽取式导航回答 ----
        strong_navigation = bool(
            context.get("evidence_signal", False)
            and context.get("response_mode") == "supported_with_context"
            and bool(evidence)
            and evidence[0].get("assertion_policy") == "navigation_only"
        )
        try:
            if strong_navigation:
                nav_context = dict(context)
                nav_context["evidence"] = [
                    item for item in evidence
                    if item.get("assertion_policy") == "navigation_only"
                ]
                composition = self.fallback.generate(nav_context)
                composer_used = self.fallback.name
            else:
                composition = self.composer.generate(context)
            valid, errors = validate_composition(composition, context)
            if not valid:
                warning = "model_output_failed_grounding_validation:" + ",".join(errors)
                composition = self.fallback.generate(context)
                composer_used = self.fallback.name
            elif composition.get("_warnings"):
                warning = "composer_warning:" + ",".join(composition["_warnings"])
        except Exception:
            warning = "model_provider_unavailable_fallback_used"
            composition = self.fallback.generate(context)
            composer_used = self.fallback.name

        # ---- 软 judgment 解析（意图声明不是判决，缺失用启发式兜底） ----
        judgment = str(composition.get("judgment") or "").strip().lower()
        if judgment not in {"answer", "refuse", "need_web"}:
            answer_text = str(composition.get("answer", ""))
            if _looks_like_refusal(answer_text) or (not composition.get("citations") and not composition.get("claims")):
                judgment = "refuse"
            else:
                judgment = "answer"
        web_needed = bool(composition.get("web_search_needed")) or judgment == "need_web"
        # v1.7 实时信息兜底：LLM 判 refuse 但 query 命中实时词 → 强制尝试联网，
        # 避免"三亚天气"这类实时问题被拒答（prompt 已引导，此处兜底）。
        if not web_needed and judgment == "refuse" and any(term in query for term in REALTIME_TERMS):
            web_needed = True
        citations = [str(item) for item in composition.get("citations", [])]
        citation_metadata = build_citation_metadata(context)
        visual_evidence = build_visual_evidence(retrieval)

        # ---- web_search_needed：博查 → 结果作为 W 证据喂回 LLM 二次生成 ----
        if web_needed and not web_tried:
            web_results, search_tier_used, search_query_used = _search_web_results(query)
            web_tried = True
            if web_results:
                web_evidence = []
                for index, item in enumerate(web_results, start=1):
                    snippet = str(item.get("snippet") or item.get("title") or "")
                    web_evidence.append({
                        "evidence_id": f"W{index}",
                        "title": str(item.get("title") or "联网结果"),
                        "chunk_type": "web_search",
                        "authority_tier": "web_search",
                        "assertion_policy": None,
                        "page_number": None,
                        "heading_path": None,
                        "source_url": item.get("link"),
                        "file_path": None,
                        "media_type": "link",
                        "image_thumb": None,
                        "published_at": None,
                        "effective_from": None,
                        "uploaded_at": None,
                        "date_status": "unverified",
                        "student_level": None,
                        "uncertainty": ["联网搜索结果未经学校官方核实"],
                        "text": snippet,
                        "retrieval_origin": "web_search",
                        "web_source_kind": "web_search",
                        "fetched_at": None,
                        "evidence_coverage": None,
                        "score": None,
                        "concept_coverage": None,
                    })
                web_context = dict(context)
                web_context["evidence"] = web_evidence
                web_context["query"] = query
                web_context["response_mode"] = "web_supported"
                web_context["model_contract_version"] = "campus-grounding-v5-free-answer-web"
                source = "web_search"
                try:
                    web_composition = self.composer.generate(web_context)
                    valid, errors = validate_composition(web_composition, web_context)
                    if valid:
                        composition = web_composition
                        composer_used = self.composer.name
                        if web_composition.get("_warnings"):
                            warning = "composer_warning:" + ",".join(web_composition["_warnings"])
                    else:
                        warning = "model_output_failed_grounding_validation:" + ",".join(errors)
                        composition, citations, citation_metadata = _build_web_citation_payload(query, web_results)
                except Exception:
                    warning = "model_provider_unavailable_fallback_used"
                    composition, citations, citation_metadata = _build_web_citation_payload(query, web_results)
                citations = [str(item) for item in composition.get("citations", [])]
                citation_metadata = build_citation_metadata(web_context) if citations else citation_metadata
            else:
                # 联网失败（配额/网络/无结果）：必须回落拒答语义，不得标 answered=True。
                # 在 LLM 原文后追加联网失败说明，语义标记为 refusal + answerable=false。
                warning = "web_search_unavailable:" + (search_query_used or query)
                original = str(composition.get("answer", ""))
                composition["answer"] = (
                    original.rstrip() +
                    "\n\n（已尝试联网搜索最新信息，但暂时无法获取，以上仅为本地材料判断，仅供参考。）"
                )
                judgment = "refuse"
                source = "refusal"

        # ---- 语义决策：refuse → 拒答；web_search → 非官方仅供参考；其余 → 正常答 ----
        answered = True
        if source == "web_search":
            # 联网路径沿用现状语义：answerable=false（非官方，仅供参考）
            answered = False
            judgment = "need_web"
        elif judgment == "refuse":
            answered = False
            source = "refusal"

        self.sessions.append(session_id, query, str(composition["answer"]), citations)
        _append_dialog_log(
            session_id, "ai", str(composition["answer"]),
            {"source": source, "answerable": answered, "composer": composer_used, "judgment": judgment},
        )
        return {
            "session_id": session_id,
            "query": query,
            "retrieval_query": retrieval_query,
            "answer": composition["answer"],
            "answerable": answered,
            "unknown": not answered,
            "answerability": retrieval["answerability"],
            "insufficient_reason": retrieval.get("insufficient_reason"),
            "citations": citations,
            "citation_metadata": citation_metadata,
            "visual_evidence": visual_evidence,
            "claims": composition.get("claims", []),
            "unresolved": composition.get("unresolved", []),
            "composer": composer_used,
            "composer_warning": warning,
            "evidence": context["evidence"] if answered else [],
            "conversation_turns": len(history) // 2 + 1,
            "source": source,
            "search_results": web_results,
            "search_tier_used": search_tier_used,
            "web_search_query": search_query_used,
            "judgment": judgment,
        }

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)

    def status(self) -> Dict[str, object]:
        return dict(self.provider_status)
