#!/usr/bin/env bash
# Show edge services, listeners, and externally observable staging checks.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"

for unit in \
  met-jira-postgres.service \
  met-jira-staging-pgbouncer.service \
  met-jira-staging.service \
  met-jira-staging-outbox.service \
  met-jira-staging-nginx.service \
  met-jira-staging-scheduler.service; do
  printf '%-44s %s\n' "$unit" "$(systemctl --user is-active "$unit" 2>/dev/null || true)"
done

ss -ltn '( sport = :5433 or sport = :6433 or sport = :18801 or sport = :8801 )'

printf 'direct API health: '
curl -fsS http://127.0.0.1:18801/api/health
printf '\nedge API health:   '
curl -fsS http://127.0.0.1:8801/api/health
printf '\nedge /metrics:     '
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8801/metrics

"$ROOT/scripts/ops/test-edge-topology.sh" deployed
