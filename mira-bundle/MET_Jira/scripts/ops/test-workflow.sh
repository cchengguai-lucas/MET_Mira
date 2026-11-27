#!/usr/bin/env bash
# Workflow / gates / settings regression suite (API matrix + Playwright).
# Optional before release: run from the staging worktree.
# Does not replace scripts/ops/release.sh — invoke manually when needed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "==> pytest workflow matrix"
python3 -m pytest \
  tests/backend/test_workflow_walk.py \
  tests/backend/test_workflow_matrix.py \
  tests/backend/test_governance.py \
  tests/backend/test_issues.py \
  -q \
  --cov=backend.services.workflow \
  --cov=backend.services.approvals \
  --cov-report=term-missing

echo "==> build web (Playwright serves via uvicorn)"
(cd web && npm run build)

echo "==> reset e2e sqlite (avoid stale schema)"
rm -f /tmp/metjira-e2e.db
rm -rf /tmp/metjira-e2e-data
mkdir -p /tmp/metjira-e2e-data

echo "==> playwright workflow + core"
(cd web && CI=1 npx playwright test e2e/workflow.spec.ts e2e/core.spec.ts --workers=1)

echo "workflow tests OK"
