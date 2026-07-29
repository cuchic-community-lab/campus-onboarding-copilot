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


if __name__ == "__main__":
    unittest.main()
