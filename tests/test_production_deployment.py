import os
import unittest
from pathlib import Path
from unittest.mock import patch

from campus_copilot.server import _bounded_env_int, response_security_headers


ROOT = Path(__file__).resolve().parents[1]


class ProductionDeploymentTest(unittest.TestCase):
    def test_api_security_headers_disable_caching_and_enable_hsts_behind_https(self):
        headers = response_security_headers("/api/chat", "https")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_static_content_keeps_security_headers_without_forcing_no_store(self):
        headers = response_security_headers("/", "http")
        self.assertNotIn("Cache-Control", headers)
        self.assertNotIn("Strict-Transport-Security", headers)
        self.assertEqual(headers["Permissions-Policy"], "camera=(), microphone=(), geolocation=()")

    def test_public_chat_limit_is_bounded_and_invalid_values_use_default(self):
        with patch.dict(os.environ, {"LIMIT": "not-a-number"}):
            self.assertEqual(_bounded_env_int("LIMIT", 60, 1, 1000), 60)
        with patch.dict(os.environ, {"LIMIT": "100000"}):
            self.assertEqual(_bounded_env_int("LIMIT", 60, 1, 1000), 1000)

    def test_deployment_files_preserve_secret_and_persistence_boundaries(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        env_example = (ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("deploy/.env.production", gitignore)
        self.assertIn("hic_runtime:/app/data/runtime", compose)
        self.assertIn("CAMPUS_TRACE_DB_PATH: /app/data/runtime/rag_traces.db", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertIn("USER campus", dockerfile)
        self.assertIn("CAMPUS_LLM_API_KEY=\n", env_example)
        self.assertNotIn("ports:\n      - \"8000:8000\"", compose)
        self.assertNotIn("log {", caddyfile)

    def test_ci_validates_compose_and_builds_the_container(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("docker compose --env-file deploy/.env.production config --quiet", workflow)
        self.assertIn("docker build --tag hic-copilot:ci .", workflow)


if __name__ == "__main__":
    unittest.main()
