import json
import tempfile
import unittest
from pathlib import Path

from campus_copilot.official_sync import (
    HttpResponse,
    load_approved_official_documents,
    review_official_page,
    sync_official_sites,
)


def html(title: str, body: str, links=None) -> bytes:
    anchors = "".join(f'<a href="{url}">link</a>' for url in (links or []))
    return f"<html><head><title>{title}</title></head><body><main>{body}</main>{anchors}</body></html>".encode()


class OfficialSyncTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config_path = root / "official.json"
        self.state_path = root / "official_sites" / "state.json"
        self.snapshot_dir = root / "official_sites" / "snapshots"
        self.config_path.write_text(json.dumps({
            "schema_version": 1,
            "user_agent": "test-agent",
            "max_page_bytes": 100000,
            "request_timeout_seconds": 1,
            "sources": [{
                "source_id": "school",
                "issuer": "学校",
                "seed_urls": ["https://school.example.edu/"],
                "allowed_hosts": ["school.example.edu"],
                "allowed_path_prefixes": ["/news/", "/"],
                "max_pages_per_run": 5,
                "tags": ["学校官网"],
            }],
        }), encoding="utf-8")
        self.pages = {
            "https://school.example.edu/": html(
                "首页",
                "这是学校官方网站首页，包含招生、教学和校园服务等公开信息。" * 3,
                [
                    "/news/admission.htm",
                    "https://outside.example.com/news",
                    "https://user@school.example.edu/private",
                ],
            ),
            "https://school.example.edu/news/admission.htm": html(
                "招生通知",
                "2026年7月30日发布招生通知，学生应当按照学校公布的流程完成申请。" * 3,
            ),
        }
        self.requested = []

    def tearDown(self):
        self.temporary.cleanup()

    def fetch(self, url, timeout, max_bytes, user_agent):
        self.requested.append(url)
        return HttpResponse(url, "text/html; charset=utf-8", self.pages[url])

    def sync(self):
        return sync_official_sites(
            self.config_path,
            self.state_path,
            self.snapshot_dir,
            fetcher=self.fetch,
            robots_allowed=lambda url: True,
        )

    def test_sync_stays_inside_allowlist_and_creates_pending_snapshots(self):
        report = self.sync()
        self.assertEqual(report["new"], 2)
        self.assertNotIn("https://outside.example.com/news", self.requested)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            state["pages"]["https://school.example.edu/news/admission.htm"]["approval_status"],
            "pending",
        )
        snapshots = list(self.snapshot_dir.glob("*.json"))
        self.assertEqual(len(snapshots), 2)

    def test_unchanged_page_preserves_approval_but_changed_page_requires_review(self):
        self.sync()
        url = "https://school.example.edu/news/admission.htm"
        review_official_page(url, "approved", self.state_path)
        second = self.sync()
        self.assertEqual(second["unchanged"], 2)
        approved = load_approved_official_documents(self.state_path)
        self.assertEqual([item.source_url for item in approved], [url])
        self.assertEqual(approved[0].authority_tier, "official_web")
        old_snapshot_count = len(list(self.snapshot_dir.glob("*.json")))

        self.pages[url] = html(
            "招生通知（更新）",
            "2026年7月31日发布更新后的招生通知，申请流程与截止时间发生变化。" * 3,
        )
        changed = self.sync()
        self.assertEqual(changed["changed"], 1)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["pages"][url]["approval_status"], "pending")
        self.assertEqual(load_approved_official_documents(self.state_path), [])
        self.assertEqual(len(list(self.snapshot_dir.glob("*.json"))), old_snapshot_count + 1)

    def test_robots_denial_skips_without_fetching(self):
        report = sync_official_sites(
            self.config_path,
            self.state_path,
            self.snapshot_dir,
            fetcher=self.fetch,
            robots_allowed=lambda url: False,
        )
        self.assertEqual(report["checked"], 0)
        self.assertEqual(self.requested, [])

    def test_duplicate_content_is_visible_and_not_indexed_twice(self):
        self.pages["https://school.example.edu/"] = html(
            "首页",
            "这是学校官方网站首页，包含招生、教学和校园服务等公开信息。" * 3,
            ["/main.htm"],
        )
        self.pages["https://school.example.edu/main.htm"] = self.pages["https://school.example.edu/"]
        self.sync()
        self.sync()
        duplicate_url = "https://school.example.edu/main.htm"
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIsNone(state["pages"]["https://school.example.edu/"]["duplicate_of"])
        self.assertEqual(state["pages"][duplicate_url]["duplicate_of"], "https://school.example.edu/")
        review_official_page("https://school.example.edu/", "approved", self.state_path)
        review_official_page(duplicate_url, "approved", self.state_path)
        documents = load_approved_official_documents(self.state_path)
        self.assertEqual(len(documents), 1)


if __name__ == "__main__":
    unittest.main()
