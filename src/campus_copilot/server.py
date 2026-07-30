import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

from .config import DB_PATH, PROJECT_ROOT
from .chat import GroundedChatService
from .context_builder import build_context_packet
from .retrieval import HybridRetriever
from .service import corpus_stats
from .web_retrieval import configured_web_retriever


WEB_ROOT = PROJECT_ROOT / "web"


class AppHandler(BaseHTTPRequestHandler):
    retriever = HybridRetriever(DB_PATH)
    chat = GroundedChatService(retriever, web_retriever=configured_web_retriever())

    def _json(self, body: Dict[str, object], status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("request_body_too_large")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({
                "status": "ok",
                "database_ready": DB_PATH.exists(),
                "composer": self.chat.status(),
            })
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
            path = urlparse(self.path).path
            if path == "/api/session/reset":
                session_id = str(payload.get("session_id") or "")
                if session_id:
                    self.chat.reset(session_id)
                self._json({"status": "ok", "session_id": session_id})
                return
            query = str(payload.get("query", "")).strip()
            if not query:
                self._json({"error": "query_required"}, 400)
                return
            if len(query) > 500:
                self._json({"error": "query_too_long"}, 400)
                return
            top_k = max(1, min(int(payload.get("top_k", 6)), 10))
            if path == "/api/search":
                result = self.retriever.search(query, top_k, payload.get("profile") or {})
                self._json(result)
            elif path == "/api/context":
                result = self.retriever.search(query, top_k, payload.get("profile") or {})
                self._json(build_context_packet(result))
            elif path == "/api/chat":
                self._json(self.chat.ask(
                    query=query,
                    session_id=str(payload.get("session_id") or "") or None,
                    profile=payload.get("profile") or {},
                    top_k=top_k,
                ))
            else:
                self._json({"error": "not_found"}, 404)
        except ValueError as exc:
            self._json({"error": "invalid_request", "detail": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": "request_failed"}, 500)

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
