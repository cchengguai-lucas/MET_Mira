#!/usr/bin/env bash
# Generate 0600 PgBouncer credentials/config and staging edge units from .env.
set -euo pipefail

# shellcheck source=edge-common.sh
source "$(cd "$(dirname "$0")" && pwd)/edge-common.sh"
require_staging_worktree
find_pgbouncer >/dev/null 2>&1 || {
  echo "PgBouncer is missing; run scripts/ops/install-edge-staging.sh" >&2
  exit 1
}
command -v nginx >/dev/null 2>&1 || {
  echo "Nginx is missing" >&2
  exit 1
}
generate_edge_config
