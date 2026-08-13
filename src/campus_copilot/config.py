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

import json
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

# 全局对话自动存档（T3）：data/logs/<session_id>.md，按会话分文件追加写。
# 仅写入，绝不参与会话清空（T1 只清内存 SessionStore）。
DIALOG_LOG_DIR = Path(os.getenv("CAMPUS_DIALOG_LOG_DIR", str(DATA_DIR / "logs"))).resolve()

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

# 生成链路架构（v1.7）：检索负责找、LLM 负责判。
# LLM_FREE_ANSWER=1（默认，用户已确认上线）= 新链路：检索降为候选召回（top_k 8）、
# 拆除 can_generate 硬阈值、LLM 一次输出 answer+软 judgment（answer/refuse/need_web）、
# 引用校验只保留 unknown_citation 防编造、联网由 LLM 意图+空结果硬触发兜底。
# =0 = 旧链路（现状，保留回退）：五重阈值 + 固定模板 + 8 条强制校验。
LLM_FREE_ANSWER = _env_bool("LLM_FREE_ANSWER", True)
# L3 可选（默认关）：高危领域（学费/政策/证件/报到）强制至少 1 条引用且引用出现在
# answer——仅在命中 question_type 时启用，非全局规则，留作灰度期收紧用。
FREE_ANSWER_MIN_REFERENCE = _env_bool("FREE_ANSWER_MIN_REFERENCE", False)
FREE_ANSWER_MIN_REFERENCE_TYPES = tuple(
    t.strip() for t in os.getenv("FREE_ANSWER_MIN_REFERENCE_TYPES", "fee,policy,identity,registration").split(",") if t.strip()
)

# ---------------------------------------------------------------------------
# 术语表注入（v1.5）
# ---------------------------------------------------------------------------
# TERMS_ENABLED=1（默认）开启术语表功能：生成层注入【校园语境】+ 检索层 query 改写；
# =0 完全关闭（load_glossary 返回空表，改写与注入均跳过）。
# 术语表文件：config/terms.json（用户可自行维护，缺失/损坏返回空表不报错）。
TERMS_ENABLED = _env_bool("TERMS_ENABLED", True)
GLOSSARY_PATH = Path(os.getenv("CAMPUS_GLOSSARY_PATH", str(PROJECT_ROOT / "config" / "terms.json"))).resolve()


def load_glossary() -> list:
    """Load config/terms.json into a list of term dicts.

    Robust by design: missing file, invalid JSON, or malformed entries all
    degrade to an empty list (no exception) so the server keeps running even
    when the user edits the file into a broken state. Keys starting with "_"
    are treated as comments and ignored. Called per request so manual edits
    take effect without a restart.
    """
    if not TERMS_ENABLED:
        return []
    try:
        raw = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    entries = raw.get("glossary")
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        term = entry.get("term")
        expansion = entry.get("expansion")
        if not isinstance(term, str) or not term or not isinstance(expansion, str) or not expansion:
            continue
        result.append({
            "term": term,
            "expansion": expansion,
            "note": entry.get("note", "") if isinstance(entry.get("note", ""), str) else "",
            "rewrite": bool(entry.get("rewrite", False)),
        })
    return result

# ---------------------------------------------------------------------------
# Senior Q&A
# ---------------------------------------------------------------------------
SENIOR_ADMIN_TOKEN = os.getenv("SENIOR_ADMIN_TOKEN", "dev-senior-token")


def admin_token() -> str:
    """运行时读取 SENIOR_ADMIN_TOKEN（须在 load_env() 之后调用才读到 .env.local）。

    仿照 search_settings()：模块级常量在 import 时固化，.env.local 懒加载后不会
    刷新，导致服务器配了 token 仍走默认值 401。管理端点鉴权一律走本函数；
    未配置时与模块级默认一致（dev-senior-token），保持向后兼容。
    """
    return os.getenv("SENIOR_ADMIN_TOKEN", "dev-senior-token")

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

# 网站优先级搜索（v1.4，仅 bocha provider 生效）：
# 分号 ; 分隔优先级组，组内用 | 或 , 分隔域名，最多 3 组；空组 = 该级不限制（全网兜底）。
# 搜索时按组逐级降级：先只搜第 1 组（官方权威），结果不足再并入第 2 组，仍不足则全网。
# 默认值覆盖中传官方三件套（P1）+ 微信公众号（P2）+ 全网（P3 空）。
SEARCH_PRIORITY_SITES = os.getenv(
    "SEARCH_PRIORITY_SITES",
    "hainan.cuc.edu.cn|cuc.edu.cn|hnzs.cuc.edu.cn;mp.weixin.qq.com;",
).strip()

# P1/P2 结果相关性门槛（v1.6）：对每条结果计算 query 核心词在 title+snippet 中的
# 命中率（去停用词），平均命中率低于该阈值、或 top1 完全不相关时，继续降级到下一
# 级（p1→p2→web），避免官方域硬凑不相关结果。0~1，默认 0.3，可微调。
SEARCH_P1_MIN_RELEVANCE = float(os.getenv("SEARCH_P1_MIN_RELEVANCE", "0.3"))

# v1.9：逐条相关性过滤（开关 + 阈值）。SEARCH_RELEVANCE_FILTER=1（默认）时，每级
# 搜索结果逐条做 query 核心词命中过滤：title+snippet 命中核心词数 <
# SEARCH_MIN_KEYWORD_HITS 的结果丢弃；query 含地域词（陵水/黎安/海南/三亚等）时结果
# 还必须命中该地域词，直接杀掉"陵县"（山东）这类近似词。0 关闭（回退 v1.6 平均门槛）。
SEARCH_RELEVANCE_FILTER = int(os.getenv("SEARCH_RELEVANCE_FILTER", "1")) != 0
SEARCH_MIN_KEYWORD_HITS = max(1, int(os.getenv("SEARCH_MIN_KEYWORD_HITS", "1")))


def search_settings() -> dict:
    """Runtime snapshot of web-search settings.

    Reads the environment at call time so values from .env.local (loaded lazily
    by load_env()) are honoured. The module-level constants above are kept for
    backward compatibility but can be stale before load_env() runs; callers that
    need the active configuration should prefer this function.
    """
    return {
        "provider": os.getenv("SEARCH_PROVIDER", "serper").strip().lower(),
        "serper_key": os.getenv("SEARCH_API_KEY", "").strip(),
        "bocha_key": os.getenv("BOCHA_API_KEY", "").strip(),
        "priority_sites": os.getenv(
            "SEARCH_PRIORITY_SITES",
            "hainan.cuc.edu.cn|cuc.edu.cn|hnzs.cuc.edu.cn;mp.weixin.qq.com;",
        ).strip(),
        "p1_min_relevance": float(os.getenv("SEARCH_P1_MIN_RELEVANCE", "0.3")),
        "relevance_filter": int(os.getenv("SEARCH_RELEVANCE_FILTER", "1")) != 0,
        "min_keyword_hits": max(1, int(os.getenv("SEARCH_MIN_KEYWORD_HITS", "1"))),
    }


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
    DIALOG_LOG_DIR.mkdir(parents=True, exist_ok=True)
