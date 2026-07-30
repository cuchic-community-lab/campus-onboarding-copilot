import json
import tempfile
import unittest
from pathlib import Path

from campus_copilot.web_retrieval import CuratedLiveWebRetriever


class WebRetrievalTest(unittest.TestCase):
    def test_registry_routes_and_extracts_live_page_evidence(self):
        registry = [{
            "source_id": "arrival",
            "title": "官方入学须知",
            "url": "https://official.example.test/arrival",
            "route": "official",
            "authority_tier": "official_web",
            "assertion_policy": "assert_with_live_citation",
            "published_at": "2026-07-01",
            "date_status": "live_page",
            "query_terms": ["报到", "带什么"],
            "focus_terms": ["录取通知书", "个人档案"],
            "uncertainty": [],
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            retriever = CuratedLiveWebRetriever(path)
            retriever._fetch = lambda url: (
                "导航\n报到时请携带录取通知书和密封的个人档案。\n无关新闻。",
                "2026-07-30T15:00:00+0800",
            )
            result = retriever.search("开学报到带什么？", ["official"])
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["authority_tier"], "official_web")
        self.assertIn("录取通知书", result["results"][0]["text"])

    def test_unregistered_query_does_not_broaden_to_arbitrary_web(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text("[]", encoding="utf-8")
            retriever = CuratedLiveWebRetriever(path)
            result = retriever.search("完全未知的问题", ["public"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "no_registered_source")
        self.assertEqual(result["results"], [])

    def test_verified_snapshot_preserves_http_boundary_without_live_fetch(self):
        registry = [{
            "source_id": "legacy-faq",
            "title": "公开转载问答",
            "url": "http://legacy.example.test/faq",
            "route": "public",
            "authority_tier": "public_web",
            "assertion_policy": "cite_as_public_reference",
            "published_at": "2024-06-26",
            "date_status": "verified_snapshot",
            "query_terms": ["毕业证"],
            "focus_terms": ["完全一致"],
            "verified_excerpt": "中外合作办学专业毕业证和其他专业一样吗？答：没有不同，与其他专业完全一致。",
            "verified_at": "2026-07-30T16:45:00+0800",
            "uncertainty": ["第三方转载"],
        }]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            retriever = CuratedLiveWebRetriever(path)
            retriever._fetch = lambda url: (_ for _ in ()).throw(AssertionError("must not fetch HTTP"))
            result = retriever.search("毕业证有中外合办字样吗？", ["public"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["retrieval_origin"], "verified_web_snapshot")
        self.assertIn("完全一致", result["results"][0]["text"])


if __name__ == "__main__":
    unittest.main()
