import re
import threading
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from .composition import AnswerComposer, ExtractiveComposer, _looks_like_refusal, configured_composer, validate_composition
from .context_builder import build_citation_metadata, build_context_packet, build_visual_evidence
from .search import search_web


FOLLOW_UP_MARKERS = ("那", "这个", "它", "还有", "上面", "刚才", "呢", "具体", "怎么办", "为什么")
CORRECTION_PATTERNS = (
    re.compile(r"(?:我说的是|我是说|指的是|应该是)\s*([^，。！？；]+)"),
    re.compile(r"不是[^，。！？；]+[，,；;]\s*(?:是|而是)\s*([^，。！？；]+)"),
)


def _compose_web_answer(query: str, results: List[Dict[str, object]]) -> Dict[str, object]:
    """Assemble a web-search answer deterministically (no second LLM call).

    The internet results are quoted as-is with W1..Wn citations and marked as
    non-official, so the user can still ask 师哥师姐 for an on-campus answer.
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
    lines.append("以上结果来自互联网搜索，并非学校官方信息，仅供参考。你可以点击下方「问师哥师姐」，让学长学姐给你更贴近校园的解答；也可以换个问法试试。以学校最新通知为准。")
    return {
        "answer": "\n".join(lines),
        "citations": [f"W{index}" for index in range(1, len(results) + 1)],
        "claims": [],
        "unresolved": ["联网搜索结果未经学校官方核实，仅供参考"],
    }


class SessionStore:
    def __init__(self, max_turns: int = 4, max_sessions: int = 500):
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._sessions: "OrderedDict[str, List[Dict[str, object]]]" = OrderedDict()
        self._lock = threading.Lock()

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

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


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
        history = self.sessions.history(session_id)
        retrieval_query = contextualize_query(query, history)
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

        warning: Optional[str] = None
        composer_used = self.composer.name
        source = "corpus"
        search_results: Optional[List[Dict[str, object]]] = None

        # v1.3：未知问题（语料判定不可生成）优先尝试联网搜索（可选，需
        # SEARCH_API_KEY）。搜索成功则返回联网回答（source=web_search），不硬性
        # 拒答；未配置或失败则走下方 LLM 合规拒答 / 软拒答（source=refusal）。
        if not can_generate:
            web_results = search_web(query)
            if web_results:
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
                self.sessions.append(session_id, query, str(composition["answer"]), citations)
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
                }

        try:
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
        self.sessions.append(session_id, query, str(composition["answer"]), citations)
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
            source = "refusal"
            citations = []
            citation_metadata = {}
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
        }

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)

    def status(self) -> Dict[str, object]:
        return dict(self.provider_status)
