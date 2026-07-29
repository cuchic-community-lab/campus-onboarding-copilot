import unittest

from campus_copilot.source_policy import classify_file, classify_link, faq_decision


class SourcePolicyTest(unittest.TestCase):
    def test_official_document_is_assertable_with_citation(self):
        decision = classify_file("本科生院〔2025〕65号 关于印发选课管理办法的通知.pdf", "pdf", ["官方文件"])
        self.assertEqual(decision.authority_tier, "official_policy")
        self.assertEqual(decision.assertion_policy, "assert_with_citation")

    def test_record_spreadsheet_requires_privacy_review(self):
        decision = classify_file("2023级劳动积分记录.xlsx", "excel", ["劳育"])
        self.assertEqual(decision.privacy_risk, "review_required")

    def test_student_faq_is_never_official(self):
        self.assertEqual(faq_decision().assertion_policy, "label_as_experience")

    def test_cuc_link_is_official_navigation_only(self):
        decision = classify_link("选课系统", "http://xsxk.cuc.edu.cn/", ["选课"])
        self.assertEqual(decision.authority_tier, "official_guidance")
        self.assertEqual(decision.assertion_policy, "navigation_only")


if __name__ == "__main__":
    unittest.main()
