import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict
from urllib.parse import quote, urlparse

from .config import DB_PATH, PROJECT_ROOT
from .chat import GroundedChatService
from .context_builder import build_context_packet
from .retrieval import HybridRetriever
from .service import corpus_stats
from .web_retrieval import configured_web_retriever
from .library import library_payload, resolve_library_file
from .preview_security import PreviewGuard, login_page, parse_login_body, preview_status


WEB_ROOT = PROJECT_ROOT / "web"


class AppHandler(BaseHTTPRequestHandler):
    retriever = HybridRetriever(DB_PATH)
    chat = GroundedChatService(retriever, web_retriever=configured_web_retriever())
    preview = PreviewGuard()

    def end_headers(self) -> None:
        if self.preview.enabled:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
                self.send_header("Strict-Transport-Security", "max-age=86400")
        super().end_headers()

    def _client_ip(self) -> str:
        return self.preview.client_ip(self.headers, self.client_address[0])

    def _protected(self, path: str, is_chat: bool = False) -> bool:
        if not self.preview.enabled:
            return True
        if not self.preview.authorized(self.headers):
            if self.command == "GET" and path in {"/", "/index.html", "/preview/login"}:
                body = login_page()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "preview_auth_required"}, 401)
            return False
        if not self.preview.allow_request(self._client_ip(), is_chat=is_chat):
            self._json({"error": "rate_limited"}, 429)
            return False
        return True

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
        if path == "/api/preview/status":
            enabled, mode = preview_status(self.preview)
            self._json({"enabled": enabled, "mode": mode})
            return
        if not self._protected(path):
            return
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
        if path == "/api/library":
            self._json(library_payload())
            return
        if path.startswith("/files/"):
            file_path = resolve_library_file(path.removeprefix("/files/"))
            if file_path is None:
                self._json({"error": "not_found"}, 404)
                return
            body = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(file_path.name))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/assets/hic-copilot-text-logo.svg":
            body = (WEB_ROOT / "assets" / "hic-copilot-text-logo.svg").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
            path = urlparse(self.path).path
            if path == "/api/preview/login" and self.preview.enabled:
                if not self.preview.same_origin(self.headers):
                    self._json({"error": "origin_rejected"}, 403)
                    return
                if not self.preview.allow_login(self._client_ip()):
                    self._json({"error": "rate_limited"}, 429)
                    return
                length = min(int(self.headers.get("Content-Length", "0")), 4097)
                code = parse_login_body(self.rfile.read(length), self.headers.get("Content-Type", ""))
                if not self.preview.valid_code(code):
                    body = login_page("访问口令不正确，请检查后重试。")
                    self.send_response(401)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(303)
                secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", self.preview.session_cookie(secure=secure))
                self.end_headers()
                return
            if not self._protected(path, is_chat=path == "/api/chat"):
                return
            if self.preview.enabled and not self.preview.same_origin(self.headers):
                self._json({"error": "origin_rejected"}, 403)
                return
            payload = self._read_json()
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
