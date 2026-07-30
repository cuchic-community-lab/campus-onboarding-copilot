import unittest

from campus_copilot.chunking import chunk_faq, chunk_paragraphs, chunk_structured_rows
from campus_copilot.models import DocumentRecord


def document(kind="peer_faq"):
    return DocumentRecord(
        document_id="doc-1", title="Test", source_url="https://example.test", local_path="",
        media_type="markdown", source_kind=kind, authority_tier="peer_experience",
        assertion_policy="label_as_experience", parse_status="parsed",
    )


class ChunkingTest(unittest.TestCase):
    def test_faq_keeps_question_and_complete_answer_together(self):
        chunks = chunk_faq(document(), [{"category": "宿舍", "question": "住几人？", "answer": "目前大概率两人，仍需等待通知。"}])
        self.assertEqual(len(chunks), 1)
        self.assertIn("问题：住几人？", chunks[0].text)
        self.assertIn("大概率", chunks[0].uncertainty)

    def test_paragraph_chunks_do_not_use_blind_fixed_windows(self):
        text = "一、办理流程\n第一步：准备材料。\n第二步：提交申请。\n" + "完整说明。" * 80
        chunks = chunk_paragraphs(document("official_guidance"), text, target_chars=100, max_chars=180)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(chunk.text.strip() for chunk in chunks))

    def test_spreadsheet_rows_become_atomic_facts(self):
        item = document("student_reference")
        item.media_type = "excel"
        item.content = (
            "工作表：生活一区\n序号 | 类型 | 规格\n"
            "1 | 床 | 高架床\n（上床下桌） | 长2.08m*宽0.97m | =DISPIMG(\"ID_1\",1)\n"
            "2\n3 | 书桌 | 宽1m*长0.6m\n"
        )
        chunks = chunk_structured_rows(item)
        self.assertEqual(len(chunks), 2)
        self.assertIn("生活一区", chunks[0].text)
        self.assertIn("长2.08m*宽0.97m", chunks[0].text)
        self.assertNotIn("书桌", chunks[0].text)
        self.assertNotIn("DISPIMG", chunks[0].text)


if __name__ == "__main__":
    unittest.main()
