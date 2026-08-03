import re
import threading
import time
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
from .evidence_coverage import rank_and_filter_evidence
from .web_retrieval import NullWebRetriever, WebRetriever
from .tracing import TraceStore, configured_trace_store, trace_candidate


FOLLOW_UP_MARKERS = ("那", "这个", "它", "还有", "上面", "刚才", "呢", "具体", "怎么办", "为什么")
CORRECTION_PATTERNS = (
    re.compile(r"(?:我说的是|我是说|指的是|应该是)\s*([^，。！？；]+)"),
    re.compile(r"不是[^，。！？；]+[，,；;]\s*(?:是|而是)\s*([^，。！？；]+)"),
)

UNANSWERED_HANDOFF_MESSAGE = (
    "抱歉啊，能力暂时回答不了，不过你可以留下你的邮箱，你这个问题将会有一位活的师哥/师姐回答，"
    "或者你可以加一下我的制造者问一下，顺便骂一下他做的什么狗屎AI，他的微信是：RellFu。"
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
        trace_store: Optional[TraceStore] = None,
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
        self.trace_store = trace_store or configured_trace_store()

    def _record_trace(self, trace: Dict[str, object]) -> None:
        try:
            self.trace_store.record(trace)
        except Exception:
            # Observability must never break a student-facing answer.
            return

    @staticmethod
    def _web_routes(fallback_route: str) -> List[str]:
        if fallback_route == "official_and_public_web_discovery":
            return ["official", "public"]
        if fallback_route == "official_web_discovery":
            return ["official"]
        if fallback_route == "public_web_discovery":
            return ["public"]
        return []

    @staticmethod
    def _human_handoff(question_type: str) -> str:
        if question_type in {"current_official", "credential_wording", "program_offering", "institution_structure"}:
            return "这类学校政策、专业或证书问题如果仍有疑问，建议向学院教务老师或招生办公室确认。"
        if question_type == "campus_experience":
            return "这类校园生活安排可能会变化，建议再问问本届师哥师姐确认实际情况。"
        if question_type == "general_guidance":
            return "这类发展方向没有唯一答案，也可以结合师哥师姐的去向，或咨询专业老师和就业指导老师。"
        return "如果这个问题会影响你的实际安排，建议再向师哥师姐或负责老师确认。"

    def _enrich_with_web(self, query: str, retrieval: Dict[str, object], top_k: int) -> Dict[str, object]:
        plan = build_answer_plan(query, str(retrieval.get("answerability", "insufficient")))
        routes = self._web_routes(str(plan["fallback_route"]))
        if not routes:
            return retrieval
        web_search = self.web_retriever.search(query, routes, min(4, top_k))
        enriched = dict(retrieval)
        enriched["local_answerability_before_web"] = str(retrieval.get("answerability", "insufficient"))
        enriched["local_results_before_web"] = len(retrieval.get("results", []))
        enriched["web_search"] = {key: value for key, value in web_search.items() if key != "results"}
        web_results = list(web_search.get("results", []))
        question_type = str(plan["question_type"])
        if not web_results and question_type not in {"credential_wording", "program_offering"}:
            return enriched

        if question_type in {"arrival_preparation", "institution_structure", "general_guidance"}:
            # These are explicitly web-governed intents. Weak local passages
            # must not become evidence merely because they share campus words.
            merged_results = web_results
        else:
            merged_results = rank_and_filter_evidence(
                query,
                question_type,
                web_results + list(retrieval.get("results", [])),
            )
        enriched["results"] = merged_results[:top_k]
        has_official = any(
            item.get("authority_tier") in {"official_web", "official_policy", "official_guidance"}
            for item in merged_results
        )
        has_public = any(item.get("authority_tier") == "public_web" for item in merged_results)
        has_direct_answer = any(
            bool(item.get("evidence_coverage", {}).get("direct_answer"))
            for item in merged_results
        )
        if question_type in {"credential_wording", "program_offering"} and not merged_results:
            enriched["answerability"] = "insufficient_question_coverage"
            enriched["insufficient_reason"] = "evidence_does_not_cover_question_object"
        elif question_type == "program_offering" and not has_direct_answer:
            enriched["answerability"] = "supported_with_unresolved_exclusivity"
            enriched["insufficient_reason"] = "no_scoped_official_source_proves_program_exclusivity"
        elif question_type == "credential_wording" and not has_direct_answer:
            enriched["answerability"] = "supported_with_unresolved_wording"
            enriched["insufficient_reason"] = "no_source_directly_confirms_certificate_wording"
        elif has_official and has_public:
            enriched["answerability"] = "web_supported_mixed"
        elif has_official:
            enriched["answerability"] = "web_supported"
        elif has_public:
            enriched["answerability"] = "public_web_supported"
        else:
            enriched["answerability"] = str(retrieval.get("answerability", "insufficient"))
        enriched["retrieval_mode"] = str(retrieval.get("retrieval_mode", "local")) + "+curated_live_web"
        enriched["requires_uncertainty_label"] = any(item.get("uncertainty") for item in web_results)
        return enriched

    def ask(
        self,
        query: str,
        session_id: Optional[str] = None,
        profile: Optional[Dict[str, str]] = None,
        top_k: int = 6,
        trace_source: str = "application",
    ) -> Dict[str, object]:
        trace_id = "trace_" + uuid.uuid4().hex
        started = time.perf_counter()
        timings: Dict[str, int] = {}
        stage = "initialize"
        session_id = session_id or uuid.uuid4().hex
        retrieval_query = query
        effective_profile = dict(profile or {})
        local_candidates: List[Dict[str, object]] = []
        retrieval: Dict[str, object] = {}
        context: Dict[str, object] = {}
        composer_used = self.composer.name
        warning: Optional[str] = None
        validation_status = "not_run"
        validation_errors: List[str] = []
        model_attempted = False
        model_response: Optional[Dict[str, object]] = None
        try:
            history = self.sessions.history(session_id)
            retrieval_query = contextualize_query(query, history)
            if "研究生" in query:
                effective_profile["student_level"] = "graduate"
            elif "本科" in query:
                effective_profile["student_level"] = "undergraduate"

            stage = "local_retrieval"
            phase_started = time.perf_counter()
            retrieval = self.retriever.search(retrieval_query, top_k, effective_profile)
            timings[stage] = round((time.perf_counter() - phase_started) * 1000)
            local_candidates = list(retrieval.get("results", []))
            if retrieval_query != query and retrieval.get("results"):
                top_explanation = retrieval["results"][0].get("score_explanation", {})
                coverage = top_explanation.get("concept_coverage")
                if coverage is not None and float(coverage) < 0.6:
                    retrieval["answerability"] = "insufficient_contextual_evidence"
                    retrieval["insufficient_reason"] = "top_evidence_does_not_cover_enough_of_the_prior_topic"

            stage = "web_enrichment"
            phase_started = time.perf_counter()
            retrieval = self._enrich_with_web(retrieval_query, retrieval, top_k)
            timings[stage] = round((time.perf_counter() - phase_started) * 1000)
            retrieval["query"] = query

            stage = "context_build"
            phase_started = time.perf_counter()
            context = build_context_packet(retrieval, history)
            timings[stage] = round((time.perf_counter() - phase_started) * 1000)

            stage = "model_generation"
            phase_started = time.perf_counter()
            if not context.get("can_generate"):
                composition = self.fallback.generate(context)
                composer_used = self.fallback.name
                validation_status = "skipped_insufficient_evidence"
            else:
                try:
                    model_attempted = self.composer.name != self.fallback.name
                    composition = self.composer.generate(context)
                    if model_attempted:
                        model_response = {
                            "answer": composition.get("answer"),
                            "citations": composition.get("citations", []),
                            "unresolved": composition.get("unresolved", []),
                        }
                    valid, validation_errors = validate_composition(composition, context)
                    validation_status = "passed" if valid else "failed"
                    if not valid:
                        warning = "model_output_failed_grounding_validation:" + ",".join(validation_errors)
                        composition = self.fallback.generate(context)
                        composer_used = self.fallback.name
                except Exception as exc:
                    validation_status = "provider_error"
                    validation_errors = [exc.__class__.__name__]
                    warning = "model_provider_unavailable_fallback_used"
                    composition = self.fallback.generate(context)
                    composer_used = self.fallback.name
            timings[stage] = round((time.perf_counter() - phase_started) * 1000)

            stage = "postprocessing"
            phase_started = time.perf_counter()
            citations = [str(item) for item in composition.get("citations", [])]
            self.sessions.append(session_id, query, str(composition["answer"]), citations)
            cited = set(citations)
            sources = [
                item for item in context["evidence"]
                if str(item.get("evidence_id")) in cited
            ]
            final_answerability = str(retrieval.get("answerability", ""))
            core_question_unresolved = (
                final_answerability.startswith("insufficient")
                or final_answerability in {
                    "unverified",
                    "supported_with_unresolved_wording",
                    "supported_with_unresolved_exclusivity",
                }
            )
            answer_useful = bool(
                sources and composition.get("claims") and not core_question_unresolved
            )
            answer_plan = context["answer_plan"]
            source_origins = {str(item.get("retrieval_origin", "local_knowledge")) for item in sources}
            autonomous_used = "autonomous_search" in source_origins
            registered_web_used = bool(source_origins.intersection({"live_web", "verified_web_snapshot"}))
            local_status = str(retrieval.get("local_answerability_before_web", retrieval.get("answerability", "")))
            local_source_used = "local_knowledge" in source_origins
            local_insufficient = (
                local_status.startswith("insufficient")
                or local_status == "unverified"
                or ((autonomous_used or registered_web_used) and not local_source_used)
            )
            discovery_executed = bool(answer_plan.get("web_discovery_executed"))
            if autonomous_used:
                provenance_notice = "我在知识库里没有找到足以回答这个问题的内容，下面的回答来自本轮联网搜索。"
                provenance_mode = "autonomous_web_fallback"
            elif registered_web_used and local_insufficient:
                provenance_notice = "我在知识库里没有找到足够材料，下面参考的是已登记并核验过的网页来源，不是本轮自主搜索。"
                provenance_mode = "registered_web_fallback"
            elif discovery_executed and not autonomous_used:
                provenance_notice = "我在知识库和本轮联网搜索中都没有找到足够可靠的内容，因此没有根据相似材料下结论。"
                provenance_mode = "web_search_insufficient"
            elif local_insufficient:
                provenance_notice = "我在知识库里没有找到足够材料，而且当前自主网页搜索尚未启用。"
                provenance_mode = "search_unavailable"
            else:
                provenance_notice = "这次回答主要依据当前知识库中的材料。"
                provenance_mode = "local_knowledge"
            needs_handoff = not answer_useful or provenance_mode in {
                "web_search_insufficient", "search_unavailable", "registered_web_fallback",
            } or bool(composition.get("unresolved"))
            human_handoff = self._human_handoff(str(answer_plan.get("question_type", "campus_fact"))) if needs_handoff else ""
            handoff_available = not answer_useful and self.trace_store.enabled
            student_answer = display_answer(str(composition["answer"]))
            if not answer_useful:
                student_answer = UNANSWERED_HANDOFF_MESSAGE
            response = {
                "trace_id": trace_id,
                "session_id": session_id,
                "query": query,
                "retrieval_query": retrieval_query,
                "answer": composition["answer"],
                "display_answer": student_answer,
                "citations": citations,
                "claims": composition.get("claims", []),
                "unresolved": composition.get("unresolved", []),
                "answerability": retrieval["answerability"],
                "composer": composer_used,
                "composer_warning": warning,
                "evidence": context["evidence"],
                "sources": sources,
                "answer_plan": answer_plan,
                "provenance": {
                    "mode": provenance_mode,
                    "notice": provenance_notice,
                    "knowledge_base_sufficient": not local_insufficient,
                    "autonomous_search_executed": discovery_executed,
                    "autonomous_search_used": autonomous_used,
                },
                "human_handoff": human_handoff,
                "handoff_available": handoff_available,
                "answer_useful": answer_useful,
                "conversation_turns": len(history) // 2 + 1,
            }
            timings[stage] = round((time.perf_counter() - phase_started) * 1000)
            timings["total"] = round((time.perf_counter() - started) * 1000)

            final_candidates = list(retrieval.get("results", []))
            selected_ids = set(citations)
            selected_evidence = [
                {"evidence_id": item.get("evidence_id"), "title": item.get("title"),
                 "authority_tier": item.get("authority_tier"),
                 "retrieval_origin": item.get("retrieval_origin", "local_knowledge")}
                for item in sources
            ]
            rejected_evidence = [
                {"evidence_id": f"S{index}", "title": item.get("title"),
                 "reason": "not_cited_in_final_answer"}
                for index, item in enumerate(final_candidates, start=1)
                if f"S{index}" not in selected_ids
            ]
            final_keys = {(item.get("chunk_id"), item.get("title")) for item in final_candidates}
            rejected_evidence.extend(
                {"chunk_id": item.get("chunk_id"), "title": item.get("title"),
                 "reason": "replaced_or_filtered_during_web_arbitration"}
                for item in local_candidates
                if (item.get("chunk_id"), item.get("title")) not in final_keys
            )
            self._record_trace({
                "trace_id": trace_id,
                "source": trace_source,
                "session_id": session_id,
                "query": {
                    "original": query,
                    "retrieval_query": retrieval_query,
                    "question_type": answer_plan.get("question_type"),
                    "conversation_turn": len(history) // 2 + 1,
                    "profile": effective_profile,
                },
                "retrieval": {
                    "retrieval_mode": retrieval.get("retrieval_mode"),
                    "local_answerability": retrieval.get("local_answerability_before_web", retrieval.get("answerability")),
                    "answerability": retrieval.get("answerability"),
                    "insufficient_reason": retrieval.get("insufficient_reason"),
                    "local_candidates": [trace_candidate(item, index) for index, item in enumerate(local_candidates, 1)],
                    "final_candidates": [trace_candidate(item, index) for index, item in enumerate(final_candidates, 1)],
                    "web_search": retrieval.get("web_search") or {"executed": False},
                    "selected_evidence": selected_evidence,
                    "rejected_evidence": rejected_evidence,
                },
                "llm": {
                    "provider": self.provider_status.get("provider"),
                    "model": self.provider_status.get("model"),
                    "adapter": self.provider_status.get("adapter"),
                    "model_attempted": model_attempted,
                    "composer_requested": self.composer.name,
                    "composer_used": composer_used,
                    "fallback_used": composer_used == self.fallback.name,
                    "warning": warning,
                    "contract_version": context.get("model_contract_version"),
                    "input": {
                        "question_type": answer_plan.get("question_type"),
                        "evidence_ids": [item.get("evidence_id") for item in context.get("evidence", [])],
                        "history_messages": len(history),
                    },
                    "attempted_response": model_response,
                },
                "validation": {
                    "status": validation_status,
                    "errors": validation_errors,
                    "can_generate": bool(context.get("can_generate")),
                },
                "response": {
                    "status": "completed",
                    "answer": response["display_answer"],
                    "citations": citations,
                    "unresolved": response["unresolved"],
                    "provenance": response["provenance"],
                    "human_handoff": human_handoff,
                    "handoff_available": handoff_available,
                    "answer_useful": answer_useful,
                },
                "timings_ms": timings,
                "error": None,
            })
            return response
        except Exception as exc:
            timings["total"] = round((time.perf_counter() - started) * 1000)
            self._record_trace({
                "trace_id": trace_id,
                "source": trace_source,
                "session_id": session_id,
                "query": {
                    "original": query,
                    "retrieval_query": retrieval_query,
                    "question_type": (context.get("answer_plan") or {}).get("question_type"),
                    "profile": effective_profile,
                },
                "retrieval": {
                    "answerability": retrieval.get("answerability"),
                    "local_candidates": [trace_candidate(item, index) for index, item in enumerate(local_candidates, 1)],
                    "web_search": retrieval.get("web_search") or {"executed": False},
                },
                "llm": {
                    "provider": self.provider_status.get("provider"),
                    "model": self.provider_status.get("model"),
                    "composer_requested": self.composer.name,
                    "composer_used": composer_used,
                    "model_attempted": model_attempted,
                },
                "validation": {"status": validation_status, "errors": validation_errors},
                "response": {"status": "failed"},
                "timings_ms": timings,
                "error": {"stage": stage, "exception_class": exc.__class__.__name__},
            })
            raise

    def reset(self, session_id: str) -> None:
        self.sessions.reset(session_id)

    def status(self) -> Dict[str, object]:
        status = dict(self.provider_status)
        status["web_retrieval"] = self.web_retriever.status()
        status["tracing"] = {"enabled": self.trace_store.enabled}
        return status
