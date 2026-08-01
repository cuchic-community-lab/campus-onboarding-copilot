import unittest
from urllib.parse import quote

from campus_copilot.library import library_payload, resolve_library_file


class LibraryTest(unittest.TestCase):
    def test_payload_preserves_original_public_library_structure(self):
        payload = library_payload()
        self.assertGreater(payload["counts"]["files"], 0)
        self.assertGreater(payload["counts"]["links"], 0)
        self.assertGreater(payload["counts"]["questions"], 50)
        self.assertTrue(any(item["name"] == "入学指南.pdf" for item in payload["files"]))
        self.assertTrue(any("宿舍" in item["category"] for item in payload["questions"]))
        self.assertTrue(all(item["url"].startswith("/files/") for item in payload["files"]))

    def test_hidden_admin_fields_are_not_exposed(self):
        payload = library_payload()
        public_fields = set(payload["files"][0])
        self.assertNotIn("path", public_fields)
        self.assertNotIn("visible", public_fields)
        self.assertNotIn("views", public_fields)

    def test_only_manifest_listed_files_can_be_served(self):
        allowed = resolve_library_file(quote("入学指南.pdf", safe=""))
        self.assertIsNotNone(allowed)
        self.assertTrue(allowed.is_file())
        self.assertIsNone(resolve_library_file("../files.json"))
        self.assertIsNone(resolve_library_file("not-in-manifest.pdf"))


if __name__ == "__main__":
    unittest.main()
