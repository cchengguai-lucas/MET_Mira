#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/met-jira-staging-edge"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/met-jira-staging-edge"
UNIT_DIR="$HOME/.config/systemd/user"
LOCAL_PGBOUNCER="$HOME/.local/lib/met-jira-pgbouncer/usr/sbin/pgbouncer"
LOCAL_PGBOUNCER_LIB="$HOME/.local/lib/met-jira-pgbouncer/usr/lib/x86_64-linux-gnu"

find_pgbouncer() {
  if command -v pgbouncer >/dev/null 2>&1; then
    command -v pgbouncer
  elif [[ -x "$LOCAL_PGBOUNCER" ]]; then
    printf '%s\n' "$LOCAL_PGBOUNCER"
  else
    return 1
  fi
}

require_staging_worktree() {
  if [[ "$(git -C "$ROOT" branch --show-current)" != staging ]]; then
    echo "must run from the staging worktree: $ROOT" >&2
    exit 1
  fi
  if [[ "$ROOT" == */MET_Jira ]]; then
    echo "refusing to operate on the production worktree" >&2
    exit 1
  fi
}

generate_edge_config() {
  local pgbouncer_bin nginx_bin
  pgbouncer_bin="$(find_pgbouncer)"
  nginx_bin="$(command -v nginx)"
  mkdir -p "$UNIT_DIR"
  python3 "$ROOT/scripts/ops/edge_config.py" \
    --root "$ROOT" \
    --runtime-dir "$RUNTIME_DIR" \
    --state-dir "$STATE_DIR" \
    --unit-dir "$UNIT_DIR" \
    --pgbouncer-bin "$pgbouncer_bin" \
    --nginx-bin "$nginx_bin"
}

wait_http() {
  local url="$1" attempts="${2:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
