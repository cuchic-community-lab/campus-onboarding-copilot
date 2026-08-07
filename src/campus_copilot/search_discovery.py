import json
import hashlib
import os
import re
import time
from typing import Dict, Iterable, List, Protocol
from urllib import request
from urllib.parse import urlparse


TAVILY_ENDPOINT = "https://api.tavily.com/search"
OFFICIAL_SEARCH_DOMAINS = ("cuc.edu.cn",)
MAX_DISCOVERY_TEXT = 1800


class SearchDiscoveryProvider(Protocol):
    name: str

    def search(self, query: str, routes: Iterable[str], top_k: int) -> Dict[str, object]:
        ...

    def status(self) -> Dict[str, object]:
        ...


class NullSearchDiscovery:
    name = "disabled"

    def search(self, query: str, routes: Iterable[str], top_k: int) -> Dict[str, object]:
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


def sanitize_search_query(query: str, limit: int = 240) -> str:
    cleaned = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已省略]", query)
    cleaned = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已省略]", cleaned)
    cleaned = re.sub(r"(?<!\d)\d{12,18}[Xx]?(?!\d)", "[编号已省略]", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()[:limit]


def _official_host(host: str) -> bool:
    lowered = host.lower().strip(".")
    return any(lowered == domain or lowered.endswith("." + domain) for domain in OFFICIAL_SEARCH_DOMAINS)


def _safe_result_url(url: str) -> bool:
    parsed = urlparse(url)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or host == "localhost":
        return False
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host):
        return False
    return not host.endswith((".local", ".internal"))


class TavilySearchDiscovery:
    name = "tavily"

    def __init__(self, api_key: str, timeout_seconds: float = 12.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _search_query(query: str, route: str) -> str:
        prefix = "中国传媒大学 海南国际学院"
        return f"{prefix} {sanitize_search_query(query)}".strip()

    def _request(self, query: str, route: str, top_k: int) -> List[Dict[str, object]]:
        payload: Dict[str, object] = {
            "api_key": self.api_key,
            "query": self._search_query(query, route),
            "search_depth": "basic",
            "max_results": max(1, min(top_k, 5)),
            "include_answer": False,
            "include_raw_content": True,
        }
        if route == "official":
            payload["include_domains"] = list(OFFICIAL_SEARCH_DOMAINS)
        outbound = request.Request(
            TAVILY_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(outbound, timeout=self.timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        return list(value.get("results", []))

    @staticmethod
    def _to_evidence(item: Dict[str, object], route: str, index: int) -> Dict[str, object]:
        url = str(item.get("url", ""))
        host = str(urlparse(url).hostname or "").lower()
        official = _official_host(host)
        authority = "official_web" if official else "public_web"
        content = str(item.get("content") or item.get("raw_content") or "")
        text = re.sub(r"\s+", " ", content).strip()[:MAX_DISCOVERY_TEXT]
        fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
        url_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return {
            "document_id": f"discovered-{url_id}",
            "chunk_id": f"search-{index}-{url_id}",
            "title": str(item.get("title") or host or "公开网页"),
            "text": text,
            "chunk_type": "search_excerpt",
            "heading_path": "自主官网搜索" if official else "自主公开网络搜索",
            "page_number": None,
            "tags": ["autonomous_search", "official" if official else "public"],
            "authority_tier": authority,
            "assertion_policy": "assert_with_search_citation" if official else "cite_as_public_reference",
            "source_url": url,
            "cohort": None,
            "academic_year": None,
            "student_level": "all",
            "major": None,
            "campus": None,
            "uploaded_at": None,
            "published_at": None,
            "effective_from": None,
            "date_status": "live_search_unverified",
            "uncertainty": [
                "该页面由实时搜索发现，发布时间和当前适用性尚未结构化核验"
            ],
            "retrieval_origin": "autonomous_search",
            "web_source_kind": "official" if official else "public",
            "fetched_at": fetched_at,
            "score": float(item.get("score") or 0.0),
            "score_explanation": {"search_provider_score": float(item.get("score") or 0.0)},
        }

    def search(self, query: str, routes: Iterable[str], top_k: int = 4) -> Dict[str, object]:
        requested_routes = list(dict.fromkeys(str(route) for route in routes))
        results: List[Dict[str, object]] = []
        errors: List[str] = []
        seen = set()
        for route in requested_routes:
            try:
                provider_results = self._request(query, route, top_k)
            except Exception as exc:
                errors.append(f"{route}:{type(exc).__name__}")
                continue
            for item in provider_results:
                url = str(item.get("url", ""))
                if not _safe_result_url(url) or url in seen:
                    continue
                if float(item.get("score") or 0.0) < 0.2:
                    continue
                evidence = self._to_evidence(item, route, len(results) + 1)
                if not evidence["text"]:
                    continue
                if route == "official" and evidence["authority_tier"] != "official_web":
                    continue
                seen.add(url)
                results.append(evidence)
        results.sort(
            key=lambda item: (
                1 if item.get("authority_tier") == "official_web" else 0,
                float(item.get("score", 0.0)),
            ),
            reverse=True,
        )
        return {
            "executed": True,
            "provider": self.name,
            "status": "success" if results else "failed",
            "routes": requested_routes,
            "results": results[:top_k],
            "errors": errors,
        }

    def status(self) -> Dict[str, object]:
        return {
            "mode": "live_discovery",
            "provider": self.name,
            "official_domains": list(OFFICIAL_SEARCH_DOMAINS),
            "credential_configured": bool(self.api_key),
        }


def configured_search_discovery() -> SearchDiscoveryProvider:
    provider = os.getenv("CAMPUS_WEB_SEARCH_PROVIDER", "").strip().lower()
    api_key = os.getenv("CAMPUS_WEB_SEARCH_API_KEY", "").strip()
    if provider == "tavily" and api_key:
        return TavilySearchDiscovery(
            api_key,
            timeout_seconds=float(os.getenv("CAMPUS_WEB_SEARCH_TIMEOUT", "12")),
        )
    return NullSearchDiscovery()
