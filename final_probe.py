import urllib.request, json
out = []
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as r:
        d = json.loads(r.read().decode("utf-8"))
        out.append("HEALTH status=%s adapter=%s provider=%s" % (
            d.get("status"), d.get("composer", {}).get("adapter"), d.get("composer", {}).get("provider")))
except Exception as e:
    out.append("HEALTH FAIL: %r" % e)
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/sessions/stats", timeout=5) as r:
        d = json.loads(r.read().decode("utf-8"))
        out.append("STATS count=%s max=%s" % (d.get("count"), d.get("max_sessions")))
except Exception as e:
    out.append("STATS FAIL: %r" % e)
with open("final_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
