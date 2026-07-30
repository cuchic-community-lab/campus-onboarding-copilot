import json
import io
import re
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Tuple
from urllib import request
from urllib.error import URLError
from urllib.parse import urlparse

from .config import PROJECT_ROOT, USER_AGENT
from .search_discovery import (
    NullSearchDiscovery,
    SearchDiscoveryProvider,
    configured_search_discovery,
)


SOURCE_REGISTRY_PATH = PROJECT_ROOT / "config" / "web_sources.json"
MAX_PAGE_BYTES = 2_000_000
BLOCK_TAGS = {
    "article", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2",
    "h3", "h4", "h5", "h6", "li", "main", "p", "section", "td", "th", "tr",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}


class WebRetriever(Protocol):
    name: str

    def search(self, query: str, routes: Iterable[str], top_k: int = 4) -> Dict[str, object]:
        ...

    def status(self) -> Dict[str, object]:
        ...


class NullWebRetriever:
    name = "disabled"

    def search(self, query: str, routes: Iterable[str], top_k: int = 4) -> Dict[str, object]:
        return {
            "executed": False,
            "provider": self.name,
            "status": "disabled",
            "routes": list(routes),
            "results": [],
            "errors": [],
        }

    def status(self) -> Dict[str, object]:
        return {"mode": "disabled", "provider": self.name}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._current: List[str] = []
        self.blocks: List[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", " ".join(self._current)).strip()
        if text:
            self.blocks.append(text)
        self._current = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        lowered = tag.lower()
        if lowered in SKIP_TAGS:
            self._skip_depth += 1
        elif not self._skip_depth and lowered in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif not self._skip_depth and lowered in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


@dataclass
class _CachedPage:
    fetched_at: float
    text: str


class CuratedLiveWebRetriever:
    """Fetch and rank live pages from a reviewed source registry.

    This is intentionally not an unrestricted crawler. Source discovery and
    source approval are separate product decisions; runtime retrieval may only
    fetch the exact HTTPS hosts present in the reviewed registry.
    """

    name = "curated_live_web"

    def __init__(
        self,
        registry_path: Path = SOURCE_REGISTRY_PATH,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: float = 1800.0,
        discovery_provider: Optional[SearchDiscoveryProvider] = None,
    ) -> None:
        self.registry_path = registry_path
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.sources = json.loads(registry_path.read_text(encoding="utf-8"))
        self.allowed_hosts = {
            str(urlparse(str(source["url"])).hostname or "").lower()
            for source in self.sources
        }
        self._cache: Dict[str, _CachedPage] = {}
        self._lock = threading.Lock()
        self.discovery_provider = discovery_provider or NullSearchDiscovery()

    @staticmethod
    def _matches(query: str, terms: Iterable[str]) -> float:
        compact = re.sub(r"\s+", "", query).lower()
        matched = [term for term in terms if str(term).lower() in compact]
        if not matched:
            return 0.0
        return sum(max(2, len(str(term))) for term in matched) / max(6, len(compact))

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = str(parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in self.allowed_hosts:
            raise ValueError("web_source_not_allowlisted")

    @staticmethod
    def _decode_page(payload: bytes, content_type: str) -> str:
        declared_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        meta_match = re.search(br"charset\s*=\s*['\"]?([\w-]+)", payload[:4096], re.IGNORECASE)
        candidates = [
            declared_match.group(1) if declared_match else None,
            meta_match.group(1).decode("ascii", errors="ignore") if meta_match else None,
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

    def _fetch(self, url: str) -> Tuple[str, str]:
        self._validate_url(url)
        now = time.time()
        with self._lock:
            cached = self._cache.get(url)
            if cached and now - cached.fetched_at <= self.cache_ttl_seconds:
                return cached.text, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(cached.fetched_at))

        outbound = request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8",
            },
        )
        last_error: Optional[BaseException] = None
        for attempt in range(3):
            try:
                with request.urlopen(outbound, timeout=self.timeout_seconds) as response:
                    final_url = response.geturl()
                    self._validate_url(final_url)
                    content_type = response.headers.get("Content-Type", "")
                    if not any(kind in content_type.lower() for kind in ("html", "text/plain", "application/pdf")):
                        raise ValueError("unsupported_web_content_type")
                    payload = response.read(MAX_PAGE_BYTES + 1)
                break
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))
        else:  # pragma: no cover - loop always breaks or raises
            raise RuntimeError("web_fetch_failed") from last_error
        if len(payload) > MAX_PAGE_BYTES:
            raise ValueError("web_page_too_large")
        if "application/pdf" in content_type.lower() or urlparse(url).path.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise RuntimeError("pypdf_required_for_registered_pdf_source") from exc
            reader = PdfReader(io.BytesIO(payload))
            text = "\n".join((page.extract_text() or "").strip() for page in reader.pages)
        else:
            document = self._decode_page(payload, content_type)
            parser = _VisibleTextParser()
            parser.feed(document)
            parser.close()
            text = "\n".join(dict.fromkeys(block for block in parser.blocks if len(block) >= 2))
        fetched_at = time.time()
        with self._lock:
            self._cache[url] = _CachedPage(fetched_at=fetched_at, text=text)
        return text, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(fetched_at))

    @staticmethod
    def _excerpt(page_text: str, query: str, focus_terms: Iterable[str], limit: int = 1400) -> str:
        raw_segments = re.split(r"\n+|(?<=[。！？；])", page_text)
        segments = [re.sub(r"\s+", " ", item).strip() for item in raw_segments]
        segments = [item for item in segments if 2 <= len(item) <= 650]
        compact_query = re.sub(r"\s+", "", query).lower()
        query_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,6}|[a-z0-9]+", compact_query))
        scored: List[Tuple[float, int, str]] = []
        for index, segment in enumerate(segments):
            lowered = segment.lower()
            focus_score = sum(4.0 + min(len(str(term)), 10) / 10 for term in focus_terms if str(term).lower() in lowered)
            query_score = sum(1.0 for token in query_tokens if token in lowered)
            if focus_score or query_score:
                scored.append((focus_score + query_score, index, segment))
        if not scored:
            return re.sub(r"\s+", " ", page_text).strip()[:limit]
        chosen = sorted(sorted(scored, reverse=True)[:10], key=lambda item: item[1])
        output: List[str] = []
        length = 0
        for _, _, segment in chosen:
            if segment in output:
                continue
            if length + len(segment) > limit and output:
                break
            output.append(segment)
            length += len(segment)
        return "\n".join(output)

    def _fetch_source(self, source: Dict[str, object], query: str) -> Dict[str, object]:
        if source.get("verified_excerpt"):
            page_text = str(source["verified_excerpt"])
            fetched_at = str(source.get("verified_at") or "")
            retrieval_origin = "verified_web_snapshot"
        else:
            page_text, fetched_at = self._fetch(str(source["url"]))
            retrieval_origin = "live_web"
        excerpt = self._excerpt(page_text, query, source.get("focus_terms", []))
        if not excerpt:
            raise ValueError("empty_web_excerpt")
        return {
            "document_id": str(source["source_id"]),
            "chunk_id": "web-" + str(source["source_id"]),
            "title": str(source["title"]),
            "text": excerpt,
            "chunk_type": "web_excerpt",
            "heading_path": (
                "已核验公开网页快照" if retrieval_origin == "verified_web_snapshot"
                else ("官网实时检索" if source["route"] == "official" else "公开网络实时检索")
            ),
            "page_number": None,
            "tags": [retrieval_origin, str(source["route"])],
            "authority_tier": str(source["authority_tier"]),
            "assertion_policy": str(source["assertion_policy"]),
            "source_url": str(source["url"]),
            "cohort": None,
            "academic_year": None,
            "student_level": "all",
            "major": None,
            "campus": "Hainan" if "hainan" in str(source["source_id"]) or "lingshui" in str(source["source_id"]) else None,
            "uploaded_at": None,
            "published_at": source.get("published_at"),
            "effective_from": None,
            "date_status": str(source["date_status"]),
            "uncertainty": list(source.get("uncertainty", [])),
            "retrieval_origin": retrieval_origin,
            "web_source_kind": str(source["route"]),
            "fetched_at": fetched_at,
            "score": float(source.get("match_score", 0.0)),
            "score_explanation": {"registry_match": float(source.get("match_score", 0.0))},
        }

    def search(self, query: str, routes: Iterable[str], top_k: int = 4) -> Dict[str, object]:
        requested_routes = list(dict.fromkeys(str(route) for route in routes))
        candidates: List[Dict[str, object]] = []
        for source in self.sources:
            if source.get("route") not in requested_routes:
                continue
            score = self._matches(query, source.get("query_terms", []))
            if score <= 0:
                continue
            candidate = dict(source)
            candidate["match_score"] = round(score, 4)
            candidates.append(candidate)
        candidates.sort(key=lambda item: float(item["match_score"]), reverse=True)
        candidates = candidates[:top_k]
        results: List[Dict[str, object]] = []
        errors: List[str] = []
        # The reviewed registry is deliberately small. Sequential fetches are
        # more reliable through university/corporate proxies, which often drop
        # one of several simultaneous TLS handshakes to Chinese public sites.
        for source in candidates:
            try:
                results.append(self._fetch_source(source, query))
            except Exception as exc:
                errors.append(f"{source['source_id']}:{type(exc).__name__}")
        discovery = self.discovery_provider.search(query, requested_routes, top_k)
        errors.extend(
            f"discovery:{error}" for error in discovery.get("errors", [])
        )
        seen_urls = {str(item.get("source_url", "")) for item in results}
        for item in discovery.get("results", []):
            url = str(item.get("source_url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(item)
        results.sort(
            key=lambda item: (
                1 if item.get("web_source_kind") == "official" else 0,
                float(item.get("score", 0.0)),
            ),
            reverse=True,
        )
        attempted = bool(candidates) or bool(discovery.get("executed"))
        return {
            "executed": bool(candidates) or bool(discovery.get("executed")),
            "provider": self.name,
            "status": "success" if results else ("failed" if attempted else "no_registered_source"),
            "routes": requested_routes,
            "results": results[:top_k],
            "errors": errors,
            "discovery": {key: value for key, value in discovery.items() if key != "results"},
        }

    def status(self) -> Dict[str, object]:
        discovery_status = self.discovery_provider.status()
        return {
            "mode": "registry_plus_discovery" if discovery_status.get("mode") == "live_discovery" else "registry_only",
            "provider": self.name,
            "registered_sources": len(self.sources),
            "allowed_hosts": sorted(self.allowed_hosts),
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "discovery": discovery_status,
        }


def configured_web_retriever() -> WebRetriever:
    return CuratedLiveWebRetriever(discovery_provider=configured_search_discovery())
