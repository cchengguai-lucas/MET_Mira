#!/usr/bin/env bash
# Install user-level edge dependencies and render units; does not switch traffic.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
require_staging_worktree

for command in nginx python3 psql systemctl curl ss dpkg-deb apt-get; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 1
  }
done

if ! find_pgbouncer >/dev/null 2>&1; then
  echo "PgBouncer is not system-installed; installing the signed apt package under ~/.local"
  temporary="$(mktemp -d)"
  trap 'rm -rf "$temporary"' EXIT
  (
    cd "$temporary"
    apt-get download pgbouncer libevent-2.1-7
    package="$(printf '%s\n' pgbouncer_*.deb | sort -V | tail -n 1)"
    dpkg-deb -x "$package" extracted
    event_package="$(printf '%s\n' libevent-2.1-7_*.deb | sort -V | tail -n 1)"
    dpkg-deb -x "$event_package" extracted
  )
  test -x "$temporary/extracted/usr/sbin/pgbouncer"
  mkdir -p "$HOME/.local/lib"
  rm -rf "$HOME/.local/lib/met-jira-pgbouncer.new"
  mv "$temporary/extracted" "$HOME/.local/lib/met-jira-pgbouncer.new"
  rm -rf "$HOME/.local/lib/met-jira-pgbouncer"
  mv "$HOME/.local/lib/met-jira-pgbouncer.new" "$HOME/.local/lib/met-jira-pgbouncer"
fi

if [[ "$(find_pgbouncer)" == "$LOCAL_PGBOUNCER" ]] \
  && LD_LIBRARY_PATH="$LOCAL_PGBOUNCER_LIB" ldd "$LOCAL_PGBOUNCER" | grep -q 'not found'; then
  echo "repairing user-local PgBouncer shared libraries"
  temporary="$(mktemp -d)"
  trap 'rm -rf "$temporary"' EXIT
  (
    cd "$temporary"
    apt-get download libevent-2.1-7
    package="$(printf '%s\n' libevent-2.1-7_*.deb | sort -V | tail -n 1)"
    dpkg-deb -x "$package" extracted
  )
  mkdir -p "$LOCAL_PGBOUNCER_LIB"
  cp -a "$temporary/extracted/usr/lib/x86_64-linux-gnu/." "$LOCAL_PGBOUNCER_LIB/"
fi
if [[ "$(find_pgbouncer)" == "$LOCAL_PGBOUNCER" ]] \
  && LD_LIBRARY_PATH="$LOCAL_PGBOUNCER_LIB" ldd "$LOCAL_PGBOUNCER" | grep -q 'not found'; then
  echo "the user-local PgBouncer package still has unresolved shared libraries" >&2
  LD_LIBRARY_PATH="$LOCAL_PGBOUNCER_LIB" ldd "$LOCAL_PGBOUNCER" >&2
  exit 1
fi

generate_edge_config
nginx -t -p "$RUNTIME_DIR/" -c "$RUNTIME_DIR/nginx.conf"
systemctl --user daemon-reload
echo "edge units installed but not started; run scripts/ops/deploy-staging.sh to switch staging"
