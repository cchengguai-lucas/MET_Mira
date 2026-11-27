#!/usr/bin/env bash
# Install the MET Jira Ops console as a user-level systemd service on :8803.
# Creates the read-only PostgreSQL role met_jira_ops and configs/ops.env on first run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PG_HOST="${METJIRA_OPS_PG_HOST:-127.0.0.1}"
PG_PORT="${METJIRA_OPS_PG_PORT:-5434}"
ADMIN_PASSWORD="${METJIRA_DB_PASSWORD:-met_jira_dev}"
ENV_FILE="$ROOT/configs/ops.env"

if [[ ! -f "$ENV_FILE" ]]; then
  secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  printf 'METJIRA_OPS_SECRET=%s\nMETJIRA_OPS_DB_PASSWORD=%s\n' "$secret" "$db_password" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "created $ENV_FILE"
fi
if ! grep -q '^METJIRA_OPS_USERS=' "$ENV_FILE"; then
  printf 'METJIRA_OPS_USERS=%s\n' "${METJIRA_OPS_USERS:-}" >>"$ENV_FILE"
fi
db_password="$(sed -n 's/^METJIRA_OPS_DB_PASSWORD=//p' "$ENV_FILE")"

psql_as_owner() {
  if [[ -x "$PG_BIN/psql" ]]; then
    PGPASSWORD="$ADMIN_PASSWORD" "$PG_BIN/psql" -h "$PG_HOST" -p "$PG_PORT" -U met_jira -v ON_ERROR_STOP=1 -q "$@"
  else
    # Host 65: mira-postgres publishes 127.0.0.1:5434; use the container client when host psql is absent.
    docker exec -i -e PGPASSWORD="$ADMIN_PASSWORD" mira-postgres \
      psql -U met_jira -v ON_ERROR_STOP=1 -q "$@"
  fi
}

psql_as_owner -d postgres -v pw="$db_password" <<'SQL'
select format('create role met_jira_ops login password %L', :'pw')
where not exists (select 1 from pg_roles where rolname = 'met_jira_ops') \gexec
alter role met_jira_ops with login password :'pw';
alter role met_jira_ops set default_transaction_read_only = on;
SQL

for db in met_jira met_jira_staging; do
  if [[ -z "$(psql_as_owner -d postgres -tAc "select 1 from pg_database where datname = '$db'")" ]]; then
    echo "database $db not found; skipping grants"
    continue
  fi
  psql_as_owner -d "$db" <<SQL
grant connect on database $db to met_jira_ops;
grant usage on schema public to met_jira_ops;
grant select on all tables in schema public to met_jira_ops;
alter default privileges for role met_jira in schema public grant select on tables to met_jira_ops;
SQL
done

mkdir -p "$UNIT_DIR" "$HOME/.met_jira_ops/presence"
sed "s#@ROOT@#$ROOT#g" "$ROOT/systemd/met-jira-ops.service" >"$UNIT_DIR/met-jira-ops.service"
systemctl --user daemon-reload
systemctl --user enable met-jira-ops.service
systemctl --user restart met-jira-ops.service
echo "MET Jira Ops is starting on http://$(hostname -I | awk '{print $1}'):8803"
