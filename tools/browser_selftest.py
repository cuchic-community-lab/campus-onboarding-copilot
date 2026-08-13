#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser_selftest.py — XiaohaiGPT 浏览器视觉层自检（API 文本自检之外的第二通道）

通道说明:
  tools/selfcheck.py  : API 文本层（JSON 响应断言）
  本脚本              : 浏览器视觉层（Edge headless + CDP 打开真实页面、输入问题、
                        截图 + DOM 元数据，配合 image-understand skill 识图断言 UI 问题）

技术栈（零额外依赖）:
  - Edge headless  --headless=new --remote-debugging-port
  - Node 22 原生 WebSocket 写 CDP 驱动（tools/browser_cdp_driver.js）
  - Python stdlib 做编排：起 Edge、生成驱动配置、汇总元数据

用法:
  python tools/browser_selftest.py [--out-dir DIR] [--debug-port PORT] [--keep-edge]

输出:
  <out>/shots/Cxxx.png ...       截图（移动端 375x812 / 桌面 1280x800）
  <out>/browser_dom_meta.json    DOM 元数据（供 browser_report.md 与识图交叉验证）
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

# ────────────────────────── 常量 ──────────────────────────
BASE_URL = "http://127.0.0.1:8000/xiaohaigpt.html"
API_BASE = "http://127.0.0.1:8000"
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
DEFAULT_OUT_DIR = r"C:\Users\20206\WorkBuddy\2026-07-31-17-28-47\output\xiaohaigpt\selfcheck"

VIEWPORT_MOBILE = {"w": 375, "h": 812, "dsf": 2}      # 移动端优先
VIEWPORT_DESKTOP = {"w": 1280, "h": 800, "dsf": 1}    # 桌面

# 抽样用例（对齐 selfcheck_cases.json）：正常问答/图片佐证/已知BUG/拒答BUG/联网/师哥师姐
CASES = [
    {"id": "C001", "query": "宿舍是几人间",        "viewport": "mobile",  "expand": True,  "group": "正常问答",  "note": "语料问答+引用佐证折叠"},
    {"id": "C008", "query": "GPA怎么算",          "viewport": "mobile",  "expand": True,  "group": "图片佐证",  "note": "GPA 折算表图片佐证显示"},
    {"id": "C006", "query": "食堂有什么好吃的",    "viewport": "mobile",  "expand": True,  "group": "已知BUG",   "note": "API层答成深职院，看视觉呈现"},
    {"id": "R001", "query": "清华大学在哪个城市",  "viewport": "mobile",  "expand": True,  "group": "拒答BUG",   "note": "应拒答却联网答游泳队新闻"},
    {"id": "R003", "query": "量子纠缠宿舍",        "viewport": "mobile",  "expand": True,  "group": "拒答BUG",   "note": "完全不存在话题，幻觉用例"},
    {"id": "W001", "query": "三亚天气怎么样",      "viewport": "mobile",  "expand": True,  "group": "联网搜索",  "note": "联网来源区+徽标+折叠"},
    {"id": "W003", "query": "园区最近有什么新闻",  "viewport": "mobile",  "expand": True,  "group": "联网搜索",  "note": "site_tier 徽标 / p1 提示"},
    {"id": "S003", "query": "军训要带什么？",      "viewport": "mobile",  "action": "senior", "group": "师哥师姐", "note": "问师哥师姐 pending→answered"},
    # 桌面端关键页
    {"id": "C001d", "query": "宿舍是几人间",       "viewport": "desktop", "expand": True,  "group": "桌面·正常问答", "note": "1280 桌面渲染"},
    {"id": "C008d", "query": "GPA怎么算",         "viewport": "desktop", "expand": True,  "group": "桌面·图片佐证", "note": "1280 桌面渲染"},
    {"id": "W001d", "query": "三亚天气怎么样",     "viewport": "desktop", "expand": True,  "group": "桌面·联网",   "note": "1280 桌面渲染"},
]


def log(msg):
    print("[browser_selftest] " + msg, flush=True)


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def service_ok():
    try:
        st, _ = http_get(API_BASE + "/xiaohaigpt.html", timeout=5)
        return st == 200
    except Exception:
        return False


def wait_cdp(port, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st, body = http_get("http://127.0.0.1:%d/json/version" % port, timeout=2)
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_edge(edge, port, profile_dir):
    args = [
        edge,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--mute-audio",
        "--remote-debugging-port=%d" % port,
        "--user-data-dir=%s" % profile_dir,
        "--window-size=375,812",
        "about:blank",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description="XiaohaiGPT 浏览器视觉层自检")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--debug-port", type=int, default=9333)
    ap.add_argument("--keep-edge", action="store_true", help="结束后保留 Edge（默认 kill）")
    args = ap.parse_args()

    shots_dir = os.path.join(args.out_dir, "shots")
    os.makedirs(shots_dir, exist_ok=True)

    # 1) 服务在线
    if not service_ok():
        log("FATAL: 服务不在线 %s" % API_BASE)
        log("先启动: cd D:/CUC_Files/26新生网站/campus-onboarding-copilot && PYTHONPATH=src python -m campus_copilot.cli serve")
        return 2
    log("服务在线: %s" % API_BASE)

    # 2) Edge
    edge = find_edge()
    if not edge:
        log("FATAL: 未找到 Edge")
        return 2
    log("Edge: %s" % edge)

    # 3) 驱动脚本
    here = os.path.dirname(os.path.abspath(__file__))
    driver = os.path.join(here, "browser_cdp_driver.js")
    if not os.path.exists(driver):
        log("FATAL: 缺少驱动 %s" % driver)
        return 2

    # 4) 启动 Edge
    profile_dir = tempfile.mkdtemp(prefix="xhg-edge-")
    proc = start_edge(edge, args.debug_port, profile_dir)
    log("Edge 已启动 (port=%d)" % args.debug_port)
    if not wait_cdp(args.debug_port):
        proc.kill()
        log("FATAL: CDP 未就绪")
        return 2
    log("CDP 就绪")

    # 5) 生成驱动配置
    cfg = {
        "debugPort": args.debug_port,
        "baseUrl": BASE_URL,
        "outDir": shots_dir,
        "apiBase": API_BASE,
        "viewportMobile": VIEWPORT_MOBILE,
        "viewportDesktop": VIEWPORT_DESKTOP,
        "cases": CASES,
    }
    cfg_path = os.path.join(args.out_dir, "browser_driver_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 6) 执行驱动
    log("运行 CDP 驱动（%d 用例，可能耗时数分钟）..." % len(CASES))
    t0 = time.time()
    r = subprocess.run(
        ["node", driver, cfg_path],
        capture_output=True, text=True, encoding="utf-8", timeout=900,
    )
    elapsed = time.time() - t0
    if r.returncode != 0:
        log("驱动异常 rc=%d stderr=%s" % (r.returncode, r.stderr[-2000:]))

    results = []
    try:
        results = json.loads(r.stdout)
    except Exception as e:
        log("解析驱动输出失败: %s" % e)
        log("stdout 尾部: %s" % r.stdout[-3000:])

    # 7) 汇总元数据
    meta_path = os.path.join(args.out_dir, "browser_dom_meta.json")
    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE_URL,
        "cases_total": len(CASES),
        "cases_ok": sum(1 for x in results if "error" not in x),
        "cases_error": [x.get("id") for x in results if "error" in x],
        "elapsed_sec": round(elapsed, 1),
        "results": results,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 8) 截图清单
    shots = sorted(os.listdir(shots_dir))
    log("完成: %d 用例 / %d 张截图 / 耗时 %.1fs" % (len(results), len(shots), elapsed))
    for s in shots:
        log("  shots/%s" % s)
    log("DOM 元数据: %s" % meta_path)

    if not args.keep_edge:
        proc.kill()
        log("Edge 已关闭")

    return 0


if __name__ == "__main__":
    sys.exit(main())
