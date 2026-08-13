"""v1.9 重启后实测：/api/chat 全链路验证搜索结果相关性过滤。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api/chat"
CASES = [
    "今天陵水天气",
    "三亚到北京的航班",
    "三亚天气怎么样",
    "园区最近有什么新闻",
    "宿舍是几人间",   # 本地问答不触发联网
]


def ask(query):
    body = json.dumps({"query": query, "session_id": None, "profile": {}}).encode("utf-8")
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ok = True
    for q in CASES:
        print("=" * 70)
        print("Q:", q)
        try:
            d = ask(q)
        except Exception as e:
            print("  ERROR:", repr(e))
            ok = False
            continue
        print("  source:", d.get("source"), "| tier:", d.get("search_tier_used"),
              "| sq:", d.get("web_search_query"))
        results = d.get("search_results") or []
        print("  results(%d):" % len(results))
        for i, item in enumerate(results, 1):
            t = str(item.get("title") or "")
            sn = str(item.get("snippet") or "")[:60]
            tier = item.get("site_tier")
            print(f"    {i}. [{tier}] {t} | {sn}")
        # 断言
        if q == "今天陵水天气":
            titles = " ".join(str(x.get("title") or "") + str(x.get("snippet") or "") for x in results)
            if "陵县" in titles:
                print("  >>> FAIL 陵县(山东)混入"); ok = False
            else:
                print("  >>> PASS 无陵县")
        elif q == "三亚到北京的航班":
            titles = " ".join(str(x.get("title") or "") + str(x.get("snippet") or "") for x in results)
            if "飞机上能上网" in titles or "会务信息" in titles:
                print("  >>> FAIL P1硬凑未过滤"); ok = False
            else:
                print("  >>> PASS 无硬凑(飞机能上网/会务)")
        elif q == "三亚天气怎么样":
            titles = " ".join(str(x.get("title") or "") + str(x.get("snippet") or "") for x in results)
            if results and "三亚" not in titles:
                print("  >>> FAIL 三亚天气误伤/无结果"); ok = False
            else:
                print("  >>> PASS 三亚相关")
        elif q == "园区最近有什么新闻":
            titles = " ".join(str(x.get("title") or "") + str(x.get("snippet") or "") for x in results)
            if results and not any(k in titles for k in ("试验区", "黎安", "陵水", "海南")):
                print("  >>> WARN 结果不含试验区语境词:", titles[:120])
            else:
                print("  >>> PASS 试验区语境")
        elif q == "宿舍是几人间":
            if d.get("source") == "web_search":
                print("  >>> WARN 本地问答走了联网")
            else:
                print("  >>> PASS 本地问答未触发联网 source=%s" % d.get("source"))
    print("=" * 70)
    print("OVERALL:", "OK" if ok else "HAS FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
