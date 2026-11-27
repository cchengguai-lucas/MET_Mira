# AGENTS.md — MET Jira (Mira)

MET intranet issue tracker: FastAPI backend, React frontend, PostgreSQL. This file is the same in both worktrees of this repo. Check which one you are in with `git branch --show-current`.

| Worktree | Branch | Environment | Port on 65 | Service |
| --- | --- | --- | --- | --- |
| `/home/hal/docker/mira/MET_Jira` | `production` | production | 8800 | `met-jira.service` |
| `/home/hal/docker/mira/MET_Jira-staging` | `staging` | staging | 8810 | `met-jira-staging.service` |

## Commands

Run from the worktree root as user `lucas` (`.venv` and `configs/ldap.env` belong to lucas; staging `.venv` is a symlink to `MET_Jira/.venv`).

```bash
# Backend
.venv/bin/ruff check <changed files>
.venv/bin/pytest -q <tests>                 # tests/backend, SQLite; Postgres tests need METJIRA_TEST_POSTGRES_URL

# Frontend (from web/)
npm run typecheck && npm test && npm run build
npm run i18n:check
# Browser flows. The default e2e port 8810 is live staging on 65, so set a free port.
METJIRA_E2E_PORT=<free port> npx playwright test e2e/<spec>.spec.ts

# After a model change: add an Alembic migration in backend/migrations/versions/, then
.venv/bin/python -m alembic upgrade head

# Restart staging after a change
systemctl --user restart met-jira-staging.service
curl -fsS http://127.0.0.1:8810/api/health
```

## Always

- Make normal changes, tests, and builds only in the staging worktree. Treat the production worktree as read-only.
- Release only when the user asks. Then merge `staging` into `production` with `git merge --no-ff staging` in the production worktree, run `python -m backend.ops bootstrap`, `npm run build` in `web/`, restart `met-jira.service`, and wait for `/api/health`. Run full `scripts/ops/release.sh` only when the user asks for full verification (全量测试).
- Do not copy files between worktrees. Release through git.
- On host 65, do not run `scripts/ops/deploy-staging.sh`, `install-edge-staging.sh`, or `rollback-staging.sh`. They target the Nginx/PgBouncer edge on :8801/:6433, which this host does not have (8801 is BOM).
- PostgreSQL on 65 is Docker `mira-postgres` on `127.0.0.1:5434` (from git-ignored `compose.override.yaml`), not `met-jira-postgres.service` on 5433.
- Keep staging data and production data separate (databases `met_jira_staging` and `met_jira`).
- Product invariants:
  - Delivery projects (`settings.work_mode=delivery`, default) are one finishable goal with an explicit target date.
  - Ongoing projects (`settings.work_mode=ongoing`) keep full issue workflow but have no delivery commitment, schedule baseline, or project-level delay risk.
  - Published schedule baselines are immutable and versioned (delivery projects only).
  - Schedule urgency is computed separately from business priority.
  - Automated schedule changes are suggestions. A PM must confirm them.
  - Agent answers about live work must come from an authorized data tool.
  - Durable AI memory is stored only after explicit user confirmation.
- Sticky table headers and pinned columns: follow `.cursor/rules/sticky-cells.mdc`.

## Documents

- Read `dlog/INDEX.md` first for development history, and `errors/INDEX.md` for error history. Open a day file only for the entries you need.
- Update `ARCHITECTURE.md` when the architecture or workflow changes (services, ports, data flow, deployment).
- Update `DATA_MODEL.md` when a model, migration, data store, data path, or mount changes.
- Update `SECURITY.md` only on a beta container deployment or a security-check request.
- `README.md` (Chinese) and `.cursor/rules/staging-release-workflow.mdc` describe host 63 (ports 8801, 5433). On host 65 use the ports in this file and in `/home/hal/docker/mira/README.md`.

## Guides

- `README.md` — read for configuration (`.env`, `configs/ldap.env`), backup, Excel import, and the LLM assistant.
- `.cursor/rules/staging-release-workflow.mdc` — read before a release.
- `web/src/pages/mira-login/PROVENANCE.md` — read before you change the login page theme.
- `/home/hal/docker/mira/MET_Jira-ops/AGENTS.md` — read when a change affects presence files, health checks, or the database grants that Ops uses.

## Git
- If your working directory is under `/home/hal/docker/`, it is a **deploy tree**: containers run from it. Do not edit tracked files or commit there.
- Edit code in your own clone instead: `bash /home/hal/docker/scripts/clone-app.sh <repo>` (creates `~/git/<repo>`), work on a `<user>/<topic>` branch, push to origin (`/data/git_origin/<repo>.git`).
- Update a deploy tree only with `bash /home/hal/docker/scripts/deploy-app.sh <app>`, then run the build/restart commands from this file in the deploy tree.
- Never run `git add -A`, `git stash`, `git reset --hard`, `git checkout <branch>` or `git checkout .` in a deploy tree.
- Workflow and repo map: `/home/hal/docker/scripts/GIT_WORKFLOW.md`.
