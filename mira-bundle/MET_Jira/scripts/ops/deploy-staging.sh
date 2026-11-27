#!/usr/bin/env bash
# Build and switch staging to Nginx -> Uvicorn -> PgBouncer.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
require_staging_worktree
cd "$ROOT"

find_pgbouncer >/dev/null 2>&1 || {
  echo "run scripts/ops/install-edge-staging.sh before deployment" >&2
  exit 1
}

(cd web && { [[ -d node_modules ]] || npm ci; } && npm run build)
"$ROOT/scripts/ops/preflight-staging.sh"

direct_database_url="$(
  python3 - <<'PY'
from pathlib import Path

from backend.config import _load_dotenv

print(_load_dotenv(Path(".env"))["METJIRA_DATABASE_URL"])
PY
)"

echo "== bootstrap (direct PostgreSQL :5433)"
METJIRA_DATABASE_URL="$direct_database_url" \
  METJIRA_DATA_DIR="${METJIRA_DATA_DIR:-$HOME/.met_jira_staging}" \
  METJIRA_DEPLOY_ENV=staging \
  python3 -m backend.ops bootstrap

generate_edge_config
systemctl --user daemon-reload
systemctl --user disable --now met-jira-staging-scheduler.service >/dev/null 2>&1 || true

switched=0
rollback_on_error() {
  local status=$?
  trap - ERR
  if [[ "$switched" == 1 ]]; then
    echo "deployment failed after traffic switch began; restoring direct :8801" >&2
    "$ROOT/scripts/ops/rollback-staging.sh" || true
  fi
  exit "$status"
}
trap rollback_on_error ERR

echo "== PgBouncer (:6433)"
systemctl --user enable met-jira-staging-pgbouncer.service >/dev/null
systemctl --user restart met-jira-staging-pgbouncer.service
for _ in $(seq 1 20); do
  if ss -ltn | rg -q '127\.0\.0\.1:6433\b'; then
    break
  fi
  sleep 1
done
ss -ltn | rg -q '127\.0\.0\.1:6433\b'
pooled_database_url="$(
  python3 - "$RUNTIME_DIR/app.env" <<'PY'
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("METJIRA_DATABASE_URL="):
        print(line.split("=", 1)[1])
        break
else:
    raise SystemExit("generated app.env has no DATABASE_URL")
PY
)"
METJIRA_DATABASE_URL="$pooled_database_url" python3 - <<'PY'
from sqlalchemy import text

from backend.db import configure

engine = configure()
with engine.connect() as connection:
    assert connection.scalar(text("select 1")) == 1
engine.dispose()
PY

echo "== API (:18801, 2 workers)"
switched=1
systemctl --user enable met-jira-staging.service >/dev/null
systemctl --user restart met-jira-staging.service
wait_http http://127.0.0.1:18801/api/health 45

echo "== outbox"
systemctl --user enable met-jira-staging-outbox.service >/dev/null
systemctl --user restart met-jira-staging-outbox.service
systemctl --user is-active --quiet met-jira-staging-outbox.service

echo "== Nginx (:8801)"
systemctl --user enable met-jira-staging-nginx.service >/dev/null
systemctl --user restart met-jira-staging-nginx.service
wait_http http://127.0.0.1:8801/api/health 30

"$ROOT/scripts/ops/test-edge-topology.sh" deployed
trap - ERR
echo "staging edge is healthy at http://$(hostname -I | awk '{print $1}'):8801 ($(git rev-parse --short HEAD))"
