#!/usr/bin/env python3
"""Render staging edge runtime files without committing credentials."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

PLACEHOLDER = re.compile(r"@[A-Z][A-Z0-9_]*@")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_secure(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _render(template: Path, replacements: dict[str, str]) -> str:
    content = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"@{key}@", value)
    unresolved = sorted(set(PLACEHOLDER.findall(content)))
    if unresolved:
        raise ValueError(f"unresolved placeholders in {template}: {', '.join(unresolved)}")
    return content


def _pool_url(direct_url: str) -> tuple[str, str, str, str, int]:
    parsed = urlsplit(direct_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("METJIRA_DATABASE_URL must use postgresql+psycopg")
    if parsed.hostname not in {"127.0.0.1", "localhost"} or (parsed.port or 5432) != 5433:
        raise ValueError("staging source DATABASE_URL must point directly to 127.0.0.1:5433")
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = unquote(parsed.path.lstrip("/"))
    if not username or not password or not database:
        raise ValueError("DATABASE_URL must include user, password, and database")
    if not SAFE_IDENTIFIER.fullmatch(username) or not SAFE_IDENTIFIER.fullmatch(database):
        raise ValueError(
            "database user and name must contain only letters, digits, '.', '_' or '-'"
        )
    if database != "met_jira_staging":
        raise ValueError("edge generation is restricted to the met_jira_staging database")

    userinfo = parsed.netloc.rsplit("@", 1)[0]
    pooled = urlunsplit(
        (parsed.scheme, f"{userinfo}@127.0.0.1:6433", parsed.path, parsed.query, parsed.fragment)
    )
    return pooled, username, password, database, parsed.port or 5432


def read_auth_secret(
    *,
    username: str,
    password: str,
    database: str,
    port: int,
    psql_bin: str,
) -> str:
    env = {**os.environ, "PGPASSWORD": password}
    command = [
        psql_bin,
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        "127.0.0.1",
        "-p",
        str(port),
        "-U",
        username,
        "-d",
        database,
        "-c",
        "select rolpassword from pg_authid where rolname = current_user",
    ]
    result = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    secret = result.stdout.strip()
    if result.returncode == 0 and secret.startswith("SCRAM-SHA-256$"):
        return secret
    # PgBouncer accepts a plaintext auth_file secret and still performs SCRAM
    # on both sides. The file is generated at runtime with mode 0600.
    if password:
        return password
    raise RuntimeError(f"could not obtain PostgreSQL SCRAM verifier: {result.stderr.strip()}")


def _auth_field(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def generate(
    *,
    root: Path,
    runtime_dir: Path,
    state_dir: Path,
    unit_dir: Path,
    pgbouncer_bin: str,
    nginx_bin: str,
    direct_url: str | None = None,
    auth_secret: str | None = None,
    psql_bin: str | None = None,
) -> list[Path]:
    root = root.resolve()
    values = load_env(root / ".env")
    source_url = direct_url or os.environ.get("METJIRA_DATABASE_URL") or values.get(
        "METJIRA_DATABASE_URL", ""
    )
    pooled_url, username, password, database, postgres_port = _pool_url(source_url)
    psql = psql_bin or shutil.which("psql") or "/usr/lib/postgresql/16/bin/psql"
    secret = auth_secret or read_auth_secret(
        username=username,
        password=password,
        database=database,
        port=postgres_port,
        psql_bin=psql,
    )

    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_dir.chmod(0o700)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    for child in ("client_body", "proxy"):
        (runtime_dir / child).mkdir(mode=0o700, exist_ok=True)

    generated: list[Path] = []
    app_env = runtime_dir / "app.env"
    _write_secure(
        app_env,
        "\n".join(
            (
                f"METJIRA_DATABASE_URL={pooled_url}",
                "METJIRA_DB_CONNECT_TIMEOUT=5",
                "METJIRA_DB_APPLICATION_NAME=met-jira-staging",
                "METJIRA_DB_PREPARE_THRESHOLD=-1",
                "",
            )
        ),
        0o600,
    )
    generated.append(app_env)

    userlist = runtime_dir / "userlist.txt"
    _write_secure(userlist, f"{_auth_field(username)} {_auth_field(secret)}\n", 0o600)
    generated.append(userlist)

    common = {
        "ROOT": str(root),
        "RUNTIME_DIR": str(runtime_dir),
        "STATE_DIR": str(state_dir),
        "DATABASE": database,
        "DB_USER": username,
        "USERLIST": str(userlist),
        "PGBOUNCER_BIN": pgbouncer_bin,
        "NGINX_BIN": nginx_bin,
    }
    rendered = (
        (
            root / "configs/pgbouncer/pgbouncer.ini.template",
            runtime_dir / "pgbouncer.ini",
            0o600,
        ),
        (
            root / "configs/nginx/met-jira-staging.conf.template",
            runtime_dir / "nginx.conf",
            0o600,
        ),
        (
            root / "scripts/ops/systemd/met-jira-staging.service",
            unit_dir / "met-jira-staging.service",
            0o644,
        ),
        (
            root / "scripts/ops/systemd/met-jira-staging-pgbouncer.service",
            unit_dir / "met-jira-staging-pgbouncer.service",
            0o644,
        ),
        (
            root / "scripts/ops/systemd/met-jira-staging-nginx.service",
            unit_dir / "met-jira-staging-nginx.service",
            0o644,
        ),
    )
    for template, destination, mode in rendered:
        _write_secure(destination, _render(template, common), mode)
        generated.append(destination)

    role_common = {**common, "ENV": "staging", "DATA_DIR": "%h/.met_jira_staging"}
    for role in ("outbox", "scheduler"):
        template = root / f"scripts/ops/systemd/met-jira-{role}.service"
        destination = unit_dir / f"met-jira-staging-{role}.service"
        _write_secure(destination, _render(template, role_common), 0o644)
        generated.append(destination)
    return generated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--unit-dir", type=Path, required=True)
    parser.add_argument("--pgbouncer-bin", required=True)
    parser.add_argument("--nginx-bin", required=True)
    args = parser.parse_args()
    generated = generate(**vars(args))
    print(f"generated {len(generated)} staging edge files (credentials omitted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
