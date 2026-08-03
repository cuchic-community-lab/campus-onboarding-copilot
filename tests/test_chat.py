import unittest

from campus_copilot.chat import GroundedChatService, SessionStore, contextualize_query
from campus_copilot.composition import ExtractiveComposer


class FakeRetriever:
    def __init__(self):
        self.queries = []

    def search(self, query, top_k, profile):
        self.queries.append(query)
        return {
            "query": query,
            "intent": "campus_life",
            "retrieval_mode": "fake",
            "answerability": "experience_only",
            "requires_uncertainty_label": True,
            "results": [{
                "title": "宿舍问答",
                "authority_tier": "peer_experience",
                "assertion_policy": "label_as_experience",
                "page_number": None,
                "heading_path": "",
                "source_url": "https://example.test/qa",
                "published_at": None,
                "effective_from": None,
                "uploaded_at": "2026-07-24",
                "date_status": "unknown",
                "student_level": profile.get("student_level", "all"),
                "uncertainty": ["大概率"],
                "text": "回答：目前大概率是四人寝两人住。",
                "score_explanation": {"concept_coverage": 1.0},
            }],
        }


class ChatTest(unittest.TestCase):
    def test_follow_up_uses_prior_user_question_for_retrieval(self):
        retriever = FakeRetriever()
        service = GroundedChatService(
            retriever,
            composer=ExtractiveComposer(),
            sessions=SessionStore(),
            provider_status={"mode": "fallback"},
        )
        first = service.ask("宿舍是几人间？", session_id="test", profile={"student_level": "undergraduate"})
        second = service.ask("那研究生呢？", session_id="test", profile={"student_level": "graduate"})
        self.assertEqual(first["conversation_turns"], 1)
        self.assertEqual(second["conversation_turns"], 2)
        self.assertIn("宿舍是几人间", second["retrieval_query"])
        self.assertIn("那研究生呢", retriever.queries[-1])

    def test_reset_removes_context(self):
        retriever = FakeRetriever()
        service = GroundedChatService(retriever, composer=ExtractiveComposer())
        service.ask("宿舍是几人间？", session_id="test")
        service.reset("test")
        result = service.ask("那研究生呢？", session_id="test")
        self.assertEqual(result["retrieval_query"], "那研究生呢？")

    def test_long_independent_question_is_not_merged(self):
        history = [{"role": "user", "content": "宿舍是几人间？"}]
        query = "本科生第一次选课需要完成哪些步骤以及注意哪些截止时间？"
        self.assertEqual(contextualize_query(query, history), query)

    def test_correction_turn_replaces_previous_topic_with_corrected_entity(self):
        history = [{"role": "user", "content": "学生有保留学籍的机会吗？"}]
        self.assertEqual(contextualize_query("我说的是保研", history), "保研")
        self.assertEqual(contextualize_query("不是保留学籍，是保研", history), "保研")

    def test_weak_follow_up_topic_match_is_refused(self):
        retriever = FakeRetriever()
        service = GroundedChatService(retriever, composer=ExtractiveComposer())
        service.ask("宿舍是几人间？", session_id="test")
        original_search = retriever.search

        def weak_search(query, top_k, profile):
            result = original_search(query, top_k, profile)
            result["results"][0]["score_explanation"]["concept_coverage"] = 0.4
            return result

        retriever.search = weak_search
        result = service.ask("那研究生呢？", session_id="test")
        self.assertEqual(result["answerability"], "insufficient_contextual_evidence")
        self.assertEqual(result["citations"], [])
        self.assertIn("问师哥师姐", result["answer"])


if __name__ == "__main__":
    unittest.main()
