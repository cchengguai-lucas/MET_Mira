# DATA_MODEL.md — MET Jira Ops

Ops owns one SQLite store. It reads, but does not own, the MET Jira databases and presence files (see `/home/hal/docker/mira/MET_Jira/DATA_MODEL.md`).

## SQLite store `ops.sqlite3`

Schema in `ops_console/store.py` (created with `create table if not exists`, WAL mode). Time zone for `day` is `Asia/Shanghai`. Rows older than 120 days are deleted daily.

| Table | Columns | Key / index |
| --- | --- | --- |
| `samples` | `ts` int, `env` text, `online` int | index `samples_ts (ts)` |
| `sample_users` | `ts` int, `env` text, `user_id` int, `label` text | index `sample_users_env_ts (env, ts)` |
| `usage` | `day` text, `env` text, `user_id` int, `label` text, `page` text, `seconds` int | primary key `(day, env, user_id, page)`; upsert adds `seconds` |
| `probes` | `ts` int, `target` text, `ok` int, `latency_ms` int | index `probes_target_ts (target, ts)` |

All columns are `not null` except `probes.latency_ms`. There are no foreign keys. `env` is `production` or `staging`. `user_id` is the MET Jira `users.id`.

## External data (read only)

- PostgreSQL `met_jira` and `met_jira_staging` on `127.0.0.1:5434`, role `met_jira_ops` (`SELECT` only, `default_transaction_read_only = on`).
- Presence: `presence/{production,staging}/` (one JSON file per user) and `presence/events/YYYY-MM-DD.jsonl` (login, logout, Excel export).

## Storage locations

| Store | Location | Mount |
| --- | --- | --- |
| Ops store | `~/.met_jira_ops/ops.sqlite3` (user lucas) | none (systemd service on the host) |
| Presence | `~/.met_jira_ops/presence` | none |

## Known deviations

- Known deviation: the ops store and the presence files are in the home directory of user lucas, not under `/data/mira/...`.
