import unittest
from unittest.mock import patch

from campus_copilot.composition import (
    ExtractiveComposer,
    OpenAICompatibleComposer,
    ProviderConfig,
    _normalize_model_response,
    configured_composer,
    display_answer,
    validate_composition,
)


def packet(authority="peer_experience", can_generate=True):
    return {
        "query": "宿舍怎么样？",
        "can_generate": can_generate,
        "response_mode": "experience_only" if authority == "peer_experience" else "supported",
        "evidence": [{
            "evidence_id": "S1",
            "title": "宿舍问答",
            "authority_tier": authority,
            "assertion_policy": "label_as_experience" if authority == "peer_experience" else "cite_as_policy",
            "page_number": None,
            "source_url": "https://example.test/source",
            "effective_from": None,
            "date_status": "unknown",
            "text": "回答：目前大概率是四人寝两人住。",
        }],
    }


class CompositionTest(unittest.TestCase):
    def test_model_string_unresolved_is_normalized_without_touching_claims(self):
        value = {
            "answer": "学生经验提到四人间。[S1]",
            "citations": ["S1"],
            "claims": [{"text": "四人间", "evidence_ids": ["S1"], "certainty": "experience"}],
            "unresolved": "研究生是否适用仍未知",
        }
        normalized = _normalize_model_response(value)
        self.assertEqual(normalized["unresolved"], ["研究生是否适用仍未知"])
        self.assertEqual(normalized["claims"], value["claims"])

    def test_bracketed_structured_evidence_ids_are_normalized(self):
        value = {
            "answer": "往届85人中有14人保研。",
            "citations": ["[S1]"],
            "claims": [{"text": "14人保研", "evidence_ids": ["[S1]"], "certainty": "experience"}],
            "unresolved": [],
        }
        normalized = _normalize_model_response(value)
        self.assertEqual(normalized["citations"], ["S1"])
        self.assertEqual(normalized["claims"][0]["evidence_ids"], ["S1"])

    def test_makers_preset_selects_gateway_and_default_model(self):
        with patch.dict(
            "os.environ",
            {"CAMPUS_LLM_PROVIDER": "makers", "CAMPUS_LLM_API_KEY": "test-key"},
            clear=True,
        ):
            config = ProviderConfig.from_env()
            composer, status = configured_composer()
        self.assertEqual(config.base_url, "https://ai-gateway.edgeone.link/v1")
        self.assertEqual(config.model, "@makers/deepseek-v4-flash")
        self.assertTrue(config.enabled)
        self.assertIsInstance(composer, OpenAICompatibleComposer)
        self.assertEqual(status["provider"], "makers")
        self.assertTrue(status["credential_configured"])

    def test_makers_without_key_stays_on_safe_fallback(self):
        with patch.dict("os.environ", {"CAMPUS_LLM_PROVIDER": "makers"}, clear=True):
            composer, status = configured_composer()
        self.assertIsInstance(composer, ExtractiveComposer)
        self.assertEqual(status["reason"], "missing_api_key")
        self.assertFalse(status["credential_configured"])

    def test_extracts_peer_answer_with_label_and_citation(self):
        context = packet()
        result = ExtractiveComposer().generate(context)
        valid, errors = validate_composition(result, context)
        self.assertTrue(valid, errors)
        self.assertIn("根据学生整理的往届信息", result["answer"])
        self.assertNotIn("《宿舍问答》提到", result["answer"])
        self.assertIn("[S1]", result["answer"])
        self.assertEqual(result["claims"][0]["certainty"], "experience")

    def test_display_answer_moves_audit_citation_out_of_prose(self):
        self.assertEqual(display_answer("有的。往届有14人保研。[S1]"), "有的。往届有14人保研。")

    def test_validator_rejects_document_reader_opening(self):
        context = packet(authority="official_policy")
        invalid = {
            "answer": "现有学校材料《中国传媒大学办法》提到：可以申请。[S1]",
            "citations": ["S1"],
            "claims": [{"text": "可以申请", "evidence_ids": ["S1"], "certainty": "official"}],
            "unresolved": [],
        }
        valid, errors = validate_composition(invalid, context)
        self.assertFalse(valid)
        self.assertIn("answer_starts_with_document_frame", errors)

    def test_historical_outcome_is_not_called_a_fixed_quota(self):
        context = packet()
        context["query"] = "班上大概排到多少名可以保研？"
        context["answer_plan"] = {"question_type": "historical_outcome"}
        context["evidence"][0]["text"] = "回答：22级85人中14人保研，比例约17%。"
        result = ExtractiveComposer().generate(context)
        self.assertIn("85人中有14人保研，约占16.5%", result["answer"])
        self.assertNotIn("保研固定比例17%", result["answer"])
        self.assertIn("不代表每一届都有固定比例或固定名额", result["answer"])
        self.assertIn("不能据此判断排到第几名就一定可以", result["answer"])

    def test_validator_rejects_rank_inferred_from_historical_ratio(self):
        context = packet()
        context["query"] = "班上大概排到多少名可以保研？"
        context["answer_plan"] = {"question_type": "historical_outcome"}
        context["evidence"][0]["text"] = "22级毕业生共85人保研14人。"
        invalid = {
            "answer": "往届大约班级前15%-17%可以保研。",
            "citations": ["S1"],
            "claims": [{
                "text": "班级前15%-17%可以保研",
                "evidence_ids": ["S1"],
                "certainty": "experience",
            }],
            "unresolved": [],
        }
        valid, errors = validate_composition(invalid, context)
        self.assertFalse(valid)
        self.assertIn("historical_outcome_inferred_rank_without_evidence", errors)

    def test_validator_rejects_peer_claim_presented_as_official(self):
        context = packet()
        invalid = {
            "answer": "学校规定是四人寝。[S1]",
            "citations": ["S1"],
            "claims": [{"text": "四人寝", "evidence_ids": ["S1"], "certainty": "official"}],
            "unresolved": [],
        }
        valid, errors = validate_composition(invalid, context)
        self.assertFalse(valid)
        self.assertTrue(any("peer_claim_not_labeled_experience" in error for error in errors))

    def test_refusal_makes_no_evidence_claim(self):
        context = packet(can_generate=False)
        result = ExtractiveComposer().generate(context)
        valid, errors = validate_composition(result, context)
        self.assertTrue(valid, errors)
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["claims"], [])

    def test_structured_facts_can_combine_multiple_relevant_rows(self):
        context = packet()
        context["evidence"] = [
            {
                **context["evidence"][0],
                "evidence_id": f"S{index}",
                "chunk_type": "structured_fact",
                "text": text,
            }
            for index, text in enumerate(
                ["生活一区 床垫 2m×1m", "生活二区 床垫 2.1m×1.01m", "生活一区 床架 2.08m×0.97m"],
                start=1,
            )
        ]
        result = ExtractiveComposer().generate(context)
        self.assertEqual(result["citations"], ["S1", "S2", "S3"])
        self.assertIn("2.1m×1.01m", result["answer"])


if __name__ == "__main__":
    unittest.main()
