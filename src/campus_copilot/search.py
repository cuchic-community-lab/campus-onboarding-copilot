"""Optional web search for out-of-corpus questions (v1.4).

Zero third-party dependency: uses only the stdlib (urllib). Two providers:

1. Serper.dev (https://google.serper.dev/search) — default. OpenAI-style HTTP
   endpoint that accepts POST JSON and an API key (SEARCH_API_KEY).
2. 博查 AI 搜索 (https://api.bochaai.com/v1/web-search) — 国内服务，Bing 兼容
   响应格式，API key 为 BOCHA_API_KEY。通过 SEARCH_PROVIDER=bocha 启用。

The active provider is chosen by SEARCH_PROVIDER at import time. Disabled when
the selected provider's key is empty/missing (default). All failures collapse
to None so callers can fall back to the soft refusal without exception handling.
"""

import json
from typing import Dict, List, Optional
from urllib import request
from urllib.error import HTTPError, URLError

from .config import BOCHA_API_KEY, SEARCH_API_KEY, SEARCH_PROVIDER

SERPER_ENDPOINT = "https://google.serper.dev/search"
BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
_SEARCH_TIMEOUT = 8.0
_MAX_RESULTS = 5


def search_web(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[List[Dict[str, str]]]:
    """Search the web via the configured provider; returns [{title, link, snippet}] or None.

    None means "disabled or failed": the caller should keep the soft refusal.
    Never raises; network/parse errors are collapsed into None.
    """
    provider = (SEARCH_PROVIDER or "serper").strip().lower()
    if provider == "bocha":
        return _search_bocha(query, max_results=max_results, timeout=timeout)
    return _search_serper(query, max_results=max_results, timeout=timeout)


def _search_serper(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[List[Dict[str, str]]]:
    api_key = (SEARCH_API_KEY or "").strip()
    if not api_key:
        return None
    payload = json.dumps(
        {"q": query, "num": max(1, min(int(max_results), 10))},
        ensure_ascii=False,
    ).encode("utf-8")
    outbound = request.Request(
        SERPER_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": api_key,
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(outbound, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None

    results: List[Dict[str, str]] = []
    for item in data.get("organic", []) or []:
        title = str(item.get("title") or "").strip()
        link = str(item.get("link") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title or not link:
            continue
        results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results or None


def _search_bocha(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[List[Dict[str, str]]]:
    api_key = (BOCHA_API_KEY or "").strip()
    if not api_key:
        return None
    payload = json.dumps(
        {
            "query": query,
            "count": max(1, min(int(max_results), 5)),
            "freshness": "noLimit",
            "summary": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    outbound = request.Request(
        BOCHA_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(outbound, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None

    # Bing-compatible response: data.webPages.value[] with name/url/snippet.
    results: List[Dict[str, str]] = []
    web_pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    for item in web_pages:
        title = str(item.get("name") or "").strip()
        link = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title or not link:
            continue
        results.append({"title": title, "link": link, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results or None
