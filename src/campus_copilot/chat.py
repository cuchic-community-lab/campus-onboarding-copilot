import re
import threading
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from .composition import (
    AnswerComposer,
    ExtractiveComposer,
    configured_composer,
    display_answer,
    validate_composition,
)
from .context_builder import build_context_packet
from .answer_planning import build_answer_plan
from .web_retrieval import NullWebRetriever, WebRetriever


FOLLOW_UP_MARKERS = ("那", "这个", "它", "还有", "上面", "刚才", "呢", "具体", "怎么办", "为什么")
CORRECTION_PATTERNS = (
    re.compile(r"(?:我说的是|我是说|指的是|应该是)\s*([^，。！？；]+)"),
    re.compile(r"不是[^，。！？；]+[，,；;]\s*(?:是|而是)\s*([^，。！？；]+)"),
)


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
        web_retriever: Optional[WebRetriever] = None,
    ):
        if composer is None:
            composer, detected_status = configured_composer()
            provider_status = provider_status or detected_status
        self.retriever = retriever
        self.composer = composer
        self.sessions = sessions or SessionStore()
        self.provider_status = provider_status or {"mode": "custom", "adapter": composer.name}
        self.fallback = ExtractiveComposer()
        self.web_retriever = web_retriever or NullWebRetriever()

    @staticmethod
    def _web_routes(fallback_route: str) -> List[str]:
        if fallback_route == "official_and_public_web_discovery":
            return ["official", "public"]
        if fallback_route == "official_web_discovery":
            return ["official"]
        if fallback_route == "public_web_discovery":
            return ["public"]
        return []

    def _enrich_with_web(self, query: str, retrieval: Dict[str, object], top_k: int) -> Dict[str, object]:
        plan = build_answer_plan(query, str(retrieval.get("answerability", "insufficient")))
        routes = self._web_routes(str(plan["fallback_route"]))
        if not routes:
            return retrieval
        web_search = self.web_retriever.search(query, routes, min(4, top_k))
        enriched = dict(retrieval)
        enriched["web_search"] = {key: value for key, value in web_search.items() if key != "results"}
        web_results = list(web_search.get("results", []))
        if not web_results:
            return enriched

        question_type = str(plan["question_type"])
        if question_type in {"arrival_preparation", "institution_structure"}:
            # These are explicitly web-governed intents. Weak local passages
            # must not become evidence merely because they share campus words.
            merged_results = web_results
        else:
            merged_results = web_results + list(retrieval.get("results", []))
        enriched["results"] = merged_results[:top_k]
        has_official = any(item.get("authority_tier") == "official_web" for item in web_results)
        has_public = any(item.get("authority_tier") == "public_web" for item in web_results)
        if has_official and has_public:
            enriched["answerability"] = "web_supported_mixed"
        elif has_official:
            enriched["answerability"] = "web_supported"
        else:
            enriched["answerability"] = "public_web_supported"
        enriched["retrieval_mode"] = str(retrieval.get("retrieval_mode", "local")) + "+curated_live_web"
        enriched["requires_uncertainty_label"] = any(item.get("uncertainty") for item in web_results)
        return enriched

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
        retrieval = self._enrich_with_web(retrieval_query, retrieval, top_k)
        retrieval["query"] = query
        context = build_context_packet(retrieval, history)

        warning: Optional[str] = None
        composer_used = self.composer.name
        if not context.get("can_generate"):
            composition = self.fallback.generate(context)
            composer_used = self.fallback.name
        else:
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
        cited = set(citations)
        sources = [
            item for item in context["evidence"]
            if str(item.get("evidence_id")) in cited
        ]
        return {
            "session_id": session_id,
            "query": query,
            "retrieval_query": retrieval_query,
            "answer": composition["answer"],
            "display_answer": display_answer(str(composition["answer"])),
            "citations": citations,
            "claims": composition.get("claims", []),
            "unresolved": composition.get("unresolved", []),
            "answerability": retrieval["answerability"],
            "composer": composer_used,
            "composer_warning": warning,
            "evidence": context["evidence"],
            "sources": sources,
            "answer_plan": context["answer_plan"],
            "conversation_turns": len(history) // 2 + 1,
        }

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)

    def status(self) -> Dict[str, object]:
        status = dict(self.provider_status)
        status["web_retrieval"] = self.web_retriever.status()
        return status
