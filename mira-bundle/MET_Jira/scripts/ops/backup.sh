#!/usr/bin/env bash
# Production backups: PostgreSQL dump + attachment/import files, with rotation and restore drills.
#
#   scripts/ops/backup.sh backup         # dump met_jira + data/, then prune
#   scripts/ops/backup.sh restore-check  # restore the newest backup into a scratch database and verify it
#   scripts/ops/backup.sh list           # show backups and the last drill result
#
# Layout under METJIRA_BACKUP_DIR (default ~/.met_jira_backups):
#   daily/<stamp>/{db.dump,data.tar.gz,manifest.json}   newest METJIRA_BACKUP_KEEP_DAILY kept (14)
#   weekly/<stamp>/                                      Sunday copies, newest METJIRA_BACKUP_KEEP_WEEKLY kept (8)
#   last-backup.json, last-restore-check.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/16/bin}"
PGHOST="${METJIRA_PGHOST:-127.0.0.1}"
PGPORT="${METJIRA_PGPORT:-5433}"
PGUSER="${METJIRA_DB_USER:-met_jira}"
export PGPASSWORD="${METJIRA_DB_PASSWORD:-met_jira_dev}"
DB_NAME="${METJIRA_BACKUP_DB:-met_jira}"
CHECK_DB="${DB_NAME}_restore_check"
BACKUP_DIR="${METJIRA_BACKUP_DIR:-$HOME/.met_jira_backups}"
KEEP_DAILY="${METJIRA_BACKUP_KEEP_DAILY:-14}"
KEEP_WEEKLY="${METJIRA_BACKUP_KEEP_WEEKLY:-8}"

# Attachments live in the production worktree's data/ directory.
production_root() {
  if [[ -n "${METJIRA_PROD_ROOT:-}" ]]; then
    echo "$METJIRA_PROD_ROOT"
    return
  fi
  git -C "$ROOT" worktree list --porcelain | awk '
    /^worktree / { path = substr($0, 10) }
    /^branch refs\/heads\/production$/ { print path; exit }'
}
DATA_DIR="${METJIRA_BACKUP_DATA_DIR:-$(production_root)/data}"

pg() { "$PG_BIN/$1" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "${@:2}"; }
query() { pg psql -d "$1" -v ON_ERROR_STOP=1 -tAc "$2"; }
log() { echo "[$(date '+%F %T')] $*"; }

# Row counts recorded at backup time and compared after a restore.
COUNT_TABLES=(users projects issues comments attachments activity_logs saved_filters)
counts_json() {
  local db="$1" sep="" out="{"
  for table in "${COUNT_TABLES[@]}"; do
    out+="$sep\"$table\": $(query "$db" "select count(*) from $table")"
    sep=", "
  done
  echo "$out}"
}

prune() {
  local kind="$1" keep="$2"
  [[ -d "$BACKUP_DIR/$kind" ]] || return 0
  find "$BACKUP_DIR/$kind" -mindepth 1 -maxdepth 1 -type d -name '*.partial' -mmin +360 \
    -exec rm -rf {} +
  find "$BACKUP_DIR/$kind" -mindepth 1 -maxdepth 1 -type d ! -name '*.partial' -printf '%f\n' \
    | sort -r | tail -n +"$((keep + 1))" | while read -r old; do
      rm -rf "${BACKUP_DIR:?}/$kind/$old"
      log "pruned $kind/$old"
    done
}

backup() {
  local stamp target final
  stamp="$(date +%Y%m%d-%H%M%S)"
  final="$BACKUP_DIR/daily/$stamp"
  target="$final.partial"
  mkdir -p "$target"
  chmod 700 "$BACKUP_DIR"

  log "dumping $DB_NAME"
  pg pg_dump -Fc --no-owner -d "$DB_NAME" -f "$target/db.dump"
  local counts revision
  counts="$(counts_json "$DB_NAME")"
  revision="$(query "$DB_NAME" "select version_num from alembic_version")"

  log "archiving $DATA_DIR"
  if [[ -d "$DATA_DIR" ]]; then
    tar -czf "$target/data.tar.gz" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
  else
    tar -czf "$target/data.tar.gz" --files-from /dev/null
  fi

  local files
  files="$(find "$DATA_DIR" -type f 2>/dev/null | wc -l)"
  cat >"$target/manifest.json" <<EOF
{
  "created_at": "$(date -Iseconds)",
  "database": "$DB_NAME",
  "alembic_revision": "$revision",
  "data_dir": "$DATA_DIR",
  "data_files": $files,
  "counts": $counts,
  "db_dump_bytes": $(stat -c %s "$target/db.dump"),
  "data_bytes": $(stat -c %s "$target/data.tar.gz"),
  "sha256": {
    "db.dump": "$(sha256sum "$target/db.dump" | cut -d' ' -f1)",
    "data.tar.gz": "$(sha256sum "$target/data.tar.gz" | cut -d' ' -f1)"
  }
}
EOF
  chmod -R go-rwx "$target"
  mv "$target" "$final"

  if [[ "$(date +%u)" == 7 || ! -d "$BACKUP_DIR/weekly" || -z "$(ls -A "$BACKUP_DIR/weekly")" ]]; then
    mkdir -p "$BACKUP_DIR/weekly"
    cp -al "$final" "$BACKUP_DIR/weekly/$stamp"
    log "kept weekly/$stamp"
  fi
  cp "$final/manifest.json" "$BACKUP_DIR/last-backup.json"
  prune daily "$KEEP_DAILY"
  prune weekly "$KEEP_WEEKLY"
  log "backup complete: $final ($(du -sh "$final" | cut -f1))"
}

latest() {
  find "$BACKUP_DIR/daily" -mindepth 1 -maxdepth 1 -type d ! -name '*.partial' -printf '%f\n' \
    2>/dev/null | sort -r | head -1
}

restore_check() {
  local name dir scratch status="ok" problems=()
  name="${1:-$(latest)}"
  if [[ -z "$name" ]]; then
    echo "no backup found in $BACKUP_DIR/daily" >&2
    exit 1
  fi
  dir="$BACKUP_DIR/daily/$name"
  [[ -d "$dir" ]] || dir="$BACKUP_DIR/weekly/$name"
  log "restore drill for $dir into $CHECK_DB"

  local expected
  expected="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' \
    "$dir/manifest.json")"
  for file in db.dump data.tar.gz; do
    local want got
    want="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"][sys.argv[2]])' \
      "$dir/manifest.json" "$file")"
    got="$(sha256sum "$dir/$file" | cut -d' ' -f1)"
    [[ "$want" == "$got" ]] || problems+=("checksum mismatch: $file")
  done

  pg dropdb --if-exists "$CHECK_DB"
  pg createdb "$CHECK_DB"
  trap 'pg dropdb --if-exists "$CHECK_DB" >/dev/null 2>&1 || true; rm -rf "${scratch:-}"' EXIT
  pg pg_restore --no-owner --exit-on-error -d "$CHECK_DB" "$dir/db.dump"

  local restored
  restored="$(counts_json "$CHECK_DB")"
  local diff
  diff="$(python3 - "$expected" "$restored" <<'PY'
import json, sys
expected = json.loads(sys.argv[1])["counts"]
restored = json.loads(sys.argv[2])
print("; ".join(f"{t}: {expected[t]} != {restored.get(t)}" for t in expected
                if expected[t] != restored.get(t)))
PY
)"
  [[ -z "$diff" ]] || problems+=("row counts differ: $diff")

  scratch="$(mktemp -d)"
  tar -xzf "$dir/data.tar.gz" -C "$scratch"
  find "$scratch" -type f -printf '%f\n' | sort -u >"$scratch/.present"
  query "$CHECK_DB" "select stored_name from attachments" | sort -u >"$scratch/.expected"
  local missing checked
  checked="$(grep -c . "$scratch/.expected" || true)"
  missing="$(comm -23 "$scratch/.expected" "$scratch/.present" | grep -c . || true)"
  ((missing == 0)) || problems+=("$missing of $checked attachment files missing from data.tar.gz")

  ((${#problems[@]} == 0)) || status="failed"
  python3 - "$BACKUP_DIR/last-restore-check.json" "$name" "$status" "$restored" "$checked" \
    "${problems[@]}" <<'PY'
import json, sys
from datetime import datetime
path, name, status, restored, checked, *problems = sys.argv[1:]
json.dump({"checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "backup": name, "status": status, "counts": json.loads(restored),
           "attachments_checked": int(checked), "problems": problems},
          open(path, "w"), ensure_ascii=False, indent=2)
PY
  if [[ "$status" == ok ]]; then
    log "restore drill passed: $restored, $checked attachment files present"
  else
    log "restore drill FAILED: ${problems[*]}"
    exit 1
  fi
}

migration_dry_run() {
  local name dir scratch_db before after expected_head current
  name="${1:-$(latest)}"
  if [[ -z "$name" ]]; then
    echo "no backup found in $BACKUP_DIR/daily" >&2
    exit 1
  fi
  dir="$BACKUP_DIR/daily/$name"
  [[ -d "$dir" ]] || dir="$BACKUP_DIR/weekly/$name"
  scratch_db="${DB_NAME}_migration_check"
  log "migration dry-run for $dir into $scratch_db"
  pg dropdb --if-exists "$scratch_db"
  pg createdb "$scratch_db"
  trap "pg dropdb --if-exists '$scratch_db' >/dev/null 2>&1 || true" EXIT
  pg pg_restore --no-owner --exit-on-error -d "$scratch_db" "$dir/db.dump"
  before="$(counts_json "$scratch_db")"
  local db_url
  db_url="$(python3 - "$PGUSER" "$PGPASSWORD" "$PGHOST" "$PGPORT" "$scratch_db" <<'PY'
import sys
from urllib.parse import quote
user, password, host, port, database = sys.argv[1:]
print(f"postgresql+psycopg://{quote(user)}:{quote(password)}@{host}:{port}/{database}")
PY
)"
  (
    cd "$ROOT"
    METJIRA_DATABASE_URL="$db_url" PYTHONPATH="$ROOT" python3 -m alembic upgrade head
  )
  after="$(counts_json "$scratch_db")"
  [[ "$before" == "$after" ]] || {
    echo "migration dry-run changed protected row counts: $before -> $after" >&2
    exit 1
  }
  expected_head="$(cd "$ROOT" && PYTHONPATH="$ROOT" python3 -m alembic heads | awk '{print $1}')"
  current="$(query "$scratch_db" "select version_num from alembic_version")"
  [[ "$current" == "$expected_head" ]] || {
    echo "migration dry-run stopped at $current, expected $expected_head" >&2
    exit 1
  }
  log "migration dry-run passed at revision $current: $after"
}

list() {
  for kind in daily weekly; do
    echo "$kind:"
    find "$BACKUP_DIR/$kind" -mindepth 1 -maxdepth 1 -type d -printf '  %f\n' 2>/dev/null | sort -r
  done
  [[ -f "$BACKUP_DIR/last-restore-check.json" ]] && {
    echo "last restore drill:"
    cat "$BACKUP_DIR/last-restore-check.json"
  }
  return 0
}

mkdir -p "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || { echo "another backup job is running" >&2; exit 1; }

case "${1:-backup}" in
  backup) backup ;;
  restore-check) restore_check "${2:-}" ;;
  migration-dry-run) migration_dry_run "${2:-}" ;;
  list) list ;;
  *) echo "usage: $0 {backup|restore-check [stamp]|migration-dry-run [stamp]|list}" >&2
     exit 2 ;;
esac
