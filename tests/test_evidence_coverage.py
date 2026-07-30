import unittest

from campus_copilot.evidence_coverage import rank_and_filter_evidence, score_evidence_coverage


class EvidenceCoverageTest(unittest.TestCase):
    def test_joint_program_overlap_without_credential_object_is_rejected(self):
        result = {
            "title": "中外合办校园文化问答",
            "text": "这里有没有英国校园文化？回答：没有。",
            "authority_tier": "peer_experience",
            "score": 99.0,
        }
        coverage = score_evidence_coverage(
            "毕业证有中外合办字样吗？", "credential_wording", result
        )
        self.assertFalse(coverage["covers_question_object"])
        self.assertEqual(
            rank_and_filter_evidence(
                "毕业证有中外合办字样吗？", "credential_wording", [result]
            ),
            [],
        )

    def test_direct_public_answer_ranks_above_indirect_official_award_page(self):
        official = {
            "title": "中外合作办学专业介绍",
            "text": "达到条件后授予中国传媒大学毕业证书和学士学位。",
            "authority_tier": "official_web",
            "score": 10.0,
        }
        public = {
            "title": "中外合作办学专业常见问题解答",
            "text": "毕业证、学位证和其他专业一样吗？答：没有不同，与其他专业完全一致。",
            "authority_tier": "public_web",
            "score": 1.0,
        }
        ranked = rank_and_filter_evidence(
            "毕业证有中外合办字样吗？", "credential_wording", [official, public]
        )
        self.assertEqual(ranked[0]["authority_tier"], "public_web")
        self.assertTrue(ranked[0]["evidence_coverage"]["direct_answer"])
        self.assertFalse(ranked[1]["evidence_coverage"]["direct_answer"])

    def test_generic_student_handbook_credential_text_is_not_adopted(self):
        handbook = {
            "title": "学生手册",
            "text": "毕业证书遗失后不能补办原件，可以申请毕业证明书。",
            "authority_tier": "official_guidance",
            "score": 20.0,
        }
        self.assertEqual(
            rank_and_filter_evidence(
                "毕业证有中外合办字样吗？", "credential_wording", [handbook]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
