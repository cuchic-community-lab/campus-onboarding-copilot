"""Verify admin_token() lazy-load fix (SENIOR_ADMIN_TOKEN from .env.local).

Usage:
    python tools/verify_admin_token.py > out.txt 2>&1
Checks:
  1. Before load_env(): admin_token() returns default (dev-senior-token).
  2. After load_env(): admin_token() reflects .env.local value (SENIOR_ADMIN_TOKEN).
  3. senior.pending() accepts the .env.local token, rejects wrong/default token.
Zero third-party dependencies.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

os.environ.pop("SENIOR_ADMIN_TOKEN", None)  # ensure pure .env.local path

from campus_copilot import config
from campus_copilot import senior

results = []


def _pending_ok(token: str) -> bool:
    try:
        r = senior.pending(token)
        return r.get("status") == "ok" and "pending" in r
    except PermissionError:
        return False


def _pending_401(token: str) -> bool:
    try:
        senior.pending(token)
        return False
    except PermissionError:
        return True


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)


# 1) before load_env: default expected
check(
    "pre_load_default",
    config.admin_token() == "dev-senior-token",
    f"admin_token()={config.admin_token()!r}",
)

# 2) after load_env: .env.local value expected
config.load_env()
env_val = os.getenv("SENIOR_ADMIN_TOKEN", "")
print("INFO env SENIOR_ADMIN_TOKEN =", repr(env_val))
if env_val:
    check("post_load_env", config.admin_token() == env_val, f"admin_token()={config.admin_token()!r}")
    check("pending_correct_token", _pending_ok(env_val))
    check("pending_wrong_token", _pending_401("wrong-token"))
    check("pending_default_token", _pending_401("dev-senior-token"))
else:
    # .env.local has no SENIOR_ADMIN_TOKEN: default must still work
    check("no_env_config_default_ok", _pending_ok("dev-senior-token"))
    check("no_env_config_wrong_401", _pending_401("wrong-token"))


fails = [n for n, ok, _ in results if not ok]
print("SUMMARY", f"{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
