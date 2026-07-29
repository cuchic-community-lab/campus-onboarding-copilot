import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .config import FILES_MANIFEST_URL, HOME_URL, QA_URL, RAW_DIR, RAW_FILES_DIR, USER_AGENT


def _request(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def _safe_file_target(relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/")
    name = Path(normalized).name
    if not name or name in {".", ".."}:
        raise ValueError("unsafe manifest path")
    return RAW_FILES_DIR / name


def sync_corpus(download_files: bool = False) -> Dict[str, object]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest_body = _request(FILES_MANIFEST_URL)
    home_body = _request(HOME_URL)
    qa_body = _request(QA_URL)
    _write_atomic(RAW_DIR / "files.json", manifest_body)
    _write_atomic(RAW_DIR / "index.html", home_body)
    _write_atomic(RAW_DIR / "qa-response.json", qa_body)

    manifest = json.loads(manifest_body.decode("utf-8"))
    downloaded: List[Dict[str, object]] = []
    errors: List[Dict[str, str]] = []
    if download_files:
        RAW_FILES_DIR.mkdir(parents=True, exist_ok=True)
        for item in manifest.get("files", []):
            try:
                relative = str(item["path"]).replace("\\", "/")
                url = urllib.parse.urljoin(HOME_URL, urllib.parse.quote(relative, safe="/"))
                target = _safe_file_target(relative)
                body = _request(url)
                _write_atomic(target, body)
                downloaded.append({
                    "name": item.get("name"),
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                })
            except Exception as exc:
                errors.append({"name": str(item.get("name", "unknown")), "error": str(exc)})

    report = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "manifest_files": len(manifest.get("files", [])),
        "manifest_links": len(manifest.get("links", [])),
        "downloaded_files": downloaded,
        "errors": errors,
    }
    _write_atomic(RAW_DIR.parent / "sync-report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    return report
