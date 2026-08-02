import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from campus_copilot.chat import GroundedChatService
from campus_copilot.composition import ExtractiveComposer
from campus_copilot.tracing import SQLiteTraceStore, redact_text


class TraceRetriever:
    def search(self, query, top_k, profile):
        return {
            "query": query,
            "intent": "campus_life",
            "retrieval_mode": "fake_hybrid",
            "answerability": "experience_only",
            "requires_uncertainty_label": True,
            "results": [{
                "document_id": "dorm-faq",
                "chunk_id": "dorm-faq-1",
                "title": "宿舍问答",
                "authority_tier": "peer_experience",
                "assertion_policy": "label_as_experience",
                "page_number": None,
                "heading_path": "住宿",
                "source_url": "https://example.test/dorm",
                "published_at": None,
                "effective_from": None,
                "uploaded_at": "2026-07-24",
                "date_status": "unknown",
                "student_level": profile.get("student_level", "all"),
                "uncertainty": ["历届经验"],
                "text": "回答：目前大概率是四人寝两人住。",
                "score": 0.92,
                "score_explanation": {"concept_coverage": 1.0},
            }],
        }


class FailingRetriever:
    def search(self, query, top_k, profile):
        raise RuntimeError("database unavailable")


class TracingTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "traces.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_redacts_common_personal_identifiers(self):
        clean = redact_text("联系 13800138000 或 student@example.com，学号 202612345678。")
        self.assertNotIn("13800138000", clean)
        self.assertNotIn("student@example.com", clean)
        self.assertNotIn("202612345678", clean)
        self.assertIn("[REDACTED_PHONE]", clean)
        self.assertIn("[REDACTED_EMAIL]", clean)
        self.assertIn("[REDACTED_ID]", clean)

    def test_chat_trace_records_retrieval_model_validation_and_response(self):
        store = SQLiteTraceStore(self.database)
        service = GroundedChatService(
            TraceRetriever(),
            composer=ExtractiveComposer(),
            trace_store=store,
            provider_status={"provider": "local", "model": "extractive"},
        )

        response = service.ask(
            "我的邮箱 student@example.com，宿舍是几人间？",
            session_id="private-session-id",
        )
        trace = store.get(response["trace_id"])

        self.assertIsNotNone(trace)
        self.assertEqual(trace["trace_id"], response["trace_id"])
        self.assertNotIn("student@example.com", trace["query"]["original"])
        self.assertNotEqual(trace["anonymous_session_id"], "private-session-id")
        self.assertEqual(trace["retrieval"]["retrieval_mode"], "fake_hybrid")
        self.assertEqual(trace["retrieval"]["final_candidates"][0]["chunk_id"], "dorm-faq-1")
        self.assertEqual(trace["llm"]["composer_used"], "extractive_fallback")
        self.assertEqual(trace["llm"]["input"]["evidence_ids"], ["S1"])
        self.assertIsNone(trace["llm"]["attempted_response"])
        self.assertEqual(trace["validation"]["status"], "passed")
        self.assertEqual(trace["response"]["status"], "completed")
        self.assertGreaterEqual(trace["timings_ms"]["total"], 0)

    def test_failure_trace_records_stage_without_exception_message(self):
        store = SQLiteTraceStore(self.database)
        service = GroundedChatService(FailingRetriever(), trace_store=store)

        with self.assertRaises(RuntimeError):
            service.ask("宿舍问题", session_id="session")

        summary = store.list_recent(1)[0]
        trace = store.get(summary["trace_id"])
        self.assertEqual(summary["status"], "error")
        self.assertEqual(trace["error"]["stage"], "local_retrieval")
        self.assertEqual(trace["error"]["exception_class"], "RuntimeError")
        self.assertNotIn("database unavailable", json.dumps(trace, ensure_ascii=False))

    def test_retention_removes_expired_traces(self):
        store = SQLiteTraceStore(self.database, retention_days=30)
        expired = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        store.record({
            "trace_id": "trace_expired",
            "created_at": expired,
            "query": {"original": "old"},
            "retrieval": {},
            "llm": {},
            "response": {"status": "completed"},
            "timings_ms": {},
        })
        store.record({
            "trace_id": "trace_current",
            "query": {"original": "new"},
            "retrieval": {},
            "llm": {},
            "response": {"status": "completed"},
            "timings_ms": {},
        })

        self.assertIsNone(store.get("trace_expired"))
        self.assertIsNotNone(store.get("trace_current"))

    def test_sensitive_keys_are_never_persisted(self):
        store = SQLiteTraceStore(self.database)
        store.record({
            "trace_id": "trace_secret",
            "query": {"original": "hello"},
            "retrieval": {},
            "llm": {"api_key": "should-not-survive", "provider": "test"},
            "response": {"status": "completed"},
            "timings_ms": {},
        })
        with sqlite3.connect(self.database) as connection:
            payload = connection.execute(
                "SELECT payload_json FROM rag_traces WHERE trace_id = 'trace_secret'"
            ).fetchone()[0]
        self.assertNotIn("should-not-survive", payload)
        self.assertIn("[REDACTED_SECRET]", payload)


if __name__ == "__main__":
    unittest.main()
