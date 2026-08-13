#!/usr/bin/env python3
"""Selfcheck: 6 acceptance questions against /api/chat.
Usage: python selfcheck.py <tag>   (tag e.g. legacy / free / free2)
Outputs a line per question: source | answerable | judgment | answer[:100]
"""
import json
import sys
import urllib.request

API = "http://127.0.0.1:8000/api/chat"
QUESTIONS = [
    ("q1", "学校去县城多长时间"),
    ("q2", "清华大学在哪个城市"),
    ("q3", "麻辣香锅多少钱"),
    ("q4", "食堂有什么好吃的"),
    ("q5", "宿舍是几人间"),
    ("q6", "三亚天气怎么样"),
]

def ask(session_id, query):
    payload = json.dumps({
        "query": query,
        "session_id": session_id,
        "top_k": 8,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    out = []
    for key, q in QUESTIONS:
        try:
            d = ask(f"selfcheck-{tag}-{key}", q)
            answer = str(d.get("answer", "")).replace("\n", " ").strip()
            out.append(f"[{key}] source={d.get('source')} answerable={d.get('answerable')} "
                       f"judgment={d.get('judgment')} unknown={d.get('unknown')} "
                       f"answer={answer[:100]}")
        except Exception as exc:
            out.append(f"[{key}] ERROR: {exc}")
    text = "\n".join(out)
    print(text)
    with open(f"selfcheck_{tag}.txt", "w", encoding="utf-8") as fh:
        fh.write(text + "\n")

if __name__ == "__main__":
    main()
