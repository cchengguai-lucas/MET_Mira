#!/usr/bin/env bash
# User-owned PostgreSQL 16 cluster for MET Jira (no sudo / Docker Hub needed).
set -euo pipefail

PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PGDATA="${METJIRA_PGDATA:-$HOME/.met_jira/pgdata}"
PGPORT="${METJIRA_PGPORT:-5433}"
DB_USER="${METJIRA_DB_USER:-met_jira}"
DB_PASSWORD="${METJIRA_DB_PASSWORD:-met_jira_dev}"
DB_NAME="${METJIRA_DB_NAME:-met_jira}"
SOCKET_DIR="$(dirname "$PGDATA")/run"

cmd="${1:-status}"

init() {
  if [[ -f "$PGDATA/PG_VERSION" ]]; then
    echo "cluster already initialised at $PGDATA"
    return
  fi
  mkdir -p "$PGDATA" "$SOCKET_DIR"
  pwfile="$(mktemp)"
  printf '%s' "$DB_PASSWORD" >"$pwfile"
  "$PG_BIN/initdb" -D "$PGDATA" -U "$DB_USER" --pwfile="$pwfile" \
    --auth-local=scram-sha-256 --auth-host=scram-sha-256 \
    --encoding=UTF8 --locale=C.UTF-8 >/dev/null
  rm -f "$pwfile"
  cat >>"$PGDATA/postgresql.conf" <<EOF
listen_addresses = '127.0.0.1'
port = $PGPORT
unix_socket_directories = '$SOCKET_DIR'
EOF
  start
  PGPASSWORD="$DB_PASSWORD" "$PG_BIN/createdb" -h 127.0.0.1 -p "$PGPORT" -U "$DB_USER" "$DB_NAME"
  PGPASSWORD="$DB_PASSWORD" "$PG_BIN/createdb" -h 127.0.0.1 -p "$PGPORT" -U "$DB_USER" "${DB_NAME}_test"
  echo "initialised $DB_NAME on 127.0.0.1:$PGPORT"
}

start() {
  mkdir -p "$SOCKET_DIR"
  "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$(dirname "$PGDATA")/postgres.log" -w status >/dev/null 2>&1 \
    || "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$(dirname "$PGDATA")/postgres.log" -w start
}

case "$cmd" in
  init) init ;;
  start) start ;;
  run) mkdir -p "$SOCKET_DIR"; exec "$PG_BIN/postgres" -D "$PGDATA" ;;
  stop) "$PG_BIN/pg_ctl" -D "$PGDATA" -w stop ;;
  status) "$PG_BIN/pg_ctl" -D "$PGDATA" status ;;
  *) echo "usage: $0 {init|start|run|stop|status}" >&2; exit 2 ;;
esac
