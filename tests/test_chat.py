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


def web_result(source_id, authority, text):
    return {
        "document_id": source_id,
        "chunk_id": "web-" + source_id,
        "title": source_id,
        "text": text,
        "chunk_type": "web_excerpt",
        "heading_path": "实时检索",
        "page_number": None,
        "tags": ["live_web"],
        "authority_tier": authority,
        "assertion_policy": "assert_with_live_citation" if authority == "official_web" else "cite_as_public_context",
        "source_url": "https://example.test/" + source_id,
        "cohort": None,
        "academic_year": None,
        "student_level": "all",
        "major": None,
        "campus": "Hainan",
        "uploaded_at": None,
        "published_at": "2022-08-09" if authority == "official_web" else None,
        "effective_from": None,
        "date_status": "historical_reference" if authority == "official_web" else "climate_background",
        "uncertainty": [],
        "retrieval_origin": "live_web",
        "web_source_kind": "official" if authority == "official_web" else "public",
        "fetched_at": "2026-07-30T15:00:00+0800",
        "score": 1.0,
        "score_explanation": {"registry_match": 1.0},
    }


class FakeWebRetriever:
    name = "fake_web"

    def search(self, query, routes, top_k=4):
        results = []
        if "official" in routes:
            results.append(web_result("arrival-guide", "official_web", "报到时须带录取通知书、密封档案和照片。"))
        if "public" in routes:
            results.append(web_result("lingshui-climate", "public_web", "陵水年平均气温25℃，5至10月为雨季。"))
        return {"executed": True, "provider": self.name, "status": "success", "routes": list(routes), "results": results, "errors": []}

    def status(self):
        return {"mode": "live", "provider": self.name}


class CredentialWebRetriever(FakeWebRetriever):
    def search(self, query, routes, top_k=4):
        results = [
            web_result(
                "credential-faq",
                "public_web",
                "中外合作办学专业毕业证、学位证和其他专业一样吗？答：没有不同，与其他专业完全一致。",
            ),
            web_result(
                "credential-official",
                "official_web",
                "中外合作办学专业达到条件后授予中国传媒大学毕业证书和学士学位。",
            ),
        ]
        return {
            "executed": True,
            "provider": self.name,
            "status": "success",
            "routes": list(routes),
            "results": results,
            "errors": [],
        }


class ChatTest(unittest.TestCase):
    def test_insufficient_evidence_bypasses_model_composer(self):
        retriever = FakeRetriever()

        def insufficient_search(query, top_k, profile):
            result = FakeRetriever().search(query, top_k, profile)
            result["answerability"] = "insufficient"
            return result

        retriever.search = insufficient_search

        class MustNotRunComposer:
            name = "must_not_run"

            def generate(self, context):
                raise AssertionError("model must not run without answerable evidence")

        result = GroundedChatService(retriever, composer=MustNotRunComposer()).ask(
            "智能科学与技术的就业方向有哪些？"
        )
        self.assertEqual(result["composer"], "extractive_fallback")
        self.assertEqual(result["answer_plan"]["fallback_route"], "public_web_discovery")
        self.assertEqual(result["citations"], [])

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
        self.assertNotIn("[S1]", first["display_answer"])
        self.assertEqual([item["evidence_id"] for item in first["sources"]], ["S1"])

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
        self.assertIn("不能把其他相似内容当成答案", result["answer"])

    def test_arrival_question_replaces_irrelevant_local_passage_with_governed_web_sources(self):
        service = GroundedChatService(
            FakeRetriever(), composer=ExtractiveComposer(), web_retriever=FakeWebRetriever()
        )
        result = service.ask("开学报到要带些什么？")
        self.assertEqual(result["answerability"], "web_supported_mixed")
        self.assertTrue(result["answer_plan"]["web_search_executed"])
        self.assertEqual(result["answer_plan"]["web_search_provider"], "fake_web")
        self.assertEqual([item["authority_tier"] for item in result["evidence"]], ["official_web", "public_web"])
        self.assertNotIn("宿舍问答", [item["title"] for item in result["evidence"]])
        self.assertIn("录取通知书", result["answer"])
        self.assertIn("年平均气温约25℃", result["answer"])
        self.assertIn("不是学校强制清单", result["answer"])

    def test_organization_question_uses_only_official_web_result(self):
        service = GroundedChatService(
            FakeRetriever(), composer=ExtractiveComposer(), web_retriever=FakeWebRetriever()
        )
        result = service.ask("中传的组织架构是什么？")
        self.assertEqual(result["answerability"], "web_supported")
        self.assertEqual([item["authority_tier"] for item in result["evidence"]], ["official_web"])

    def test_credential_question_discards_campus_culture_candidate_and_uses_web(self):
        retriever = FakeRetriever()

        def wrong_local_search(query, top_k, profile):
            result = FakeRetriever().search(query, top_k, profile)
            result["results"][0].update({
                "title": "中外合办校园文化问答",
                "text": "这里有没有英国校园文化？回答：没有。",
                "score": 99.0,
            })
            return result

        retriever.search = wrong_local_search
        service = GroundedChatService(
            retriever,
            composer=ExtractiveComposer(),
            web_retriever=CredentialWebRetriever(),
        )
        result = service.ask("我们的毕业证有中外合办字样吗？")
        self.assertTrue(result["answer_plan"]["web_search_executed"])
        self.assertNotIn("中外合办校园文化问答", [item["title"] for item in result["evidence"]])
        self.assertIn("没有不同", result["answer"])
        self.assertIn("不是证书样张", result["answer"])
        self.assertIn("授予中国传媒大学毕业证书", result["answer"])

    def test_credential_question_with_only_indirect_official_source_preserves_boundary(self):
        class OfficialOnly(CredentialWebRetriever):
            def search(self, query, routes, top_k=4):
                result = super().search(query, routes, top_k)
                result["results"] = [result["results"][1]]
                return result

        service = GroundedChatService(
            FakeRetriever(), composer=ExtractiveComposer(), web_retriever=OfficialOnly()
        )
        result = service.ask("我们的毕业证有中外合办字样吗？")
        self.assertEqual(result["answerability"], "supported_with_unresolved_wording")
        self.assertIn("不能仅凭", result["answer"])
        self.assertIn("单独不能证明", result["answer"])


if __name__ == "__main__":
    unittest.main()
