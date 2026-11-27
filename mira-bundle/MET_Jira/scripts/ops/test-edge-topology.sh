#!/usr/bin/env bash
# Static config assertions plus optional checks against the running topology.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
mode="${1:-config}"

test -s "$RUNTIME_DIR/pgbouncer.ini"
test -s "$RUNTIME_DIR/userlist.txt"
test -s "$RUNTIME_DIR/nginx.conf"
[[ "$(stat -c %a "$RUNTIME_DIR/userlist.txt")" == 600 ]]
[[ "$(stat -c %a "$RUNTIME_DIR/app.env")" == 600 ]]

rg -q '^pool_mode = transaction$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^default_pool_size = 8$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^reserve_pool_size = 2$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^max_client_conn = 100$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^query_wait_timeout = 5$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^max_prepared_statements = 0$' "$RUNTIME_DIR/pgbouncer.ini"
rg -q '^METJIRA_DB_PREPARE_THRESHOLD=-1$' "$RUNTIME_DIR/app.env"

rg -q 'listen 0\.0\.0\.0:8801;' "$RUNTIME_DIR/nginx.conf"
rg -q 'server 127\.0\.0\.1:18801;' "$RUNTIME_DIR/nginx.conf"
rg -q 'client_max_body_size 50m;' "$RUNTIME_DIR/nginx.conf"
rg -q 'location \^~ /api/agent/' "$RUNTIME_DIR/nginx.conf"
rg -q 'proxy_buffering off;' "$RUNTIME_DIR/nginx.conf"
rg -q 'proxy_read_timeout 180s;' "$RUNTIME_DIR/nginx.conf"
rg -q 'proxy_send_timeout 180s;' "$RUNTIME_DIR/nginx.conf"
rg -q 'location = /metrics' "$RUNTIME_DIR/nginx.conf"
rg -q 'return 404;' "$RUNTIME_DIR/nginx.conf"
rg -q 'X-Forwarded-For \$remote_addr' "$RUNTIME_DIR/nginx.conf"
nginx -t -p "$RUNTIME_DIR/" -c "$RUNTIME_DIR/nginx.conf"

if [[ "$mode" == deployed ]]; then
  curl -fsS http://127.0.0.1:18801/api/health >/dev/null
  curl -fsS http://127.0.0.1:8801/api/health >/dev/null
  [[ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8801/metrics)" == 404 ]]
  curl -fsS http://127.0.0.1:18801/metrics >/dev/null
  ss -ltn | rg -q '127\.0\.0\.1:18801\b'
  ss -ltn | rg -q '127\.0\.0\.1:6433\b'
  ss -ltn | rg -q '0\.0\.0\.0:8801\b'
fi

echo "edge topology $mode assertions passed"
