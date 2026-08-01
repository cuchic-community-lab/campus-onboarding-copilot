import json
import tempfile
import unittest
from pathlib import Path

from campus_copilot.web_retrieval import CuratedLiveWebRetriever


class FakeDiscovery:
    name = "fake_discovery"

    def search(self, query, routes, top_k):
        return {
            "executed": True,
            "provider": self.name,
            "status": "success",
            "routes": list(routes),
            "errors": [],
            "results": [{
                "document_id": "discovered-careers",
                "chunk_id": "search-careers",
                "title": "公开就业资料",
                "text": "智能科学与技术毕业生可以从事人工智能研发和软件工程。",
                "chunk_type": "search_excerpt",
                "heading_path": "自主公开网络搜索",
                "page_number": None,
                "tags": ["autonomous_search", "public"],
                "authority_tier": "public_web",
                "assertion_policy": "cite_as_public_reference",
                "source_url": "https://example.edu/careers",
                "cohort": None,
                "academic_year": None,
                "student_level": "all",
                "major": None,
                "campus": None,
                "uploaded_at": None,
                "published_at": None,
                "effective_from": None,
                "date_status": "live_search_unverified",
                "uncertainty": [],
                "retrieval_origin": "autonomous_search",
                "web_source_kind": "public",
                "fetched_at": "2026-07-30T17:00:00+0800",
                "score": 0.8,
                "score_explanation": {"search_provider_score": 0.8},
            }],
        }

    def status(self):
        return {"mode": "live_discovery", "provider": self.name}


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

    def test_discovery_runs_when_registry_has_no_matching_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text("[]", encoding="utf-8")
            retriever = CuratedLiveWebRetriever(path, discovery_provider=FakeDiscovery())
            result = retriever.search("智能科学与技术就业方向", ["public"], 3)
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["retrieval_origin"], "autonomous_search")
        self.assertTrue(result["discovery"]["executed"])
        self.assertEqual(retriever.status()["mode"], "registry_plus_discovery")


if __name__ == "__main__":
    unittest.main()
