# AGENTS.md — MET Jira Ops

Operations and usage console for MET Jira (Mira) on host `10.113.17.65`, port 8803. Separate repo, branch `main`, service `met-jira-ops.service` (user-level systemd, user lucas). It watches the production (8800) and staging (8810) MET Jira services. Feature and login details are in `README.md` (Chinese).

## Commands

Run from `/home/hal/docker/mira/MET_Jira-ops` as user `lucas`. The host Python of other users does not have the dependencies.

```bash
python3 -m pytest -q
python3 -m ruff check .

# After a code or config change
systemctl --user restart met-jira-ops.service
journalctl --user -u met-jira-ops.service -f
```

Ask before you run `scripts/install.sh`. It creates or changes the PostgreSQL role `met_jira_ops`, writes `configs/ops.env`, and installs the systemd unit.

## Always

- Never import MET Jira application code. Read MET Jira only through its database, its presence files, `/api/health`, `systemctl --user`, and `journalctl --user`.
- Database access is read-only: role `met_jira_ops`, `SELECT` only, sessions forced read-only. Do not add writes to `met_jira` or `met_jira_staging`.
- Configure through `configs/ops.env` (git-ignored, mode 600) and `METJIRA_OPS_*` variables. Defaults in `ops_console/config.py` match host 65: prod 8800, staging 8810, PostgreSQL `127.0.0.1:5434`, app root `/home/hal/docker/mira/MET_Jira`.
- LDAP settings are not stored here. They come from `MET_Jira/configs/ldap.env` (see `README.md` for the order). Do not show the LDAP password on any page.
- The ops-owned store is SQLite. Keep the 120-day retention unless the user asks.

## Documents

- Read `dlog/INDEX.md` first for development history, and `errors/INDEX.md` for error history. Open a day file only for the entries you need.
- Update `ARCHITECTURE.md` when the architecture or workflow changes (probes, data sources, ports, units).
- Update `DATA_MODEL.md` when the SQLite schema, the presence file format, or a data path changes.
- Create or update `SECURITY.md` only on a beta container deployment or a security-check request.

## Guides

- `README.md` — read for pages, online rules, login, LDAP order, and installation.
- `/home/hal/docker/mira/MET_Jira/AGENTS.md` — read when a change depends on MET Jira tables, health output, or presence files.

## Git
- If your working directory is under `/home/hal/docker/`, it is a **deploy tree**: containers run from it. Do not edit tracked files or commit there.
- Edit code in your own clone instead: `bash /home/hal/docker/scripts/clone-app.sh <repo>` (creates `~/git/<repo>`), work on a `<user>/<topic>` branch, push to origin (`/data/git_origin/<repo>.git`).
- Update a deploy tree only with `bash /home/hal/docker/scripts/deploy-app.sh <app>`, then run the build/restart commands from this file in the deploy tree.
- Never run `git add -A`, `git stash`, `git reset --hard`, `git checkout <branch>` or `git checkout .` in a deploy tree.
- Workflow and repo map: `/home/hal/docker/scripts/GIT_WORKFLOW.md`.
