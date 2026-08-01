import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse

from .config import DATA_DIR, PROJECT_ROOT
from .extractors import stable_id
from .models import DocumentRecord


OFFICIAL_CRAWL_CONFIG = PROJECT_ROOT / "config" / "official_crawl.json"
OFFICIAL_SITE_DIR = DATA_DIR / "official_sites"
OFFICIAL_STATE_PATH = OFFICIAL_SITE_DIR / "state.json"
OFFICIAL_SNAPSHOT_DIR = OFFICIAL_SITE_DIR / "snapshots"


@dataclass
class HttpResponse:
    url: str
    content_type: str
    payload: bytes


class OfficialPageParser(HTMLParser):
    BLOCK_TAGS = {"article", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4", "li", "main", "p", "section", "td", "th", "tr"}
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: List[str] = []
        self.current: List[str] = []
        self.blocks: List[str] = []
        self.links: List[str] = []

    def _flush(self) -> None:
        value = re.sub(r"\s+", " ", " ".join(self.current)).strip()
        if value:
            self.blocks.append(value)
        self.current = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if lowered == "title":
            self.in_title = True
        if lowered in self.BLOCK_TAGS:
            self._flush()
        if lowered == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if lowered == "title":
            self.in_title = False
        if lowered in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        self.current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.title_parts)).strip()

    @property
    def text(self) -> str:
        return "\n".join(dict.fromkeys(value for value in self.blocks if len(value) >= 2))


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def _allowed(url: str, source: Dict[str, object]) -> bool:
    parsed = urlparse(url)
    hosts = {str(value).lower() for value in source.get("allowed_hosts", [])}
    prefixes = [str(value) for value in source.get("allowed_path_prefixes", ["/"])]
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
        and str(parsed.hostname or "").lower() in hosts
        and any((parsed.path or "/").startswith(prefix) for prefix in prefixes)
        and not re.search(r"\.(?:jpg|jpeg|png|gif|svg|webp|zip|rar|7z|mp4|mp3)$", parsed.path, re.IGNORECASE)
    )


def _decode(payload: bytes, content_type: str) -> str:
    declared = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    meta = re.search(br"charset\s*=\s*['\"]?([\w-]+)", payload[:4096], re.IGNORECASE)
    candidates = [
        declared.group(1) if declared else None,
        meta.group(1).decode("ascii", errors="ignore") if meta else None,
        "utf-8",
        "gb18030",
    ]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _published_at(text: str) -> Optional[str]:
    match = re.search(r"(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})\s*日?", text[:5000])
    if not match:
        return None
    year, month, day = (int(value) for value in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _default_fetcher(url: str, timeout: float, max_bytes: int, user_agent: str) -> HttpResponse:
    outbound = request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
    with request.urlopen(outbound, timeout=timeout) as response:
        content_type = str(response.headers.get("Content-Type", ""))
        if "html" not in content_type.lower():
            raise ValueError("official_sync_unsupported_content_type")
        payload = response.read(max_bytes + 1)
        final_url = response.geturl()
    if len(payload) > max_bytes:
        raise ValueError("official_sync_page_too_large")
    return HttpResponse(final_url, content_type, payload)


def _robots_checker(user_agent: str, timeout: float) -> Callable[[str], bool]:
    cache: Dict[str, List[str]] = {}

    def allowed(url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in cache:
            robots_url = origin + "/robots.txt"
            try:
                outbound = request.Request(robots_url, headers={"User-Agent": user_agent})
                with request.urlopen(outbound, timeout=timeout) as response:
                    body = response.read(256_000).decode("utf-8", errors="replace")
                cache[origin] = body.splitlines()
            except HTTPError as exc:
                cache[origin] = [] if exc.code == 404 else ["User-agent: *", "Disallow: /"]
            except (URLError, TimeoutError):
                cache[origin] = ["User-agent: *", "Disallow: /"]
        from urllib.robotparser import RobotFileParser
        parser = RobotFileParser()
        parser.parse(cache[origin])
        return parser.can_fetch(user_agent, url)

    return allowed


def _load_state(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"schema_version": 1, "pages": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    value.setdefault("schema_version", 1)
    value.setdefault("pages", {})
    return value


def _save_state(path: Path, state: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _duplicate_canonical(
    url: str,
    checksum: str,
    pages: Dict[str, Dict[str, object]],
) -> Optional[str]:
    same_content = sorted(
        {url}.union(
            existing_url for existing_url, existing in pages.items()
            if existing_url != url and existing.get("checksum") == checksum
        )
    )
    return same_content[0] if same_content and same_content[0] != url else None


def sync_official_sites(
    config_path: Path = OFFICIAL_CRAWL_CONFIG,
    state_path: Path = OFFICIAL_STATE_PATH,
    snapshot_dir: Path = OFFICIAL_SNAPSHOT_DIR,
    fetcher: Optional[Callable[[str, float, int, str], HttpResponse]] = None,
    robots_allowed: Optional[Callable[[str], bool]] = None,
    persist: bool = True,
    max_pages_per_source: Optional[int] = None,
) -> Dict[str, object]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    state = _load_state(state_path)
    pages: Dict[str, Dict[str, object]] = state["pages"]  # type: ignore[assignment]
    user_agent = str(config.get("user_agent", "CampusOnboardingCopilot/0.2"))
    timeout = float(config.get("request_timeout_seconds", 12))
    max_bytes = int(config.get("max_page_bytes", 1_500_000))
    fetch = fetcher or _default_fetcher
    can_fetch = robots_allowed or _robots_checker(user_agent, timeout)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    report = {"checked": 0, "new": 0, "changed": 0, "unchanged": 0, "skipped": 0, "errors": []}

    for source in config.get("sources", []):
        queue = deque(_canonical_url(str(url)) for url in source.get("seed_urls", []))
        seen: Set[str] = set()
        configured_limit = int(source.get("max_pages_per_run", 30))
        limit = min(configured_limit, max_pages_per_source) if max_pages_per_source else configured_limit
        while queue and len(seen) < limit:
            url = queue.popleft()
            if url in seen or not _allowed(url, source):
                report["skipped"] += 1
                continue
            seen.add(url)
            if not can_fetch(url):
                report["skipped"] += 1
                continue
            try:
                response = fetch(url, timeout, max_bytes, user_agent)
                final_url = _canonical_url(response.url)
                if not _allowed(final_url, source):
                    raise ValueError("official_sync_redirect_outside_allowlist")
                parser = OfficialPageParser()
                parser.feed(_decode(response.payload, response.content_type))
                parser.close()
                text = parser.text.strip()
                if len(text) < 80:
                    raise ValueError("official_sync_content_too_short")
                checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
                previous = pages.get(final_url)
                unchanged = bool(previous and previous.get("checksum") == checksum)
                url_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:16]
                snapshot_path = snapshot_dir / f"{url_hash}-{checksum[:16]}.json"
                try:
                    relative_snapshot = str(snapshot_path.relative_to(state_path.parent))
                except ValueError:
                    raise ValueError("official_sync_snapshot_dir_must_be_within_state_dir")
                snapshot = {
                    "url": final_url,
                    "source_id": source["source_id"],
                    "issuer": source["issuer"],
                    "campus": source.get("campus", "CUCHIC"),
                    "title": parser.title or final_url,
                    "content": text,
                    "checksum": checksum,
                    "published_at": _published_at(text),
                    "fetched_at": now,
                    "tags": list(source.get("tags", [])),
                    "content_chars": len(text),
                }
                if unchanged:
                    previous["last_checked_at"] = now
                    previous["issuer"] = source["issuer"]
                    previous["campus"] = source.get("campus", "CUCHIC")
                    previous["content_chars"] = len(text)
                    previous["duplicate_of"] = _duplicate_canonical(final_url, checksum, pages)
                    report["unchanged"] += 1
                else:
                    duplicate_of = _duplicate_canonical(final_url, checksum, pages)
                    pages[final_url] = {
                        "url": final_url,
                        "source_id": source["source_id"],
                        "issuer": source["issuer"],
                        "campus": source.get("campus", "CUCHIC"),
                        "title": snapshot["title"],
                        "checksum": checksum,
                        "previous_checksum": previous.get("checksum") if previous else None,
                        "snapshot": relative_snapshot,
                        "content_chars": len(text),
                        "duplicate_of": duplicate_of,
                        "approval_status": "pending",
                        "first_seen_at": previous.get("first_seen_at", now) if previous else now,
                        "last_changed_at": now,
                        "last_checked_at": now,
                    }
                    if persist:
                        target = snapshot_path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    report["changed" if previous else "new"] += 1
                report["checked"] += 1
                for href in parser.links:
                    candidate = _canonical_url(urljoin(final_url, href))
                    if candidate not in seen and _allowed(candidate, source):
                        queue.append(candidate)
            except Exception as exc:
                report["errors"].append({"url": url, "error": type(exc).__name__, "detail": str(exc)[:160]})
    state["updated_at"] = now
    if persist:
        _save_state(state_path, state)
    report["pending_review"] = sum(1 for page in pages.values() if page.get("approval_status") == "pending")
    report["state_path"] = str(state_path)
    return report


def review_official_page(
    url: Optional[str] = None,
    action: Optional[str] = None,
    state_path: Path = OFFICIAL_STATE_PATH,
) -> Dict[str, object]:
    state = _load_state(state_path)
    pages: Dict[str, Dict[str, object]] = state["pages"]  # type: ignore[assignment]
    if url and action:
        canonical = _canonical_url(url)
        if canonical not in pages:
            raise ValueError("official_page_not_found")
        if action not in {"approved", "rejected"}:
            raise ValueError("invalid_review_action")
        pages[canonical]["approval_status"] = action
        pages[canonical]["reviewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
        _save_state(state_path, state)
    return {
        "pages": [
            {key: page.get(key) for key in ("url", "title", "issuer", "approval_status", "last_changed_at", "checksum")}
            | {"content_chars": page.get("content_chars"), "duplicate_of": page.get("duplicate_of")}
            for page in sorted(pages.values(), key=lambda value: str(value.get("url")))
        ]
    }


def load_approved_official_documents(
    state_path: Path = OFFICIAL_STATE_PATH,
) -> List[DocumentRecord]:
    state = _load_state(state_path)
    documents: List[DocumentRecord] = []
    for page in state["pages"].values():  # type: ignore[union-attr]
        if page.get("approval_status") != "approved":
            continue
        if page.get("duplicate_of"):
            continue
        snapshot_path = state_path.parent / str(page["snapshot"])
        if not snapshot_path.exists():
            continue
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        documents.append(DocumentRecord(
            document_id=stable_id("official-web", str(snapshot["url"])),
            title=str(snapshot["title"]),
            source_url=str(snapshot["url"]),
            local_path=str(snapshot_path),
            media_type="html",
            source_kind="official_web",
            authority_tier="official_web",
            assertion_policy="assert_with_citation",
            issuer=str(snapshot["issuer"]),
            campus=str(page.get("campus", snapshot.get("campus", "CUCHIC"))),
            description="Approved snapshot from the governed official-site sync pipeline.",
            tags=list(snapshot.get("tags", [])) + ["已审核官网快照"],
            published_at=snapshot.get("published_at"),
            uploaded_at=snapshot.get("fetched_at"),
            date_status="page_date" if snapshot.get("published_at") else "crawl_date_only",
            checksum=str(snapshot["checksum"]),
            parse_status="parsed",
            content=str(snapshot["content"]),
            privacy_risk="low",
        ))
    return documents
