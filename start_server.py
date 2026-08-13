"""Start the XiaohaiGPT server detached (create new process group, survives
agent turn) and wait for it to come up. Writes status to start_out.txt."""
import os
import subprocess
import sys
import time
import urllib.request
import json
from pathlib import Path

ROOT = Path(r"D:\CUC_Files\26新生网站\campus-onboarding-copilot")
os.chdir(ROOT)
env = dict(os.environ)
env["PYTHONPATH"] = str(ROOT / "src")

out = []
def log(s):
    out.append(s)

# Start server via subprocess, detached. On Windows use CREATE_NEW_PROCESS_GROUP
# + DETACHED_PROCESS so it is not tied to this script's process tree.
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
stdout_f = open(ROOT / "serve_stdout.log", "w", encoding="utf-8")
stderr_f = open(ROOT / "serve_stderr.log", "w", encoding="utf-8")
try:
    p = subprocess.Popen(
        [sys.executable, "-m", "campus_copilot.cli", "serve", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(ROOT),
        env=env,
        stdout=stdout_f,
        stderr=stderr_f,
        creationflags=flags,
        close_fds=True,
    )
    log("STARTED PID=%s" % p.pid)
except Exception as e:
    log("START FAILED: %r" % e)
    with open("start_out.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    sys.exit(1)

# Poll health up to 30s
for i in range(30):
    time.sleep(1)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
            log("HEALTH OK after %ss status=%s composer=%s" % (i + 1, d.get("status"), d.get("composer", {}).get("adapter")))
            with open("start_out.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(out))
            sys.exit(0)
    except Exception:
        continue

log("HEALTH NOT UP after 30s")
with open("start_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
sys.exit(2)
