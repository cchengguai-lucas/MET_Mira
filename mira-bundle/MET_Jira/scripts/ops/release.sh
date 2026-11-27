#!/usr/bin/env bash
# Release staging to production: verify staging, merge staging -> production, rebuild, restart :8800.
# Run only when a release has been explicitly requested.
set -euo pipefail

SELF_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
worktree_of() {
  git -C "$SELF_ROOT" worktree list --porcelain \
    | awk -v ref="refs/heads/$1" '/^worktree /{wt=$2} $0 == "branch " ref {print wt}'
}
PROD="$(worktree_of production)"
STAGING="$(worktree_of staging)"
if [[ -z "$PROD" || -z "$STAGING" ]]; then
  echo "need both production and staging worktrees (see git worktree list)" >&2
  exit 1
fi

for tree in "$PROD" "$STAGING"; do
  if [[ -n "$(git -C "$tree" status --porcelain)" ]]; then
    echo "uncommitted changes in $tree; commit or stash them first" >&2
    exit 1
  fi
done

if [[ -z "$(git -C "$PROD" log --oneline production..staging)" ]]; then
  echo "production already contains staging; nothing to release"
  exit 0
fi
echo "releasing:"
git -C "$PROD" log --oneline production..staging

echo "== verifying staging"
(cd "$STAGING" && python3 -m pytest -q && python3 -m ruff check backend tests)
"$STAGING/scripts/ops/backup.sh" migration-dry-run
(
  cd "$STAGING/web"
  npm ci
  npm run typecheck
  npm test
  npm run i18n:check
  npm run build
  npm run e2e
)

echo "== merging staging into production"
before="$(git -C "$PROD" rev-parse HEAD)"
git -C "$PROD" merge --no-ff staging -m "Merge branch 'staging'"

if ! git -C "$PROD" diff --quiet "$before" HEAD -- pyproject.toml; then
  echo "pyproject.toml changed; installing Python dependencies"
  # The service runs from the worktree, so only the dependency list is installed (no editable build).
  mapfile -t deps < <(python3 - "$PROD/pyproject.toml" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.S | re.M)
print("\n".join(re.findall(r'"([^"]+)"', block.group(1))) if block else "")
PY
  )
  if (( ${#deps[@]} )) && [[ -n "${deps[0]}" ]]; then
    python3 -m pip install --user -q "${deps[@]}"
  fi
fi

echo "== bootstrapping production database"
(
  cd "$PROD"
  METJIRA_DATA_DIR="$PROD/data" METJIRA_DEPLOY_ENV=production \
    python3 -m backend.ops bootstrap
)

echo "== building web"
(
  cd "$PROD/web"
  npm ci
  npm run build
)

echo "== restarting production"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
for role in scheduler outbox; do
  sed \
    -e "s#@ROOT@#$PROD#g" \
    -e "s#@ENV@#production#g" \
    -e "s#@DATA_DIR@#$PROD/data#g" \
    "$PROD/scripts/ops/systemd/met-jira-$role.service" \
    >"$UNIT_DIR/met-jira-$role.service"
done
systemctl --user daemon-reload
systemctl --user enable --now met-jira-scheduler.service met-jira-outbox.service
systemctl --user restart met-jira-scheduler.service met-jira-outbox.service
systemctl --user restart met-jira.service
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8800/api/health >/dev/null 2>&1; then
    echo "production is healthy at http://$(hostname -I | awk '{print $1}'):8800 ($(git -C "$PROD" rev-parse --short HEAD))"
    exit 0
  fi
  sleep 1
done
echo "production did not become healthy; see: journalctl --user -u met-jira.service -n 80" >&2
exit 1
