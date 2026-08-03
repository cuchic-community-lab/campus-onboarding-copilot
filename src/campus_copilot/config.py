"""Central configuration for XiaohaiGPT backend.

All paths are configurable and auto-detected so the same codebase runs both
locally (D:\\CUC_Files\\26新生网站) and on the 宝塔 production server.

Resolution order for every setting:
1. Environment variable (or .env.local file, loaded lazily by load_env).
2. Auto-detection (site root: walk upward from the repo until a directory
   containing files.json is found; index dir: repo-local data/).
3. Hard-coded default.

No absolute paths are hard-coded; the only constant is the *relative* layout
of a CUCHIC-style site root (files.json / files/ / qa/ / qrcodes.json /
qrcodes/), which is identical on the local machine and on 58.87.99.155.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# .env.local loading (no third-party dependency)
# ---------------------------------------------------------------------------
_ENV_CANDIDATES = (
    Path(os.getenv("CAMPUS_ENV_FILE", "")).resolve() if os.getenv("CAMPUS_ENV_FILE") else None,
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / ".env",
)


def load_env() -> None:
    """Load KEY=VALUE pairs from the first existing candidate file.

    Never overrides variables already present in the process environment, so a
    supervisor/systemd env or a shell export still wins over the file.
    """
    for candidate in _ENV_CANDIDATES:
        if not candidate or not candidate.is_file():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")
        except OSError:
            continue
        break


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Site root detection
# ---------------------------------------------------------------------------
def _is_site_root(path: Path) -> bool:
    return (path / "files.json").is_file()


def detect_site_root() -> Path:
    """Return the CUCHIC-style site root.

    Priority:
    1. CAMPUS_SITE_ROOT / SITE_ROOT env var.
    2. Walk upward from PROJECT_ROOT (up to 4 levels) looking for files.json.
    3. Fall back to PROJECT_ROOT / site (created by start scripts if missing).
    """
    for var in ("CAMPUS_SITE_ROOT", "SITE_ROOT"):
        raw = os.getenv(var, "").strip()
        if raw:
            path = Path(raw).resolve()
            if _is_site_root(path):
                return path
            # Env explicitly set but unusable: still honour it (degraded mode)
            # rather than silently picking a different root.
            return path
    current = PROJECT_ROOT
    for _ in range(4):
        if _is_site_root(current):
            return current
        current = current.parent
    return PROJECT_ROOT / "site"


SITE_ROOT = detect_site_root()

# ---------------------------------------------------------------------------
# Index / data locations (repo-local, shared between local & prod)
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.getenv("CAMPUS_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
INDEX_DIR = DATA_DIR / "index"
DB_PATH = INDEX_DIR / "knowledge.db"
PROCESSED_DIR = DATA_DIR / "processed"

# ---------------------------------------------------------------------------
# Site layout (relative to SITE_ROOT)
# ---------------------------------------------------------------------------
FILES_JSON = Path(os.getenv("CAMPUS_FILES_JSON", str(SITE_ROOT / "files.json"))).resolve()
FILES_DIR = Path(os.getenv("CAMPUS_FILES_DIR", str(SITE_ROOT / "files"))).resolve()
QA_DIR = Path(os.getenv("CAMPUS_QA_DIR", str(SITE_ROOT / "qa"))).resolve()
QRCODES_JSON = Path(os.getenv("CAMPUS_QRCODES_JSON", str(SITE_ROOT / "qrcodes.json"))).resolve()
QRCODES_DIR = Path(os.getenv("CAMPUS_QRCODES_DIR", str(SITE_ROOT / "qrcodes"))).resolve()
SENIOR_DIR = Path(os.getenv("CAMPUS_SENIOR_DIR", str(DATA_DIR / "senior"))).resolve()
SENIOR_REPLIES_DIR = Path(os.getenv("CAMPUS_SENIOR_REPLIES_DIR", str(SITE_ROOT / "senior_replies"))).resolve()

# ---------------------------------------------------------------------------
# RAG / retrieval / answerability tuning
# ---------------------------------------------------------------------------
AUTO_POLL_SECONDS = _env_int("CAMPUS_AUTO_POLL_SECONDS", 60)
AI_ASSISTED_CHUNKING = _env_bool("AI_ASSISTED_CHUNKING", False)
ANSWERABILITY_SCORE_FLOOR = float(os.getenv("ANSWERABILITY_SCORE_FLOOR", "0.06"))
ANSWERABILITY_COVERAGE_FLOOR = float(os.getenv("ANSWERABILITY_COVERAGE_FLOOR", "0.22"))
ANSWERABILITY_MIN_EVIDENCE = _env_int("ANSWERABILITY_MIN_EVIDENCE", 2)

# ---------------------------------------------------------------------------
# Senior Q&A
# ---------------------------------------------------------------------------
SENIOR_ADMIN_TOKEN = os.getenv("SENIOR_ADMIN_TOKEN", "dev-senior-token")

# ---------------------------------------------------------------------------
# LLM provider (Makers preset; same contract as the prototype)
# ---------------------------------------------------------------------------
USER_AGENT = "CampusOnboardingCopilot/0.2 (+corpus synchronization)"

# Optional web search for out-of-corpus questions (v1.4). Empty = disabled.
# Provider: "serper" (default, uses SEARCH_API_KEY) or "bocha" (博查 AI 搜索,
# 国内服务、Bing 兼容格式, uses BOCHA_API_KEY). Keys come from environment or .env.local.
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "serper").strip().lower()
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "").strip()
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "").strip()


def describe_corpus() -> dict:
    """Report which site pieces are present (used by /api/health)."""
    return {
        "site_root": str(SITE_ROOT),
        "files_json_exists": FILES_JSON.is_file(),
        "files_dir_exists": FILES_DIR.is_dir(),
        "qa_dir_exists": QA_DIR.is_dir(),
        "qrcodes_json_exists": QRCODES_JSON.is_file(),
        "qrcodes_dir_exists": QRCODES_DIR.is_dir(),
    }


def ensure_dirs() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    SENIOR_DIR.mkdir(parents=True, exist_ok=True)
