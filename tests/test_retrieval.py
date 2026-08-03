import tempfile
import unittest
from pathlib import Path

from campus_copilot.db import rebuild
from campus_copilot.models import ChunkRecord, DocumentRecord
from campus_copilot.retrieval import HybridRetriever


class RetrievalTest(unittest.TestCase):
    def test_domain_entity_match_separates_recommendation_from_status_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            peer = DocumentRecord(
                document_id="peer", title="新生问答", source_url="https://example.test/peer",
                local_path="", media_type="markdown", source_kind="peer_faq",
                authority_tier="peer_experience", assertion_policy="label_as_experience",
                parse_status="parsed",
            )
            handbook = DocumentRecord(
                document_id="handbook", title="学生手册", source_url="https://example.test/handbook",
                local_path="", media_type="pdf", source_kind="official_guidance",
                authority_tier="official_guidance", assertion_policy="assert_if_current",
                parse_status="parsed",
            )
            policy = DocumentRecord(
                document_id="policy", title="推荐优秀本科毕业生免试攻读研究生办法",
                source_url="https://example.test/policy", local_path="", media_type="pdf",
                source_kind="official_policy", authority_tier="official_policy",
                assertion_policy="assert_with_citation", parse_status="parsed",
            )
            chunks = [
                ChunkRecord(
                    chunk_id="peer", document_id="peer", title=peer.title,
                    text="问题：毕业出路有哪些？回答：有保研机会，往届保研比例约17%。",
                    chunk_type="faq", sequence=0, tags=["保研"],
                    authority_tier="peer_experience", assertion_policy="label_as_experience",
                    source_url=peer.source_url,
                ),
                ChunkRecord(
                    chunk_id="retention", document_id="handbook", title=handbook.title,
                    text="学生因创业可以申请保留学籍。保留学籍原则上以一学年为单位。",
                    chunk_type="document", sequence=0, tags=["学籍"],
                    authority_tier="official_guidance", assertion_policy="assert_if_current",
                    source_url=handbook.source_url,
                ),
                ChunkRecord(
                    chunk_id="policy", document_id="policy", title=policy.title,
                    text="本办法规范推荐优秀应届本科毕业生免试攻读硕士学位研究生工作。",
                    chunk_type="document", sequence=0, tags=["推免", "保研"],
                    authority_tier="official_policy", assertion_policy="assert_with_citation",
                    source_url=policy.source_url,
                ),
            ]
            rebuild(path, [peer, handbook, policy], chunks)
            result = HybridRetriever(path).search("学生有保研机会吗？")
            self.assertEqual(result["results"][0]["chunk_id"], "peer")
            self.assertEqual(result["results"][0]["score_explanation"]["domain_entity_match"], 1.0)
            retention = next(item for item in result["results"] if item["chunk_id"] == "retention")
            self.assertEqual(retention["score_explanation"]["domain_entity_multiplier"], 0.35)

    def test_hybrid_search_returns_relevant_faq_and_labels_experience(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            doc = DocumentRecord(
                document_id="d1", title="宿舍问答", source_url="https://example.test/qa", local_path="",
                media_type="markdown", source_kind="peer_faq", authority_tier="peer_experience",
                assertion_policy="label_as_experience", parse_status="parsed",
            )
            # MIN_EVIDENCE default is now 2, so provide two corroborating FAQ
            # chunks (both label_as_experience) to keep the test meaningful.
            chunks = [
                ChunkRecord(
                    chunk_id="c1", document_id="d1", title="宿舍问答",
                    text="问题：宿舍是几人间？ 回答：目前大概率四人寝两人住。",
                    chunk_type="faq", sequence=0, authority_tier="peer_experience",
                    assertion_policy="label_as_experience", source_url=doc.source_url, uncertainty=["大概率"],
                ),
                ChunkRecord(
                    chunk_id="c2", document_id="d1", title="宿舍问答",
                    text="问题：宿舍有独立卫浴吗？ 回答：有空调和独立卫浴，每层有公共洗衣房。",
                    chunk_type="faq", sequence=1, authority_tier="peer_experience",
                    assertion_policy="label_as_experience", source_url=doc.source_url,
                ),
            ]
            rebuild(path, [doc], chunks)
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
