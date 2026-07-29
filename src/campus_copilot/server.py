import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

from .config import DB_PATH, PROJECT_ROOT
from .context_builder import build_context_packet
from .retrieval import HybridRetriever
from .service import corpus_stats


WEB_ROOT = PROJECT_ROOT / "web"


class AppHandler(BaseHTTPRequestHandler):
    retriever = HybridRetriever(DB_PATH)

    def _json(self, body: Dict[str, object], status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"status": "ok", "database_ready": DB_PATH.exists(), "llm_connected": False})
            return
        if path == "/api/corpus/stats":
            self._json(corpus_stats())
            return
        if path in {"/", "/index.html"}:
            body = (WEB_ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            query = str(payload.get("query", "")).strip()
            if not query:
                self._json({"error": "query_required"}, 400)
                return
            result = self.retriever.search(query, int(payload.get("top_k", 6)), payload.get("profile") or {})
            path = urlparse(self.path).path
            if path == "/api/search":
                self._json(result)
            elif path == "/api/context":
                self._json(build_context_packet(result))
            else:
                self._json({"error": "not_found"}, 404)
        except Exception as exc:
            self._json({"error": "request_failed", "detail": str(exc)}, 500)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Campus Copilot running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
