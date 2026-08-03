"""Optional LLM-assisted chunk enhancement (AI_ASSISTED_CHUNKING=1).

When enabled and a provider credential is configured, each rule-chunked unit
gets a short LLM-generated tag/summary pass before indexing. This is strictly
optional: the default rule-based chunker is fully self-contained and correct;
this module only *augments* search signals (tags) and never becomes a source
of truth for answers.

Failure is always silent — the rule-chunked data is used unchanged, so a
missing key, provider outage, or slow model can never block indexing.
"""

import json
import os
import threading
from typing import Dict, List

from .composition import ProviderConfig
from .models import ChunkRecord

_TAG_LOCK = threading.Lock()
_CACHE: Dict[str, List[str]] = {}


def _llm_tags(chunk: ChunkRecord) -> List[str]:
    """Ask the configured chat model for 3-6 short Chinese tags for a chunk."""
    config = ProviderConfig.from_env()
    if not config.enabled:
        return []
    key = chunk.content_hash
    with _TAG_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    system = (
        "你是资料切分助手。请为下面的资料片段生成 3-6 个中文标签，只输出 JSON 数组字符串，"
        "例如 [\"选课\",\"学分\",\"步骤\"]。不要解释，不要多余文字。"
    )
    payload = json.dumps({
        "model": config.model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": chunk.text[:800]},
        ],
    }, ensure_ascii=False).encode("utf-8")
    from urllib import request
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    outbound = request.Request(
        f"{config.base_url}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(outbound, timeout=config.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            return []
        tags = [str(t).strip().strip('"').strip("'") for t in json.loads(content[start:end + 1])]
        tags = [t for t in tags if t][:8]
        with _TAG_LOCK:
            _CACHE[key] = tags
        return tags
    except Exception:
        return []


def enhance_chunks(chunks: List[ChunkRecord], max_items: int = 400) -> Dict[str, object]:
    """Augment chunk tags via the configured LLM. Silent on any failure."""
    enhanced = 0
    for chunk in chunks:
        tags = _llm_tags(chunk)
        if tags:
            existing = set(chunk.tags or [])
            chunk.tags = list(existing | set(tags))
            enhanced += 1
        max_items -= 1
        if max_items <= 0:
            break
    return {"enabled": True, "enhanced_chunks": enhanced, "total": len(chunks)}
