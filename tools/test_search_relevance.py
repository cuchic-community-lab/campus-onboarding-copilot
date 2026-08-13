"""v1.9 逐条相关性过滤单测：直接测 search.py 的 _filter_results/_extract_geo_terms。

不依赖网络；用 mock 结果集验证 4 个验收场景：
1. 陵水天气 → 杀"陵县"(山东)
2. 三亚到北京航班 → 杀 P1 硬凑"飞机能上网/会务信息更新"
3. 三亚天气怎么样 → 不误伤三亚天气
4. 园区最近有什么新闻（glossary 改写后含试验区地域词）→ 保留试验区新闻
"""
import os
import sys
from pathlib import Path

ROOT = Path(r"D:\CUC_Files\26新生网站\campus-onboarding-copilot")
sys.path.insert(0, str(ROOT / "src"))

from campus_copilot.search import _filter_results, _extract_geo_terms, _query_keywords

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name} {detail}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def r(title, snippet):
    return {"title": title, "link": "https://example.com/x", "snippet": snippet}


print("== 1. 陵水天气：杀陵县(山东) ==")
q1 = "陵水 天气"
geo1 = _extract_geo_terms(q1)
print("  geo:", geo1)
assert "陵水" in geo1
res1 = _filter_results([
    r("陵水县天气预报", "陵水明天天气 晴转多云"),
    r("山东省德州市陵县天气预报", "陵县天气 小雨"),   # 近似词，应被杀
    r("陵水黎安国际教育创新试验区", "陵水 试验区 最新动态"),
], q1, 1)
check("陵水天气结果保留", any("陵水县" in x["title"] for x in res1))
check("陵县(山东)被丢弃", not any("陵县" in x["title"] for x in res1), f"kept={[x['title'] for x in res1]}")
check("试验区结果保留", any("试验区" in x["title"] for x in res1))

print("== 2. 三亚到北京的航班：杀 P1 硬凑 ==")
q2 = "三亚到北京的航班"
geo2 = _extract_geo_terms(q2)
print("  geo:", geo2)
assert "三亚" in geo2 and "北京" in geo2
res2 = _filter_results([
    r("三亚到北京航班时刻表", "三亚飞北京 机票 价格 时刻表"),
    r("飞机上能上网吗", "飞机上网 空中WiFi 服务"),          # 硬凑，应被杀
    r("会务信息更新通知", "会议安排 会务信息 更新"),          # 硬凑，应被杀
    r("三亚凤凰国际机场", "三亚 机场 航班信息"),
], q2, 1)
check("三亚航班结果保留", any("航班时刻表" in x["title"] for x in res2))
check("飞机能上网被杀", not any("飞机上能上网" in x["title"] for x in res2))
check("会务信息被杀", not any("会务信息" in x["title"] for x in res2))
# AND 锚定：query=三亚+北京，仅含三亚的机场信息属泛相关，宁缺毋滥杀掉
check("仅三亚机场被杀", not any("凤凰" in x["title"] for x in res2), f"kept={[x['title'] for x in res2]}")

print("== 2b. LLM 空格分词 query：仅含北京(目的地)的无关新闻必须被杀 ==")
q2b = "三亚 北京 航班"
geo2b = _extract_geo_terms(q2b)
print("  geo:", geo2b)
assert set(geo2b) == {"三亚", "北京"}, geo2b
res2b = _filter_results([
    r("三亚到北京航班时刻表", "三亚 北京 机票 航班 时刻表"),
    r("飞机能上网,但不是真正空中上网", "国航国内首架无线局域网航班 执飞北京-成都航线"),  # 含北京+航班但无关，应被杀
    r("中国传媒大学到校路线", "北京南站 传媒大学 地铁14号线 大望路"),                 # 含北京无关，应被杀
], q2b, 1)
check("真航班保留", any("航班时刻表" in x["title"] for x in res2b), f"kept={[x['title'] for x in res2b]}")
check("北京-成都航线被杀", not any("空中上网" in x["title"] for x in res2b))
check("传媒大学路线被杀", not any("传媒大学" in x["title"] for x in res2b))

print("== 3. 三亚天气怎么样：不误伤 ==")
q3 = "三亚天气怎么样"
geo3 = _extract_geo_terms(q3)
print("  geo:", geo3)
assert "三亚" in geo3
res3 = _filter_results([
    r("三亚天气预报15天", "三亚 未来天气 温度"),
    r("陵水天气", "陵水 天气"),   # 地域锚定：query 含三亚 → 非三亚结果被杀（合理）
], q3, 1)
check("三亚天气保留", any("三亚" in x["title"] for x in res3), f"kept={[x['title'] for x in res3]}")
check("非三亚地域被杀", not any("陵水" in x["title"] for x in res3))

print("== 4. 园区最近有什么新闻（glossary 改写后含试验区/海南/陵水/黎安）==")
q4 = "海南陵水黎安国际教育创新试验区 最近 新闻"
geo4 = _extract_geo_terms(q4)
print("  geo:", geo4)
assert "海南" in geo4 and "陵水" in geo4 and "黎安" in geo4, geo4
check("后缀模式不误提取试验区", "试验区" not in geo4)
res4 = _filter_results([
    r("试验区新闻动态", "海南陵水黎安国际教育创新试验区 最新新闻"),
    r("湖南建投二建中标海南黎安教育城宿舍项目", "中标 海南陵水黎安国际教育创新试验区 高校学生宿舍"),
    r("北京海淀区新闻", "北京 海淀 新闻"),   # 不含试验区地域词 → 被杀
], q4, 1)
check("试验区新闻保留", any("试验区新闻动态" in x["title"] for x in res4))
check("教育城宿舍项目保留", any("宿舍项目" in x["title"] for x in res4), f"kept={[x['title'] for x in res4]}")
check("外地新闻被杀", not any("海淀" in x["title"] for x in res4), f"kept={[x['title'] for x in res4]}")

print("== 5. 无地域词 query：核心词命中即可 ==")
q5 = "2026级新生 报到 时间"
res5 = _filter_results([
    r("2026级新生报到须知", "新生 报到 时间 安排"),
    r("校园风光照片", "校园 风景 摄影"),
], q5, 1)
check("报到须知保留", any("报到须知" in x["title"] for x in res5), f"kept={[x['title'] for x in res5]}")
check("无关照片被杀", not any("风光" in x["title"] for x in res5))

print("== 6. 开关/阈值配置读取 ==")
from campus_copilot.config import search_settings
s = search_settings()
print("  relevance_filter:", s["relevance_filter"], "min_keyword_hits:", s["min_keyword_hits"])
check("默认开启", s["relevance_filter"] is True)
check("默认阈值=1", s["min_keyword_hits"] == 1)

print("== 7. _query_keywords 2-gram 捕获天气/航班/新闻 ==")
kw = _query_keywords("三亚到北京的航班")
print("  kw:", kw)
check("含航班 2-gram", "航班" in kw)
kw_weather = _query_keywords("三亚天气怎么样")
print("  kw_weather:", kw_weather)
check("含天气 2-gram", "天气" in kw_weather)

print(f"\n===== PASS={PASS} FAIL={FAIL} =====")
sys.exit(1 if FAIL else 0)
