import io
import json
import unittest
from unittest.mock import patch

from campus_copilot.search_discovery import (
    NullSearchDiscovery,
    TavilySearchDiscovery,
    configured_search_discovery,
    sanitize_search_query,
)


class FakeResponse:
    def __init__(self, value):
        self.payload = json.dumps(value, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class SearchDiscoveryTest(unittest.TestCase):
    def test_search_query_redacts_common_personal_identifiers(self):
        value = sanitize_search_query(
            "我的手机号13800138000，邮箱student@example.com，学号202612345678怎么注册？"
        )
        self.assertNotIn("13800138000", value)
        self.assertNotIn("student@example.com", value)
        self.assertNotIn("202612345678", value)
        self.assertIn("[手机号已省略]", value)

    def test_missing_search_key_disables_discovery_transparently(self):
        with patch.dict(
            "os.environ",
            {"CAMPUS_WEB_SEARCH_PROVIDER": "tavily", "CAMPUS_WEB_SEARCH_API_KEY": ""},
            clear=True,
        ):
            provider = configured_search_discovery()
        self.assertIsInstance(provider, NullSearchDiscovery)
        self.assertEqual(provider.status()["mode"], "disabled")

    def test_tavily_result_becomes_labeled_public_evidence(self):
        provider = TavilySearchDiscovery("test-key")
        response = FakeResponse({
            "results": [{
                "title": "智能科学与技术就业方向",
                "url": "https://example.edu/careers",
                "content": "毕业生可从事人工智能系统研发、数据分析与软件工程。",
                "score": 0.82,
            }]
        })
        with patch("campus_copilot.search_discovery.request.urlopen", return_value=response):
            result = provider.search("智能科学与技术就业方向", ["public"], 3)
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "success")
        evidence = result["results"][0]
        self.assertEqual(evidence["authority_tier"], "public_web")
        self.assertEqual(evidence["retrieval_origin"], "autonomous_search")
        self.assertIn("人工智能系统研发", evidence["text"])

    def test_official_route_drops_non_cuc_result(self):
        provider = TavilySearchDiscovery("test-key")
        response = FakeResponse({
            "results": [{
                "title": "第三方转载",
                "url": "https://example.com/cuc-news",
                "content": "学校通知摘要。",
                "score": 0.9,
            }]
        })
        with patch("campus_copilot.search_discovery.request.urlopen", return_value=response):
            result = provider.search("最新通知", ["official"], 3)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
