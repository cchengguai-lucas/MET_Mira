#!/usr/bin/env bash
# Validate the host and generated m2 edge topology without switching traffic.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
require_staging_worktree

for command in nginx python3 psql systemctl curl ss rg; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 1
  }
done
find_pgbouncer >/dev/null 2>&1 || {
  echo "PgBouncer is missing; run scripts/ops/install-edge-staging.sh (no sudo required)" >&2
  exit 1
}
[[ "$(stat -c %a "$ROOT/.env")" == 600 ]] || {
  echo "$ROOT/.env must have mode 0600" >&2
  exit 1
}
systemctl --user is-active --quiet met-jira-postgres.service || {
  echo "met-jira-postgres.service is not active" >&2
  exit 1
}

generate_edge_config
"$ROOT/scripts/ops/test-edge-topology.sh" config

(
  cd "$ROOT"
  python3 - <<'PY'
from sqlalchemy import text

from backend.config import get_settings
from backend.db import configure

settings = get_settings()
if settings.database_url.rsplit("@", 1)[-1].split("/", 1)[0] not in {
    "127.0.0.1:5433",
    "localhost:5433",
}:
    raise SystemExit(".env DATABASE_URL must remain the direct PostgreSQL rollback path")
engine = configure(settings.database_url)
with engine.connect() as connection:
    if connection.scalar(text("select current_database()")) != "met_jira_staging":
        raise SystemExit("DATABASE_URL does not select met_jira_staging")
engine.dispose()
PY
)

echo "listeners before deployment:"
ss -ltn '( sport = :5433 or sport = :6433 or sport = :18801 or sport = :8801 )'
echo "staging edge preflight passed"
