import json
import mimetypes
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

from .config import (
    AUTO_POLL_SECONDS,
    DB_PATH,
    DIALOG_LOG_DIR,
    FILES_DIR,
    PROJECT_ROOT,
    QA_DIR,
    QRCODES_DIR,
    SITE_ROOT,
    describe_corpus,
    ensure_dirs,
    load_env,
)
from .chat import GroundedChatService
from .composition import OpenAICompatibleComposer, ProviderConfig
from .context_builder import build_context_packet
from .retrieval import HybridRetriever
from .service import build_knowledge_base, corpus_stats
from . import senior


WEB_ROOT = PROJECT_ROOT / "web"
_MIME = mimetypes.MimeTypes()
_MIME.add_type("application/pdf", ".pdf")
_MIME.add_type("text/markdown", ".md")


def _build_once() -> Dict[str, object]:
    """Full/incremental index build; never raises (degraded mode instead)."""
    try:
        return build_knowledge_base(DB_PATH)
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": "error", "detail": str(exc)}


class AppHandler(BaseHTTPRequestHandler):
    server_version = "XiaohaiGPT/1.0"
    retriever: Optional[HybridRetriever] = None
    chat: Optional[GroundedChatService] = None
    build_lock = threading.Lock()

    def _json(self, body: Dict[str, object], status: int = 200) -> None:
        payload = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self._json({"error": "not_found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.guess_type(str(path))[0] or "application/octet-stream")
        if download:
            # RFC 5987：中文文件名需 filename* 编码，否则 latin-1 header 会崩
            ascii_name = path.name.encode("ascii", "ignore").decode() or "download"
            encoded = quote(path.name)
            self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("request_body_too_large")
        body = self.rfile.read(length).decode("utf-8") if length else ""
        return json.loads(body or "{}")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v for k, v in (item.split("=", 1) for item in parsed.query.split("&") if "=" in item)}

        if path == "/api/health":
            self._json(self._health())
            return
        if path == "/api/model/check":
            self._json(self._model_check())
            return
        if path == "/api/corpus/stats":
            self._json(corpus_stats(DB_PATH))
            return
        if path == "/api/senior/answers":
            try:
                self._json(senior.answers(query.get("device_id", "")))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/senior/pending":
            try:
                self._json(senior.pending(query.get("token", "")))
            except PermissionError:
                self._json({"error": "token_required"}, 401)
            return
        if path == "/api/logs":
            self._dialog_logs(query)
            return
        if path == "/api/sessions/stats":
            self._json(self._session_stats())
            return
        if path in {"/", "/index.html", "/xiaohaigpt.html"}:
            # /xiaohaigpt.html 优先返回聊天页；/ 与 /index.html 在聊天页存在时也作为首页返回
            has_chat = (WEB_ROOT / "xiaohaigpt.html").is_file()
            if path == "/xiaohaigpt.html" and has_chat:
                target = WEB_ROOT / "xiaohaigpt.html"
            elif path in {"/", "/index.html"} and has_chat:
                target = WEB_ROOT / "xiaohaigpt.html"
            else:
                target = WEB_ROOT / "index.html"
            if not target.is_file():
                self._json({"error": "index_missing"}, 404)
                return
            self._static(target)
            return
        if path.startswith("/static/"):
            self._static(WEB_ROOT / unquote(path.lstrip("/")))
            return
        if path.startswith("/logo/"):
            # 站点根 logo 目录（favicon/header/welcome 的 svg 图）
            rel = unquote(path[len("/logo/"):])
            self._static((SITE_ROOT / "logo" / rel).resolve())
            return
        if path.startswith("/files/"):
            rel = unquote(path[len("/files/"):])
            target = (FILES_DIR / rel).resolve()
            # PDFs and images open inline (preview); other binaries download.
            inline_suffixes = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
            self._static(target, download=target.suffix.lower() not in inline_suffixes)
            return
        if path.startswith("/qrcodes/"):
            rel = unquote(path[len("/qrcodes/"):])
            self._static((QRCODES_DIR / rel).resolve())
            return
        if path.startswith("/qa/"):
            # FAQ/markdown citations point here (e.g. /qa/新生常见问题.md);
            # md is served inline as text/markdown so it opens in the browser.
            rel = unquote(path[len("/qa/"):])
            self._static((QA_DIR / rel).resolve())
            return
        if path.startswith("/corpus/"):
            # v1.3：站点根目录 markdown（如「新生问答整理.md」）的 citation 打开用。
            # /corpus/新生问答整理.md → SITE_ROOT/新生问答整理.md（仅限 .md）。
            rel = unquote(path[len("/corpus/"):])
            target = (SITE_ROOT / rel).resolve()
            if target.suffix.lower() != ".md" or not str(target).startswith(str(SITE_ROOT.resolve())):
                self._json({"error": "not_found"}, 404)
                return
            self._static(target)
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            path = urlparse(self.path).path

            if path == "/api/ingest":
                force = bool(payload.get("force", False))
                with self.build_lock:
                    result = build_knowledge_base(DB_PATH, force=force)
                self._refresh_services()
                self._json(result)
                return

            if path == "/api/senior/ask":
                try:
                    self._json(senior.ask(
                        device_id=str(payload.get("device_id", "")),
                        question=str(payload.get("question", "")),
                        nickname=str(payload.get("nickname", "匿名")),
                    ))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return

            if path == "/api/senior/reply":
                try:
                    self._json(senior.reply(
                        token=str(payload.get("token", "")),
                        question_id=str(payload.get("question_id", "")),
                        text=str(payload.get("text", "")),
                        author=str(payload.get("author", "师哥师姐")),
                    ))
                except PermissionError:
                    self._json({"error": "token_required"}, 401)
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except LookupError as exc:
                    self._json({"error": str(exc)}, 404)
                return

            if path == "/api/session/reset":
                session_id = str(payload.get("session_id") or "")
                if session_id and self.chat is not None:
                    self.chat.reset(session_id)
                self._json({"status": "ok", "session_id": session_id})
                return

            if path == "/api/session/clear":
                # T1：清空会话。带 session_id 只清该会话；不带则清全部。
                # 只清内存运行时（SessionStore），绝不触碰 data/logs/ 等持久化日志。
                session_id = str(payload.get("session_id") or "").strip()
                if self.chat is not None:
                    if session_id:
                        self.chat.reset(session_id)
                        cleared = session_id
                    else:
                        self.chat.sessions.clear_all()
                        cleared = "all"
                else:
                    cleared = session_id or "all"
                self._json({"status": "ok", "cleared": cleared})
                return

            query = str(payload.get("query", "")).strip()
            if not query:
                self._json({"error": "query_required"}, 400)
                return
            if len(query) > 500:
                self._json({"error": "query_too_long"}, 400)
                return
            top_k = max(1, min(int(payload.get("top_k", 6)), 10))
            if self.retriever is None:
                self._json({"error": "index_not_ready"}, 503)
                return
            if path == "/api/search":
                self._json(self.retriever.search(query, top_k, payload.get("profile") or {}))
            elif path == "/api/context":
                result = self.retriever.search(query, top_k, payload.get("profile") or {})
                self._json(build_context_packet(result))
            elif path == "/api/chat":
                assert self.chat is not None
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
        except Exception:
            self._json({"error": "request_failed"}, 500)

    def _model_check(self) -> Dict[str, object]:
        """Live LLM probe for the front-end status light (green/yellow/red).

        Makes one real minimal call (max_tokens=1) through the same
        OpenAICompatibleComposer path as /api/chat. Never raises: failures are
        returned as ok=false with a stable detail tag.
        """
        config = ProviderConfig.from_env()
        if not config.enabled:
            detail = (
                "missing_api_key"
                if config.requires_api_key and not config.api_key
                else "model_not_configured"
            )
            return {
                "ok": False,
                "composer": "extractive_fallback",
                "provider": config.provider,
                "model": config.model or None,
                "credential_configured": bool(config.api_key),
                "latency_ms": 0,
                "detail": detail,
            }
        ok, detail, latency_ms = OpenAICompatibleComposer(config).probe()
        return {
            "ok": ok,
            "composer": "openai_compatible",
            "provider": config.provider,
            "model": config.model,
            "credential_configured": bool(config.api_key),
            "latency_ms": latency_ms,
            "detail": detail,
        }

    def _health(self) -> Dict[str, object]:
        stats = corpus_stats(DB_PATH)
        composer_status = self.chat.status() if self.chat is not None else {"mode": "not_ready"}
        degraded = (
            not DB_PATH.exists()
            or (DB_PATH.exists() and stats.get("documents", 0) == 0)
            or not describe_corpus()["files_json_exists"]
        )
        degraded_reason = None
        if not describe_corpus()["files_json_exists"]:
            degraded_reason = "site_root_not_found"
        elif not DB_PATH.exists() or stats.get("documents", 0) == 0:
            degraded_reason = "index_not_built"
        last_build = None
        if DB_PATH.exists():
            try:
                from .db import connect
                conn = connect(DB_PATH)
                row = conn.execute("SELECT value FROM index_meta WHERE key='last_sync_at'").fetchone()
                conn.close()
                last_build = row["value"] if row else None
            except Exception:
                last_build = None
        return {
            "status": "ok" if not degraded else "degraded",
            "server_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "database_ready": DB_PATH.exists(),
            "composer": composer_status,
            "corpus": {
                **describe_corpus(),
                **stats,
                "last_build_at": last_build,
                "degraded": degraded,
                "degraded_reason": degraded_reason,
            },
            "rag": {
                "auto_poll_seconds": AUTO_POLL_SECONDS,
                "incremental": True,
            },
        }

    def _refresh_services(self) -> None:
        # Update class-level services so all request instances and the poll
        # thread share the same retriever/chat after an ingest.
        AppHandler.retriever = HybridRetriever(DB_PATH)
        AppHandler.chat = GroundedChatService(AppHandler.retriever)

    def _session_stats(self) -> Dict[str, object]:
        """T2：管理面板会话状态（内存 SessionStore 只读统计）。"""
        if self.chat is None:
            return {"count": 0, "max_sessions": 0, "max_turns": 0, "last_updated_at": None}
        return self.chat.sessions.stats()

    def _dialog_logs(self, query: Dict[str, str]) -> None:
        """T2：对话日志列表 / 单文件内容（data/logs/ 只读，不做任何删除）。

        GET /api/logs          → {files:[{session_id, created_at, lines, size}], total}
        GET /api/logs?file=<id>→ {session_id, filename, content, lines, created_at}
        """
        file_param = query.get("file", "").strip()
        if file_param:
            name = file_param if file_param.endswith(".md") else f"{file_param}.md"
            # 仅允许 32 位 hex 会话 id 的 .md，杜绝路径穿越
            if not re.fullmatch(r"[0-9a-fA-F]{32}\.md", name):
                self._json({"error": "invalid_file"}, 400)
                return
            target = (DIALOG_LOG_DIR / name).resolve()
            if not str(target).startswith(str(DIALOG_LOG_DIR.resolve())) or not target.is_file():
                self._json({"error": "not_found"}, 404)
                return
            text = target.read_text(encoding="utf-8", errors="replace")
            self._json({
                "session_id": target.stem,
                "filename": name,
                "content": text,
                "lines": text.count("\n") + 1 if text else 0,
                "created_at": _log_created_at(text) or _iso_from_mtime(target),
            })
            return

        files = []
        for p in sorted(DIALOG_LOG_DIR.glob("*.md")):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files.append({
                "session_id": p.stem,
                "filename": p.name,
                "created_at": _log_created_at(text) or _iso_from_mtime(p),
                "lines": text.count("\n") + 1 if text else 0,
                "size": p.stat().st_size,
            })
        self._json({"files": files, "total": len(files), "dir": str(DIALOG_LOG_DIR)})

    def log_message(self, format: str, *args: object) -> None:
        return


def _poll_loop() -> None:
    """Periodically detect site manifest changes and re-index incrementally."""
    last_signature = _manifest_signature()
    while True:
        time.sleep(AUTO_POLL_SECONDS)
        signature = _manifest_signature()
        if signature != last_signature:
            try:
                with AppHandler.build_lock:
                    result = build_knowledge_base(DB_PATH)
                AppHandler.retriever = HybridRetriever(DB_PATH)
                AppHandler.chat = GroundedChatService(AppHandler.retriever)
                print(f"[poll] re-indexed: {result.get('changed', 0)} changed, {result.get('added', 0)} added")
            except Exception as exc:
                print(f"[poll] re-index failed: {exc}")
            last_signature = signature


def _manifest_signature() -> str:
    import hashlib
    digest = hashlib.sha256()
    for path in (
        Path(str(SITE_ROOT)) / "files.json",
        Path(str(SITE_ROOT)) / "qrcodes.json",
        Path(str(SITE_ROOT)) / "新生问答整理.md",
    ):
        try:
            digest.update(path.read_bytes())
        except OSError:
            pass
    return digest.hexdigest()


def _log_created_at(text: str) -> Optional[str]:
    """从日志头部元信息解析创建时间（如「- 创建时间：2026-08-10T23:58:17」）。"""
    m = re.search(r"创建时间[：:]\s*([0-9T:.+\-]+)", text)
    return m.group(1) if m else None


def _iso_from_mtime(path: Path) -> str:
    """mtime 兜底：文件系统修改时间转 ISO（无秒以下精度）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime))


def serve(host: str = "127.0.0.1", port: int = 8000, auto_build: bool = True) -> None:
    load_env()
    ensure_dirs()

    if auto_build and not DB_PATH.exists():
        result = _build_once()
        print(f"[startup] index build: {result.get('status')} ({result.get('documents', 0)} docs, {result.get('chunks', 0)} chunks)")

    AppHandler.retriever = HybridRetriever(DB_PATH)
    AppHandler.chat = GroundedChatService(AppHandler.retriever)

    if AUTO_POLL_SECONDS > 0 and auto_build:
        threading.Thread(target=_poll_loop, daemon=True).start()
        print(f"[startup] auto re-index every {AUTO_POLL_SECONDS}s enabled")

    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"XiaohaiGPT running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
