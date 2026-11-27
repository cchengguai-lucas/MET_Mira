"""MET Jira Ops console settings. Nothing here imports MET Jira application code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent
OPS_ROOT = APP_DIR.parent
TZ = ZoneInfo("Asia/Shanghai")


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _path(name: str, default: str) -> Path:
    return Path(_env(name, default)).expanduser()


OPS_HOME = _path("METJIRA_OPS_HOME", "~/.met_jira_ops")
PRESENCE_ROOT = _path("METJIRA_PRESENCE_ROOT", str(OPS_HOME / "presence"))
STORE_PATH = _path("METJIRA_OPS_STORE", str(OPS_HOME / "ops.sqlite3"))

SESSION_COOKIE = "metjira_ops_session"
SESSION_MAX_AGE = 12 * 60 * 60

# MET Jira heartbeats every 30s while the tab is visible and the user is active.
ONLINE_TTL_SECONDS = int(_env("METJIRA_OPS_ONLINE_TTL", "120"))
SAMPLE_SECONDS = 15
PROBE_SECONDS = 60
RETENTION_DAYS = 120

LLM_BASE_URL = _env("METJIRA_LLM_BASE_URL", "http://10.113.17.65:8001/v1").rstrip("/")
PUBLIC_HOST = _env("METJIRA_OPS_PUBLIC_HOST", "10.113.17.65")

APP_ROOT = _path("METJIRA_OPS_APP_ROOT", "/home/hal/docker/mira/MET_Jira")
# Later files override earlier ones; process env overrides all (same order as MET Jira itself).
_LDAP_OVERRIDE = _env("METJIRA_OPS_LDAP_ENV", "")
_LDAP_SHARED = _env(
    "METJIRA_OPS_LDAP_SHARED",
    str(APP_ROOT / "configs" / "ldap.env"),
)
LDAP_ENV_FILES = (
    Path(_LDAP_SHARED).expanduser(),
    APP_ROOT / "configs" / "ldap.env",
    OPS_ROOT / "configs" / "ldap.env",
) + ((Path(_LDAP_OVERRIDE).expanduser(),) if _LDAP_OVERRIDE else ())
# AD accounts allowed into Ops besides MET Jira system admins.
OPS_USERS = frozenset(
    name.strip().lower() for name in _env("METJIRA_OPS_USERS", "").split(",") if name.strip()
)


@dataclass(frozen=True)
class AppEnv:
    key: str
    label: str
    port: int
    unit: str
    dsn: str
    data_dir: Path

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/health"

    @property
    def public_url(self) -> str:
        return f"http://{PUBLIC_HOST}:{self.port}"


def _dsn(name: str, database: str) -> str:
    password = _env("METJIRA_OPS_DB_PASSWORD", "")
    host = _env("METJIRA_OPS_PG_HOST", "127.0.0.1")
    port = _env("METJIRA_OPS_PG_PORT", "5434")
    default = f"postgresql://met_jira_ops:{password}@{host}:{port}/{database}"
    return _env(name, default)


ENVS: dict[str, AppEnv] = {
    "production": AppEnv(
        key="production",
        label="Production",
        port=int(_env("METJIRA_OPS_PROD_PORT", "8800")),
        unit=_env("METJIRA_OPS_PROD_UNIT", "met-jira.service"),
        dsn=_dsn("METJIRA_OPS_PROD_DSN", "met_jira"),
        data_dir=_path("METJIRA_OPS_PROD_DATA", str(APP_ROOT / "data")),
    ),
    "staging": AppEnv(
        key="staging",
        label="Staging",
        port=int(_env("METJIRA_OPS_STAGING_PORT", "8810")),
        unit=_env("METJIRA_OPS_STAGING_UNIT", "met-jira-staging.service"),
        dsn=_dsn("METJIRA_OPS_STAGING_DSN", "met_jira_staging"),
        data_dir=_path("METJIRA_OPS_STAGING_DATA", "~/.met_jira_staging"),
    ),
}

_PG_PORT = _env("METJIRA_OPS_PG_PORT", "5434")
INFRA_UNITS = (
    (
        _env("METJIRA_OPS_POSTGRES_UNIT", "met-jira-postgres.service"),
        f"PostgreSQL :{_PG_PORT}",
    ),
    ("met-jira-ops.service", "Ops console :8803"),
)
# Docker volume for mira-postgres (compose.override.yaml maps 127.0.0.1:5434).
PGDATA = _path("METJIRA_PGDATA", "/data/docker/volumes/mira_mira-pg/_data")


def get_env(key: str | None) -> AppEnv:
    return ENVS.get((key or "production").lower(), ENVS["production"])
