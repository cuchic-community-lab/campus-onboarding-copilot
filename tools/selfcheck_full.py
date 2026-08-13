#!/usr/bin/env python3
"""Selfcheck full: run all cases from selfcheck_cases.json against /api/chat.

Usage: python selfcheck_full.py [--report-dir DIR]
Outputs a Markdown report (timestamped) + prints a summary line.
Exit code: 0 all pass / 1 has FAIL / 2 has WARN.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

API = "http://127.0.0.1:8000"
CASES = Path(__file__).parent / "selfcheck_cases.json"


def load_cases():
    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = data if isinstance(data, list) else data.get("cases", [])
    return cases


def ask(session_id, query):
    payload = json.dumps({"query": query, "session_id": session_id, "top_k": 8},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data["_http_status"] = resp.status
            return data
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        try:
            data = json.loads(body)
        except ValueError:
            data = {"error": body[:200]}
        data["_http_status"] = exc.code
        return data


def _match(want, got):
    """Match expected value (str/bool or list of allowed values) against actual."""
    if isinstance(want, list):
        return got in want
    return got == want


def check(case, resp):
    """Return (status, reasons): status in PASS/FAIL/WARN."""
    expect = case.get("expect", {})
    reasons = []
    # source
    want_src = expect.get("source")
    if want_src and not _match(want_src, resp.get("source")):
        reasons.append(f"source 期望 {want_src} 实际 {resp.get('source')}")
    # answerable
    if "answerable" in expect:
        want = expect["answerable"]
        if not _match(want, resp.get("answerable")):
            reasons.append(f"answerable 期望 {want} 实际 {resp.get('answerable')}")
    # must_contain (any-of if list, all if single string)
    must = expect.get("must_contain", [])
    if isinstance(must, str):
        must = [must]
    answer = str(resp.get("answer", ""))
    for kw in must:
        if kw and kw not in answer:
            reasons.append(f"answer 缺关键词 '{kw}'")
    # must_contain_any
    must_any = expect.get("must_contain_any", [])
    if must_any and not any(kw in answer for kw in must_any):
        reasons.append(f"answer 应含其一 {must_any}")
    # must_not_contain
    must_not = expect.get("must_not_contain", [])
    for kw in must_not:
        if kw and kw in answer:
            reasons.append(f"answer 不应含 '{kw}'")
    # boundary cases: expected HTTP status / error field
    want_status = expect.get("http_status")
    if want_status:
        got_status = resp.get("_http_status")
        if got_status != want_status:
            reasons.append(f"http_status 期望 {want_status} 实际 {got_status}")
        err_field = resp.get("error") or resp.get("detail")
        want_err = expect.get("error")
        if want_err and err_field and want_err not in str(err_field):
            reasons.append(f"error 期望含 {want_err} 实际 {err_field}")
    # warnings (soft) — v1.7 新链路下 citation_missing/peer_label 是 LLM 自由答的
    # 正常现象（校验已降级为提示），不算 WARN；仅 web_search_unavailable 与真异常算。
    IGNORABLE_WARN_PREFIXES = ("citation_missing_from_answer", "peer_claim_not_labeled_experience",
                               "unknown_citation", "composer_warning:")
    warns = []
    if resp.get("composer_warning"):
        raw = str(resp.get("composer_warning"))
        parts = raw.split(":")
        sig = parts[1] if len(parts) > 1 else raw
        if not any(sig.startswith(p) for p in IGNORABLE_WARN_PREFIXES):
            warns.append(f"composer_warning={raw}")
    return ("FAIL" if reasons else ("WARN" if warns else "PASS")), reasons, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default=str(Path(__file__).parent / ".." / "reports"))
    ap.add_argument("--cases", default=str(CASES))
    args = ap.parse_args()

    cases = load_cases()
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"selfcheck_{stamp}.md"

    results = []
    start = time.time()
    for case in cases:
        cid = case.get("id", "?")
        query = case.get("query", "")
        group = case.get("group", "未分组")
        try:
            resp = ask(f"fullcheck-{cid}", query)
        except Exception as exc:
            results.append({"case": case, "status": "FAIL", "reasons": [f"请求异常 {exc}"], "warns": [], "resp": None})
            continue
        status, reasons, warns = check(case, resp)
        results.append({"case": case, "status": status, "reasons": reasons, "warns": warns, "resp": resp})

    elapsed = time.time() - start
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_warn = sum(1 for r in results if r["status"] == "WARN")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    total = len(results)

    # group stats
    groups = {}
    for r in results:
        g = r["case"].get("group", "未分组")
        groups.setdefault(g, {"total": 0, "pass": 0, "fail": 0, "warn": 0})
        groups[g]["total"] += 1
        if r["status"] == "PASS": groups[g]["pass"] += 1
        elif r["status"] == "FAIL": groups[g]["fail"] += 1
        else: groups[g]["warn"] += 1

    lines = []
    lines.append(f"# XiaohaiGPT 自检报告（全量）")
    lines.append("")
    lines.append(f"- 运行时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- API 基址: `{API}`")
    lines.append(f"- 用例总数: **{total}** | PASS **{n_pass}** | FAIL **{n_fail}** | WARN **{n_warn}** | 通过率 **{n_pass/total*100:.1f}%**")
    lines.append(f"- 总耗时: {elapsed:.1f}s")
    lines.append("")
    lines.append("## 总览（按分组）")
    lines.append("")
    lines.append("| 分组 | 总数 | 通过 | 失败 | 警告 | 通过率 |")
    lines.append("|------|-----:|-----:|-----:|-----:|-------:|")
    for g, s in groups.items():
        rate = s["pass"] / s["total"] * 100 if s["total"] else 0
        lines.append(f"| {g} | {s['total']} | {s['pass']} | {s['fail']} | {s['warn']} | {rate:.0f}% |")
    lines.append(f"| **合计** | **{total}** | **{n_pass}** | **{n_fail}** | **{n_warn}** | **{n_pass/total*100:.1f}%** |")
    lines.append("")
    lines.append("## FAIL 明细")
    lines.append("")
    for r in results:
        if r["status"] != "FAIL":
            continue
        c = r["case"]
        lines.append(f"### {c.get('id')} — {c.get('query')}")
        lines.append("")
        lines.append(f"- 分组: {c.get('group')}")
        lines.append(f"- 期望: `{json.dumps(c.get('expect', {}), ensure_ascii=False)}`")
        if r["resp"]:
            lines.append(f"- 实际: source=`{r['resp'].get('source')}` answerable=`{r['resp'].get('answerable')}`")
            ans = str(r["resp"].get("answer", "")).replace("\n", " ")
            lines.append(f"- 失败原因: {'; '.join(r['reasons'])}")
            lines.append(f"- answer 前200字: `{ans[:200]}`")
        else:
            lines.append(f"- 失败原因: {'; '.join(r['reasons'])}")
        lines.append("")
    lines.append("## WARN 明细")
    lines.append("")
    for r in results:
        if r["status"] != "WARN":
            continue
        c = r["case"]
        lines.append(f"- {c.get('id')} {c.get('query')}: {'; '.join(r['warns'])}")
    lines.append("")
    lines.append(f"一句话总结: 通过率 {n_pass/total*100:.1f}% ({n_pass}/{total})，FAIL {n_fail} 项。")
    text = "\n".join(lines)
    report_path.write_text(text, encoding="utf-8")
    print(f"报告: {report_path}")
    print(f"一句话总结: 通过率 {n_pass/total*100:.1f}% ({n_pass}/{total})，FAIL {n_fail} 项，WARN {n_warn} 项。")
    sys.exit(1 if n_fail else (2 if n_warn else 0))


if __name__ == "__main__":
    main()
