#!/bin/bash
# Verify SENIOR_ADMIN_TOKEN lazy-load fix on the server.
# Writes results to /tmp/server_verify_out.txt
OUT=/tmp/server_verify_out.txt
: > "$OUT"
log() { echo "$1" >> "$OUT"; }

APP=/www/wwwroot/26新生网站/campus-onboarding-copilot
SRC="$APP/src/campus_copilot"

log "=== server verify start $(date '+%F %T') ==="

# 1. Confirm .env.local token config
log "--- .env.local SENIOR_ADMIN_TOKEN ---"
grep -E '^SENIOR_ADMIN_TOKEN=' "$APP/.env.local" >> "$OUT" 2>&1 || log "NOT_SET_IN_ENV_LOCAL"

# 2. Confirm new code present
log "--- code markers ---"
grep -n "def admin_token" "$SRC/config.py" >> "$OUT" 2>&1 || log "MISSING admin_token in config.py"
grep -n "admin_token()" "$SRC/senior.py" >> "$OUT" 2>&1 || log "MISSING admin_token use in senior.py"

# 3. Restart service
log "--- restart xiaohaigpt ---"
systemctl restart xiaohaigpt 2>&1 >> "$OUT"
sleep 2
systemctl is-active xiaohaigpt >> "$OUT" 2>&1

# 4. Wait for health (probe localhost:8000)
PORT=8000
if ! (echo > /dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then
  # try to find the actual port from systemd unit
  PORT=$(systemctl show xiaohaigpt -p ExecStart --value 2>/dev/null | grep -oE 'port [0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -z "$PORT" ] && PORT=8000
fi
log "probe port=$PORT"
up=0
for i in $(seq 1 40); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    log "HEALTH OK after ${i}s"
    up=1
    break
  fi
  sleep 1
done
if [ "$up" != "1" ]; then log "HEALTH NOT UP after 40s"; exit 2; fi

# 5. Token checks
TOKEN=$(grep -E '^SENIOR_ADMIN_TOKEN=' "$APP/.env.local" | head -1 | cut -d= -f2-)
log "TOKEN=${TOKEN}"
check() {
  local name="$1" path="$2" want="$3"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$PORT$path")
  if [ "$code" = "$want" ]; then
    log "PASS $name http=$code want=$want"
  else
    log "FAIL $name http=$code want=$want"
  fi
}
check correct_token "/api/senior/pending?token=$TOKEN" 200
check wrong_token    "/api/senior/pending?token=wrong-token" 401
check default_token  "/api/senior/pending?token=dev-senior-token" 401
check no_token       "/api/senior/pending" 401

log "=== server verify done ==="
