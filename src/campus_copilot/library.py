"""Public, read-only view of the original student resource library."""

import json
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, unquote

from .config import RAW_DIR, RAW_FILES_DIR
from .extractors import load_faq_units


def _manifest() -> Dict[str, object]:
    return json.loads((RAW_DIR / "files.json").read_text(encoding="utf-8"))


def _visible_files(manifest: Dict[str, object]) -> List[Dict[str, object]]:
    return [item for item in manifest.get("files", []) if item.get("visible", True)]


def library_payload() -> Dict[str, object]:
    """Return only the public fields needed by the fused library frontend."""
    manifest = _manifest()
    files = []
    for item in _visible_files(manifest):
        name = str(item.get("name", ""))
        files.append({
            "name": name,
            "url": "/files/" + quote(name, safe=""),
            "type": str(item.get("type", "unknown")),
            "size_kb": float(item.get("sizeKB", 0) or 0),
            "tags": [str(tag) for tag in item.get("tags", [])],
            "description": str(item.get("desc", "")),
            "pinned": bool(item.get("pinned", False)),
            "priority": int(item.get("priority", 0) or 0),
            "uploaded_at": item.get("uploadTime"),
        })

    links = []
    for item in manifest.get("links", []):
        if not item.get("visible", True):
            continue
        links.append({
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "tags": [str(tag) for tag in item.get("tags", [])],
            "description": str(item.get("desc", "")),
            "pinned": bool(item.get("pinned", False)),
            "priority": int(item.get("priority", 0) or 0),
        })

    _, faq_units = load_faq_units()
    questions = [{
        "category": unit["category"],
        "question": unit["question"],
        "answer": unit["answer"],
    } for unit in faq_units]

    tag_counts: Dict[str, int] = {}
    for item in files + links:
        for tag in item["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "title": "中传海南一站通",
        "subtitle": "学生共建的入学资料库",
        "disclaimer": "学生自建资料库，经验内容不代表学校官方表述；政策与安排请结合来源日期并以学校最新通知为准。",
        "files": sorted(files, key=lambda item: (-item["pinned"], -item["priority"], item["name"])),
        "links": sorted(links, key=lambda item: (-item["pinned"], -item["priority"], item["title"])),
        "questions": questions,
        "tags": [{"name": name, "count": count} for name, count in sorted(tag_counts.items(), key=lambda pair: (-pair[1], pair[0]))],
        "counts": {"files": len(files), "links": len(links), "questions": len(questions)},
    }


def resolve_library_file(url_path: str) -> Optional[Path]:
    """Resolve a manifest-listed public file; reject traversal and hidden files."""
    requested = Path(unquote(url_path)).name
    if not requested or requested != unquote(url_path):
        return None
    manifest = _manifest()
    allowed = {str(item.get("name", "")) for item in _visible_files(manifest)}
    if requested not in allowed:
        return None
    candidate = (RAW_FILES_DIR / requested).resolve()
    if candidate.parent != RAW_FILES_DIR.resolve() or not candidate.is_file():
        return None
    return candidate
