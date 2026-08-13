"""Optional web search for out-of-corpus questions (v1.4).

Zero third-party dependency: uses only the stdlib (urllib). Two providers:

1. Serper.dev (https://google.serper.dev/search) — default. OpenAI-style HTTP
   endpoint that accepts POST JSON and an API key (SEARCH_API_KEY).
2. 博查 AI 搜索 (https://api.bochaai.com/v1/web-search) — 国内服务，Bing 兼容
   响应格式，API key 为 BOCHA_API_KEY。通过 SEARCH_PROVIDER=bocha 启用。

Site-priority search (v1.4): when SEARCH_PRIORITY_SITES is configured and the
active provider is bocha, queries run in a staged fallback chain:
  Stage p1 — `include` restricted to the first priority group (official sites);
  Stage p2 — `include` widened to group 1 + group 2 (official + WeChat);
  Stage web — no `include`, full web.
Each result is tagged with a `site_tier` (official/wechat/web) derived from the
link host. search_web_tiered() additionally reports which stage was used.

The active provider is chosen by SEARCH_PROVIDER at import time. Disabled when
the selected provider's key is empty/missing (default). All failures collapse
to None so callers can fall back to the soft refusal without exception handling.
"""

import json
import re
from typing import Dict, List, Optional
from urllib import request
from urllib.error import HTTPError, URLError

from .config import search_settings

SERPER_ENDPOINT = "https://google.serper.dev/search"
BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
_SEARCH_TIMEOUT = 8.0
_MAX_RESULTS = 5

# site_tier labels (contract v1.4)
TIER_OFFICIAL = "official"
TIER_WECHAT = "wechat"
TIER_WEB = "web"
_TIER_ORDER = {TIER_OFFICIAL: 0, TIER_WECHAT: 1, TIER_WEB: 2}

# Query tokens that carry no retrieval meaning for web-result relevance scoring
# (v1.6, T3). They are dropped before computing keyword hit-rates so "多少/
# 怎么/什么" do not inflate the score of an otherwise unrelated result.
_SEARCH_STOP_TERMS = frozenset({
    "多少", "怎么", "如何", "什么", "为什么", "是否", "能不能", "可以吗", "怎么办",
    "哪里", "哪个", "哪些", "几号", "几点", "多久", "有没有", "是不是", "一个",
    "这个", "那个", "的", "了", "啊", "吧", "吗", "呢", "请问", "我想", "知道",
    "要求", "安排", "需要", "应该", "一下", "什么时间", "什么条件", "注意事项",
    "请", "怎么坐", "怎么去", "请问一下",
})

# 地域锚定词（v1.9）：query 命中这些词时，结果必须也命中（多个地域词全部命中，
# 避免"三亚到北京航班"被仅含"北京"的无关新闻放行），否则丢弃。白名单覆盖本项目
# 语境常见地域（海南陵水黎安试验区 + 三亚等），配合行政区划后缀模式兜底（XX省/
# 市/县/镇/乡/岛/湾，不含泛化"区"以免把"试验区"误当地名）。目的是杀掉"陵水"→
# "陵县"这类近似词与"三亚航班"→"飞机能上网"这类硬凑结果。
_GEO_TERMS = frozenset({
    "海南", "陵水", "黎安", "三亚", "海口", "儋州", "文昌", "琼海", "万宁",
    "五指山", "北京", "上海", "广州", "深圳", "天津", "重庆",
})
# 行政区划后缀：捕获"陵水县""三亚市""海南省"等白名单未覆盖的地名。
_GEO_SUFFIX_RE = re.compile(r"[\u4e00-\u9fff]{2,4}(?:省|市|县|镇|乡|岛|湾)")


def parse_priority_sites(raw: str) -> List[List[str]]:
    """Parse SEARCH_PRIORITY_SITES into priority groups.

    Groups are separated by ';', domains inside a group by '|' or ','. At most
    three groups are kept; empty groups (e.g. a trailing ';' for the full-web
    fallback) are dropped.

    Example: "a.cn|b.cn;mp.weixin.qq.com;" ->
             [["a.cn", "b.cn"], ["mp.weixin.qq.com"]]
    """
    groups: List[List[str]] = []
    for group_raw in (raw or "").split(";"):
        domains = [
            d.strip().lower().lstrip("*.")
            for d in re.split(r"[|,]", group_raw)
            if d.strip()
        ]
        if not domains:
            continue
        groups.append(domains)
        if len(groups) >= 3:
            break
    return groups


def _extract_host(url: str) -> str:
    """Return the lowercased host of a URL (no scheme/port/path)."""
    stripped = (url or "").strip()
    if "://" in stripped:
        stripped = stripped.split("://", 1)[1]
    host_port = stripped.split("/", 1)[0]
    return host_port.split(":", 1)[0].strip().lower()


def _host_matches(host: str, domains: List[str]) -> bool:
    """True if host equals a domain or is a subdomain of it."""
    if not host:
        return False
    for domain in domains:
        domain = (domain or "").strip().lower().lstrip("*.")
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _site_tier_for(link: str, groups: List[List[str]]) -> str:
    """Classify a result by priority groups: official / wechat / web."""
    host = _extract_host(link)
    if groups and _host_matches(host, groups[0]):
        return TIER_OFFICIAL
    if len(groups) > 1 and _host_matches(host, groups[1]):
        return TIER_WECHAT
    return TIER_WEB


def _tag_results(
    results: List[Dict[str, str]], groups: Optional[List[List[str]]]
) -> List[Dict[str, str]]:
    """Attach a site_tier field to each result (no-op when no groups)."""
    if not groups:
        return results
    for item in results:
        item["site_tier"] = _site_tier_for(str(item.get("link") or ""), groups)
    return results


def _query_keywords(
    query: str, max_keywords: int = 20, include_bigrams: bool = True
) -> List[str]:
    """Extract search keywords from a query for lightweight relevance scoring (v1.6/v1.9).

    Combines glossary terms, CONCEPT_TERMS whitelist hits, latin tokens, and
    Chinese n-gram segments (3-gram, plus 2-gram when include_bigrams), with stop
    words removed. Longer terms come first (a 3-gram like 摆渡车 outweighs the
    1-char pieces of the full-name expansion). Returns [] when there is nothing
    to judge — callers treat that as "cannot filter, keep the stage".

    v1.9: include_bigrams=True (default) adds 2-grams so 2-char words like
    天气/航班/新闻/宿舍 are captured for per-result filtering; the average
    relevance gate (_results_relevant) keeps include_bigrams=False to preserve
    the v1.6 scoring baseline (3-gram only) and avoid dilution.
    """
    lowered = query.lower()
    keywords: List[str] = []
    try:
        from .config import load_glossary
        for entry in load_glossary():
            term = entry.get("term", "")
            if term and term in lowered:
                keywords.append(term)
    except Exception:
        pass
    try:
        from .retrieval import CONCEPT_TERMS
        for term in CONCEPT_TERMS:
            if len(term) >= 2 and term in lowered:
                keywords.append(term)
    except Exception:
        pass
    for token in re.findall(r"[a-z0-9]+", lowered):
        if len(token) >= 2 and token not in _SEARCH_STOP_TERMS:
            keywords.append(token)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        # 整词（LLM 改写后的空格分词单 token，如"三亚""航班"）：始终加入，是强信号。
        if len(run) >= 2 and run not in _SEARCH_STOP_TERMS:
            keywords.append(run)
        if include_bigrams:
            for index in range(max(0, len(run) - 1)):
                gram = run[index:index + 2]
                if gram not in _SEARCH_STOP_TERMS:
                    keywords.append(gram)
        for index in range(max(0, len(run) - 2)):
            gram = run[index:index + 3]
            if gram not in _SEARCH_STOP_TERMS:
                keywords.append(gram)
    seen: set = set()
    result: List[str] = []
    for keyword in sorted(keywords, key=len, reverse=True):
        if keyword not in seen:
            seen.add(keyword)
            result.append(keyword)
        if len(result) >= max_keywords:
            break
    return result


def _extract_geo_terms(query: str) -> List[str]:
    """地域锚定词（v1.9）：白名单地名 + 行政区划后缀模式，随后做包含归并——
    若一个地域词是另一个的子串则只保留更长者（"陵水县"⊃"陵水"），避免 AND
    锚定时冗余词导致误伤。"""
    lowered = (query or "").lower()
    raw: List[str] = []
    for term in _GEO_TERMS:
        if term in lowered and term not in raw:
            raw.append(term)
    for matched in _GEO_SUFFIX_RE.findall(lowered):
        if matched not in raw:
            raw.append(matched)
    # 包含归并：删除是其他词子串的词，保留最长。
    geo = [term for term in raw if not any(
        term != other and term in other for other in raw
    )]
    return geo


def _filter_results(
    results: List[Dict[str, object]], query: str, min_hits: int
) -> List[Dict[str, object]]:
    """逐条相关性过滤（v1.9）：结果必须命中 ≥min_hits 个核心词；query 含地域词时
    还必须命中全部地域词（AND，杀掉仅含目的地的无关结果如"北京-成都"航线新闻）。
    无核心词可判时保守保留（不误杀）。"""
    if not results:
        return []
    core = _query_keywords(query, include_bigrams=True)
    geo = _extract_geo_terms(query)
    if not core:
        return results
    kept: List[Dict[str, object]] = []
    for item in results:
        text = " ".join([
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
        ]).lower()
        if not text:
            continue
        if sum(1 for keyword in core if keyword in text) < min_hits:
            continue
        if geo and not all(term in text for term in geo):
            continue
        kept.append(item)
    return kept


def _result_relevance(item: Dict[str, object], keywords: List[str]) -> float:
    """Fraction of query keywords present in a result's title+snippet (0..1)."""
    if not keywords:
        return 1.0
    text = " ".join([
        str(item.get("title") or ""), str(item.get("snippet") or ""),
    ]).lower()
    if not text:
        return 0.0
    hits = sum(1 for keyword in keywords if keyword in text)
    return hits / len(keywords)


def _results_relevant(
    results: List[Dict[str, object]], query: str, min_relevance: float
) -> bool:
    """Lightweight relevance gate for a staged search result (v1.6, T3).

    Returns False (→ degrade to the next stage) when the average keyword
    hit-rate of the stage results is below the threshold, or when the top-1
    result has zero keyword hits (clearly unrelated). No keywords extracted
    ⇒ cannot judge ⇒ keep the stage (conservative, avoids over-degrading).
    """
    keywords = _query_keywords(query, include_bigrams=False)
    if not keywords:
        return True
    scores = [_result_relevance(item, keywords) for item in results]
    if not scores:
        return False
    average = sum(scores) / len(scores)
    return average >= min_relevance and scores[0] > 0.0


def search_web_tiered(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[Dict[str, object]]:
    """Search the web with site-priority fallback; returns {"results", "search_tier_used"}.

    - bocha + SEARCH_PRIORITY_SITES set: staged p1 -> p2 -> web.
    - bocha without priority config: single unrestricted call (site_tier still
      tagged so callers can label sources; search_tier_used is None).
    - serper: legacy behavior, search_tier_used is None, no site_tier fields.

    Returns None when the provider is disabled or every stage failed.
    Never raises; network/parse errors collapse into None.

    v1.9: per-result relevance filtering (SEARCH_RELEVANCE_FILTER) is applied to
    every stage before the tier decision. Each kept result must hit ≥
    SEARCH_MIN_KEYWORD_HITS core keywords and (when the query carries a geo term
    like 陵水/黎安/海南/三亚) must mention that geo term — this drops lookalike
    noise ("陵水" vs "陵县") and P1 hard-fills ("飞机能上网"). Filtered P1/P2
    results below 2 → degrade to the next stage; the web stage returns whatever
    survives (宁缺毋滥, no forced padding).
    """
    settings = search_settings()
    provider = settings["provider"]
    relevance_enabled = bool(settings["relevance_filter"])
    min_keyword_hits = int(settings["min_keyword_hits"])

    def _maybe_filter(
        results: Optional[List[Dict[str, object]]],
    ) -> List[Dict[str, object]]:
        if not relevance_enabled or not results:
            return results or []
        return _filter_results(results, query, min_keyword_hits)

    if provider != "bocha":
        results = _maybe_filter(
            _search_serper(query, max_results=max_results, timeout=timeout)
        )
        if not results:
            return None
        return {"results": results, "search_tier_used": None}

    groups = parse_priority_sites(settings["priority_sites"])
    if not groups:
        results = _maybe_filter(
            _search_bocha(
                query, max_results=max_results, timeout=timeout, groups=None
            )
        )
        if not results:
            return None
        return {"results": results, "search_tier_used": None}

    min_relevance = settings["p1_min_relevance"]

    # Stage 1: official sites only.
    p1_results = _maybe_filter(
        _search_bocha(
            query,
            max_results=max_results,
            timeout=timeout,
            include="|".join(groups[0]),
            groups=groups,
        )
    )
    # v1.6 (T3): P1 相关性门槛——官方域常能硬凑 ≥2 条不相关结果，导致 p2/web
    # 降级永不触发。现在对 P1 结果做轻量关键词命中率评分，平均低于阈值或 top1
    # 不相关则继续降级；无关键词可判定时不拦截（保守）。
    # v1.9: 结果已先逐条过滤（每条命中核心词+地域词），过滤后 <2 条 → 直接降级。
    if p1_results and len(p1_results) >= 2 and _results_relevant(p1_results, query, min_relevance):
        return {"results": p1_results, "search_tier_used": "p1"}

    # Stage 2: official + WeChat.
    p2_results: List[Dict[str, object]] = []
    if len(groups) > 1:
        stage2 = [domain for group in groups[:2] for domain in group]
        p2_results = _maybe_filter(
            _search_bocha(
                query,
                max_results=max_results,
                timeout=timeout,
                include="|".join(stage2),
                groups=groups,
            )
        )
        if p2_results and len(p2_results) >= 2 and _results_relevant(p2_results, query, min_relevance):
            return {"results": p2_results, "search_tier_used": "p2"}

    # Stage 3: full web (last resort). 过滤后哪怕 1 条也返回（宁缺毋滥，不强行凑）。
    web_results = _maybe_filter(
        _search_bocha(
            query, max_results=max_results, timeout=timeout, include=None, groups=groups
        )
    )
    if web_results:
        return {"results": web_results, "search_tier_used": "web"}

    # Fall back to whatever an earlier stage produced (at least partial answers).
    if p2_results:
        return {"results": p2_results, "search_tier_used": "p2"}
    if p1_results:
        return {"results": p1_results, "search_tier_used": "p1"}
    return None


def search_web(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[List[Dict[str, str]]]:
    """Legacy entry point; returns [{title, link, snippet, site_tier?}] or None.

    Kept for backward compatibility: internally runs the tiered search and
    returns only the result list (each item may carry an extra site_tier key
    when the provider is bocha).
    """
    tiered = search_web_tiered(query, max_results=max_results, timeout=timeout)
    if not tiered:
        return None
    return tiered["results"]  # type: ignore[return-value]


def sort_by_tier(results: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Stable-sort results so official sources come first (wechat, then web)."""
    return sorted(
        results,
        key=lambda item: _TIER_ORDER.get(str(item.get("site_tier") or TIER_WEB), 2),
    )


def _search_serper(
    query: str,
    max_results: int = _MAX_RESULTS,
    timeout: float = _SEARCH_TIMEOUT,
) -> Optional[List[Dict[str, str]]]:
    api_key = (search_settings()["serper_key"] or "").strip()
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
    include: Optional[str] = None,
    groups: Optional[List[List[str]]] = None,
) -> Optional[List[Dict[str, str]]]:
    api_key = (search_settings()["bocha_key"] or "").strip()
    if not api_key:
        return None
    payload: Dict[str, object] = {
        "query": query,
        "count": max(1, min(int(max_results), 5)),
        "freshness": "noLimit",
        "summary": True,
    }
    if include:
        payload["include"] = include
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    outbound = request.Request(
        BOCHA_ENDPOINT,
        data=body,
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
    return _tag_results(results, groups) or None
