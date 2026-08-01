"""Short-lived access control for explicitly enabled development previews."""

import hashlib
import hmac
import html
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from typing import Deque, Dict, Mapping, Tuple
from urllib.parse import parse_qs


COOKIE_NAME = "campus_preview_session"


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 2000) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window_seconds: int, now: float = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            if key not in self._events and len(self._events) >= self._max_keys:
                oldest_key = min(self._events, key=lambda item: self._events[item][-1] if self._events[item] else 0)
                self._events.pop(oldest_key, None)
            events = self._events[key]
            cutoff = current - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(current)
            return True


class PreviewGuard:
    def __init__(self, access_code: str = None, session_secret: bytes = None) -> None:
        self.access_code = access_code if access_code is not None else os.environ.get("CAMPUS_PREVIEW_ACCESS_CODE", "")
        if self.access_code and len(self.access_code) < 16:
            raise ValueError("CAMPUS_PREVIEW_ACCESS_CODE must contain at least 16 characters")
        self.enabled = bool(self.access_code)
        self._secret = session_secret or secrets.token_bytes(32)
        self.limiter = SlidingWindowLimiter()

    def _session_value(self) -> str:
        return hmac.new(self._secret, b"campus-preview-v1", hashlib.sha256).hexdigest()

    def authorized(self, headers: Mapping[str, str]) -> bool:
        if not self.enabled:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            return False
        supplied = cookie.get(COOKIE_NAME)
        return bool(supplied and hmac.compare_digest(supplied.value, self._session_value()))

    def valid_code(self, supplied: str) -> bool:
        return bool(self.enabled and hmac.compare_digest(str(supplied), self.access_code))

    def session_cookie(self, secure: bool = True) -> str:
        parts = [
            f"{COOKIE_NAME}={self._session_value()}",
            "Path=/",
            "Max-Age=14400",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def client_ip(headers: Mapping[str, str], fallback: str) -> str:
        cloudflare_ip = headers.get("CF-Connecting-IP", "").strip()
        if cloudflare_ip:
            return cloudflare_ip[:64]
        forwarded = headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return (forwarded or fallback)[:64]

    @staticmethod
    def same_origin(headers: Mapping[str, str]) -> bool:
        origin = headers.get("Origin", "").strip()
        host = headers.get("Host", "").strip()
        if not origin:
            return True
        return origin in {f"https://{host}", f"http://{host}"}

    def allow_login(self, client_ip: str) -> bool:
        return self.limiter.allow("login:" + client_ip, limit=8, window_seconds=600)

    def allow_request(self, client_ip: str, is_chat: bool = False) -> bool:
        limit = 12 if is_chat else 120
        return self.limiter.allow(("chat:" if is_chat else "request:") + client_ip, limit=limit, window_seconds=60)


def parse_login_body(body: bytes, content_type: str) -> str:
    if len(body) > 4096 or "application/x-www-form-urlencoded" not in content_type:
        return ""
    values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return str(values.get("access_code", [""])[0])


def login_page(error: str = "") -> bytes:
    message = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>访问新生 Copilot 预览</title>
<style>*{{box-sizing:border-box}}body{{margin:0;min-height:100dvh;display:grid;place-items:center;padding:22px;background:linear-gradient(145deg,#edf5ef,#f8f5ec);color:#17231e;font:15px/1.6 system-ui,-apple-system,'PingFang SC',sans-serif}}main{{width:min(100%,390px);padding:28px;border:1px solid #d9e2dc;border-radius:24px;background:#fff;box-shadow:0 22px 60px rgba(25,55,39,.14)}}.mark{{width:44px;height:44px;display:grid;place-items:center;border-radius:14px;background:#176b4b;color:#fff;font-weight:800}}h1{{margin:18px 0 7px;font-size:25px}}p{{margin:0 0 18px;color:#66716c}}label{{display:block;margin-bottom:6px;font-weight:700}}input{{width:100%;height:48px;border:1px solid #ccd7cf;border-radius:13px;padding:0 13px;font-size:16px;outline:none}}input:focus{{border-color:#176b4b;box-shadow:0 0 0 3px rgba(23,107,75,.12)}}button{{width:100%;height:46px;margin-top:12px;border:0;border-radius:13px;background:#176b4b;color:#fff;font-weight:800}}.fine{{margin:14px 0 0;font-size:11px;color:#89938e}}.error{{padding:9px 10px;margin:0 0 12px;border-radius:9px;background:#fff0ee;color:#9b3e35;font-size:12px}}</style></head>
<body><main><div class="mark">传</div><h1>这是临时测试入口</h1><p>输入项目成员提供的访问口令，才能查看资料库和使用 Copilot。</p>{message}<form method="post" action="/api/preview/login"><label for="access_code">访问口令</label><input id="access_code" name="access_code" type="password" minlength="16" maxlength="128" autocomplete="current-password" required autofocus><button type="submit">进入预览</button></form><p class="fine">会话最长保留 4 小时；请勿输入身份证号、手机号等敏感信息。</p></main></body></html>""".encode("utf-8")


def preview_status(guard: PreviewGuard) -> Tuple[bool, str]:
    return guard.enabled, "protected" if guard.enabled else "local_only"
