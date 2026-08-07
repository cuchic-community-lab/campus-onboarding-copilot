import tempfile
import unittest
from pathlib import Path

from campus_copilot.handoffs import SQLiteHandoffStore
from campus_copilot.tracing import SQLiteTraceStore


class HandoffStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "runtime.db"
        self.traces = SQLiteTraceStore(self.database)
        self.store = SQLiteHandoffStore(self.database)
        self.trace_id = "trace_" + "a" * 32
        self.traces.record({
            "trace_id": self.trace_id,
            "query": {"original": "学校有无障碍宿舍吗？"},
            "retrieval": {"answerability": "insufficient"},
            "llm": {},
            "response": {"status": "completed"},
            "timings_ms": {},
        })

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_opt_in_email_and_trace_are_saved_for_operator_follow_up(self):
        result = self.store.submit(
            " Student@Example.com ",
            "我的手机号 13800138000，学校有无障碍宿舍吗？",
            self.trace_id,
        )
        handoff = self.store.get(result["handoff_id"])
        self.assertEqual(handoff["email"], "student@example.com")
        self.assertEqual(handoff["trace_id"], self.trace_id)
        self.assertEqual(handoff["status"], "pending")
        self.assertNotIn("13800138000", handoff["query"])
        self.assertEqual(self.store.list_recent(1)[0]["handoff_id"], result["handoff_id"])

    def test_rejects_invalid_email_or_unknown_trace(self):
        with self.assertRaisesRegex(ValueError, "invalid_email"):
            self.store.submit("not-an-email", "问题", self.trace_id)
        with self.assertRaisesRegex(ValueError, "trace_not_found"):
            self.store.submit("student@example.com", "问题", "trace_" + "b" * 32)


if __name__ == "__main__":
    unittest.main()
