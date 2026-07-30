import unittest

from campus_copilot.answer_planning import build_answer_plan, classify_question


class AnswerPlanningTest(unittest.TestCase):
    def test_historical_outcome_is_distinct_from_policy(self):
        self.assertEqual(classify_question("班上大概多少名可以保研？"), "historical_outcome")

    def test_current_rule_routes_to_official_discovery_when_local_evidence_is_missing(self):
        plan = build_answer_plan("今年推免申请什么时候截止？", "insufficient")
        self.assertEqual(plan["question_type"], "current_official")
        self.assertEqual(plan["fallback_route"], "official_web_discovery")
        self.assertFalse(plan["web_search_executed"])

    def test_general_career_question_routes_to_public_web_discovery(self):
        plan = build_answer_plan("智能科学与技术的就业方向有哪些？", "insufficient")
        self.assertEqual(plan["question_type"], "general_guidance")
        self.assertEqual(plan["fallback_route"], "public_web_discovery")

    def test_general_career_question_searches_even_with_local_candidate(self):
        plan = build_answer_plan("智能科学与技术的就业方向有哪些？", "experience_only")
        self.assertEqual(plan["fallback_route"], "public_web_discovery")
        self.assertTrue(plan["requires_web_enrichment"])

    def test_current_official_question_searches_even_with_local_candidate(self):
        plan = build_answer_plan("今年推免申请什么时候截止？", "supported")
        self.assertEqual(plan["fallback_route"], "official_web_discovery")
        self.assertTrue(plan["requires_web_enrichment"])

    def test_supported_question_stays_on_local_knowledge(self):
        plan = build_answer_plan("宿舍是几人间？", "experience_only")
        self.assertEqual(plan["fallback_route"], "local_knowledge")

    def test_arrival_preparation_always_requests_official_and_public_context(self):
        plan = build_answer_plan("开学报到要带些什么？", "experience_only")
        self.assertEqual(plan["question_type"], "arrival_preparation")
        self.assertEqual(plan["fallback_route"], "official_and_public_web_discovery")
        self.assertTrue(plan["requires_web_enrichment"])

    def test_organization_structure_uses_official_web(self):
        plan = build_answer_plan("中传的组织架构是什么？", "experience_only")
        self.assertEqual(plan["question_type"], "institution_structure")
        self.assertEqual(plan["fallback_route"], "official_web_discovery")

    def test_credential_wording_forces_official_and_public_web_enrichment(self):
        plan = build_answer_plan("我们的毕业证有中外合办字样吗？", "experience_only")
        self.assertEqual(plan["question_type"], "credential_wording")
        self.assertEqual(plan["fallback_route"], "official_and_public_web_discovery")
        self.assertTrue(plan["requires_web_enrichment"])

    def test_visual_communication_modality_forces_official_web_discovery(self):
        plan = build_answer_plan("视传只有中外合办有嘛", "experience_only")
        self.assertEqual(plan["question_type"], "program_offering")
        self.assertEqual(plan["fallback_route"], "official_web_discovery")
        self.assertTrue(plan["requires_web_enrichment"])


if __name__ == "__main__":
    unittest.main()
