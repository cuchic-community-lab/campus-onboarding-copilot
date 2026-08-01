"""Fail closed when tracked files contain local artifacts or likely secrets."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROHIBITED = (
    ".env.local",
    "data/processed/",
    "data/index/",
    "data/official_sites/",
)
SENSITIVE_LIBRARY_FILES = {
    "data/raw/files/劳育-公共劳动积分-《2023-2024学年海南国际学院团学劳动积分（2023级）》.xlsx",
    "data/raw/files/劳育劳动文化活动-《相关志愿时长记录》.xlsx",
    "data/raw/files/综合赋能-《集体活动参与加分》（2023级）.xlsx",
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
KEY_ASSIGNMENT = re.compile(r"CAMPUS_LLM_API_KEY\s*=\s*([^\s#]*)")
SAFE_PLACEHOLDERS = {"", '""', "''", "...", '"..."', "'...'"}


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return [item for item in output.split("\0") if item]


def main() -> None:
    failures: list[str] = []
    for relative in tracked_files():
        if relative in SENSITIVE_LIBRARY_FILES:
            failures.append(f"sensitive student record must remain untracked: {relative}")
            continue
        if relative == ".env.local" or relative.startswith(PROHIBITED[1:]):
            failures.append(f"prohibited tracked path: {relative}")
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                failures.append(f"possible secret in tracked file: {relative}")
                break
        else:
            for match in KEY_ASSIGNMENT.finditer(content):
                value = match.group(1).strip()
                if value not in SAFE_PLACEHOLDERS and len(value.strip("\"'")) >= 16:
                    failures.append(f"possible configured API key in tracked file: {relative}")
                    break
    if failures:
        raise SystemExit("Publication audit failed:\n- " + "\n- ".join(failures))
    print(f"Publication audit passed: {len(tracked_files())} tracked files checked.")


if __name__ == "__main__":
    main()
