#!/usr/bin/env bash
# Restore the pre-edge staging shape: Uvicorn directly on :8801 and PostgreSQL :5433.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
require_staging_worktree

systemctl --user stop met-jira-staging-nginx.service \
  met-jira-staging-outbox.service met-jira-staging.service || true
systemctl --user stop met-jira-staging-pgbouncer.service || true
systemctl --user disable met-jira-staging-nginx.service \
  met-jira-staging-pgbouncer.service >/dev/null 2>&1 || true
rm -rf "$RUNTIME_DIR"

mkdir -p "$UNIT_DIR"
sed "s#@ROOT@#$ROOT#g" \
  "$ROOT/scripts/ops/systemd/met-jira-staging-legacy.service" \
  >"$UNIT_DIR/met-jira-staging.service"
sed \
  -e "s#@ROOT@#$ROOT#g" \
  -e "s#@ENV@#staging#g" \
  -e "s#@DATA_DIR@#%h/.met_jira_staging#g" \
  "$ROOT/scripts/ops/systemd/met-jira-outbox.service" \
  >"$UNIT_DIR/met-jira-staging-outbox.service"

systemctl --user daemon-reload
systemctl --user disable --now met-jira-staging-scheduler.service >/dev/null 2>&1 || true
systemctl --user enable met-jira-staging.service met-jira-staging-outbox.service >/dev/null
systemctl --user restart met-jira-staging.service
wait_http http://127.0.0.1:8801/api/health 45
systemctl --user restart met-jira-staging-outbox.service

if ss -ltn | rg -q '127\.0\.0\.1:(6433|18801)\b'; then
  echo "rollback left an edge listener active" >&2
  exit 1
fi
echo "staging restored to direct Uvicorn :8801 and PostgreSQL :5433"
