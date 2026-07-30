import tempfile
import unittest
from pathlib import Path

from campus_copilot.db import rebuild
from campus_copilot.models import ChunkRecord, DocumentRecord
from campus_copilot.retrieval import HybridRetriever


class RetrievalTest(unittest.TestCase):
    def test_hybrid_search_returns_relevant_faq_and_labels_experience(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            doc = DocumentRecord(
                document_id="d1", title="宿舍问答", source_url="https://example.test/qa", local_path="",
                media_type="markdown", source_kind="peer_faq", authority_tier="peer_experience",
                assertion_policy="label_as_experience", parse_status="parsed",
            )
            chunk = ChunkRecord(
                chunk_id="c1", document_id="d1", title="宿舍问答", text="问题：宿舍是几人间？ 回答：目前大概率四人寝两人住。",
                chunk_type="faq", sequence=0, authority_tier="peer_experience",
                assertion_policy="label_as_experience", source_url=doc.source_url, uncertainty=["大概率"],
            )
            rebuild(path, [doc], [chunk])
            result = HybridRetriever(path).search("宿舍住几个人")
            self.assertEqual(result["results"][0]["chunk_id"], "c1")
            self.assertEqual(result["answerability"], "experience_only")
            self.assertTrue(result["requires_uncertainty_label"])

    def test_exact_student_measurement_beats_unrelated_official_dorm_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            measurement = DocumentRecord(
                document_id="student", title="家具尺寸统计表.xlsx", source_url="https://example.test/student",
                local_path="", media_type="excel", source_kind="student_reference",
                authority_tier="peer_experience", assertion_policy="label_as_experience", parse_status="parsed",
            )
            official = DocumentRecord(
                document_id="official", title="学生手册.pdf", source_url="https://example.test/official",
                local_path="", media_type="pdf", source_kind="official_guidance",
                authority_tier="official_guidance", assertion_policy="assert_if_current", parse_status="parsed",
            )
            chunks = [
                ChunkRecord(
                    chunk_id="measurement", document_id="student", title=measurement.title,
                    text="生活一区 床垫尺寸 长2m*宽1m", chunk_type="structured_fact", sequence=0,
                    authority_tier="peer_experience", assertion_policy="label_as_experience",
                    source_url=measurement.source_url,
                ),
                ChunkRecord(
                    chunk_id="official", document_id="official", title=official.title,
                    text="宿舍应当保持卫生并遵守管理制度。", chunk_type="document", sequence=0,
                    authority_tier="official_guidance", assertion_policy="assert_if_current",
                    source_url=official.source_url,
                ),
            ]
            rebuild(path, [measurement, official], chunks)
            result = HybridRetriever(path).search("宿舍的床多大？")
            self.assertEqual(result["results"][0]["chunk_id"], "measurement")
            self.assertEqual(result["results"][0]["score_explanation"]["attribute_match"], 1.0)


if __name__ == "__main__":
    unittest.main()
