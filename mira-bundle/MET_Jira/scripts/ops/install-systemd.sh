#!/usr/bin/env bash
# Install MET Jira as user-level systemd services.
#
#   scripts/ops/install-systemd.sh                         # production worktree: PostgreSQL :5433 + web :8800
#   scripts/ops/install-systemd.sh staging [--clone-prod]  # staging worktree: web :8801, database met_jira_staging
#   scripts/ops/install-systemd.sh backup                  # daily backup 02:30 + Sunday restore drill
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="${1:-production}"
CLONE_PROD="${2:-}"
UNIT_DIR="$HOME/.config/systemd/user"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
DB_PASSWORD="${METJIRA_DB_PASSWORD:-met_jira_dev}"
mkdir -p "$UNIT_DIR"

if [[ "$TARGET" == backup ]]; then
  for unit in met-jira-backup.service met-jira-backup.timer \
    met-jira-restore-check.service met-jira-restore-check.timer; do
    sed "s#@ROOT@#$ROOT#g" "$ROOT/scripts/ops/systemd/$unit" >"$UNIT_DIR/$unit"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now met-jira-backup.timer met-jira-restore-check.timer
  systemctl --user list-timers 'met-jira-*' --no-pager
  exit 0
fi

case "$TARGET" in
  production)
    UNIT=met-jira.service PORT=8800 DB_NAME=met_jira
    SERVICE_PREFIX=met-jira DEPLOY_ENV=production DATA_DIR="$ROOT/data"
    BOOTSTRAP_DATA_DIR="$ROOT/data"
    ;;
  staging)
    UNIT=met-jira-staging.service PORT=8801 DB_NAME=met_jira_staging
    SERVICE_PREFIX=met-jira-staging DEPLOY_ENV=staging DATA_DIR="%h/.met_jira_staging"
    BOOTSTRAP_DATA_DIR="$HOME/.met_jira_staging"
    ;;
  *) echo "usage: $0 [production|staging [--clone-prod]|backup]" >&2; exit 2 ;;
esac

if [[ ! -f "$ROOT/.env" ]]; then
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed "s/^METJIRA_SECRET=.*/METJIRA_SECRET=$secret/" "$ROOT/.env.example" >"$ROOT/.env"
  if [[ "$TARGET" == staging ]]; then
    sed -i \
      -e "s#^METJIRA_DATABASE_URL=.*#METJIRA_DATABASE_URL=postgresql+psycopg://met_jira:$DB_PASSWORD@127.0.0.1:5433/$DB_NAME#" \
      -e "s/^METJIRA_SCHEDULER=.*//" "$ROOT/.env"
    printf 'METJIRA_COOKIE_NAME=metjira_staging_session\nMETJIRA_SCHEDULER=false\n' >>"$ROOT/.env"
  fi
  chmod 600 "$ROOT/.env"
  echo "created $ROOT/.env ($TARGET) with a random METJIRA_SECRET"
fi

psql_admin() {
  PGPASSWORD="$DB_PASSWORD" "$PG_BIN/psql" -h 127.0.0.1 -p 5433 -U met_jira -d postgres -v ON_ERROR_STOP=1 "$@"
}

if [[ "$TARGET" == production ]]; then
  "$ROOT/scripts/ops/pg-local.sh" init
  # Hand the cluster over to systemd if it was started manually.
  if "$ROOT/scripts/ops/pg-local.sh" status >/dev/null 2>&1 \
    && ! systemctl --user is-active --quiet met-jira-postgres.service; then
    "$ROOT/scripts/ops/pg-local.sh" stop
  fi
  sed "s#@ROOT@#$ROOT#g" "$ROOT/scripts/ops/systemd/met-jira-postgres.service" \
    >"$UNIT_DIR/met-jira-postgres.service"
  systemctl --user daemon-reload
  systemctl --user enable --now met-jira-postgres.service
else
  if ! systemctl --user is-active --quiet met-jira-postgres.service; then
    echo "met-jira-postgres.service is not running; install production first" >&2
    exit 1
  fi
  if [[ -z "$(psql_admin -tAc "select 1 from pg_database where datname = '$DB_NAME'")" ]]; then
    psql_admin -qc "create database $DB_NAME owner met_jira"
    if [[ "$CLONE_PROD" == --clone-prod ]]; then
      PGPASSWORD="$DB_PASSWORD" "$PG_BIN/pg_dump" -h 127.0.0.1 -p 5433 -U met_jira --no-owner met_jira \
        | PGPASSWORD="$DB_PASSWORD" "$PG_BIN/psql" -h 127.0.0.1 -p 5433 -U met_jira -q -d "$DB_NAME" >/dev/null
      echo "cloned production data into $DB_NAME"
    fi
  fi
fi

if [[ "$TARGET" == staging ]]; then
  "$ROOT/scripts/ops/install-edge-staging.sh"
  "$ROOT/scripts/ops/deploy-staging.sh"
  exit 0
fi

sed "s#@ROOT@#$ROOT#g" "$ROOT/scripts/ops/systemd/$UNIT" >"$UNIT_DIR/$UNIT"
for role in scheduler outbox; do
  target_unit="$SERVICE_PREFIX-$role.service"
  sed \
    -e "s#@ROOT@#$ROOT#g" \
    -e "s#@ENV@#$DEPLOY_ENV#g" \
    -e "s#@DATA_DIR@#$DATA_DIR#g" \
    "$ROOT/scripts/ops/systemd/met-jira-$role.service" >"$UNIT_DIR/$target_unit"
done

if [[ ! -d "$ROOT/web/dist" ]]; then
  (cd "$ROOT/web" && { [[ -d node_modules ]] || npm ci; } && npm run build)
fi

METJIRA_DATA_DIR="$BOOTSTRAP_DATA_DIR" METJIRA_DEPLOY_ENV="$DEPLOY_ENV" \
  python3 -m backend.ops bootstrap
systemctl --user daemon-reload
systemctl --user enable "$UNIT"
systemctl --user restart "$UNIT"
systemctl --user enable --now "$SERVICE_PREFIX-outbox.service"
systemctl --user restart "$SERVICE_PREFIX-outbox.service"
if [[ "$TARGET" == production ]]; then
  systemctl --user enable --now met-jira-scheduler.service
  systemctl --user restart met-jira-scheduler.service
else
  systemctl --user disable --now met-jira-staging-scheduler.service >/dev/null 2>&1 || true
fi
echo "MET Jira $TARGET is starting on http://$(hostname -I | awk '{print $1}'):$PORT"
