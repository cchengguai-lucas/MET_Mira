"""Runtime configuration read from environment variables and the project .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _load_dotenv(ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    return (os.environ.get(key) or _DOTENV.get(key) or default).strip()


def _bool(key: str, default: bool) -> bool:
    raw = _get(key)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout: int
    db_pool_recycle: int
    db_pool_use_lifo: bool
    db_connect_timeout: int
    db_application_name: str
    db_prepare_threshold: int
    proxy_trusted_ips: str
    web_host: str
    web_port: int
    web_workers: int
    secret: str
    auth_mode: str
    ad_admins: frozenset[str]
    admin_user: str
    admin_password: str
    session_hours: int
    cookie_name: str
    cookie_secure: bool
    deploy_env: str
    max_upload_mb: int
    attachment_reconcile_grace_seconds: int
    llm_enabled: bool
    llm_base_url: str
    llm_model: str
    llm_timeout: int
    org_sync_hour: int
    due_digest_hour: int
    report_weekday: str
    scheduler_enabled: bool
    outbox_batch_size: int
    outbox_lease_seconds: int
    outbox_poll_seconds: int
    login_max_failures: int
    login_window_seconds: int
    ai_requests_per_minute: int
    ai_max_concurrent: int
    ai_slot_lease_ttl_seconds: int
    ai_slot_heartbeat_seconds: int
    backup_dir: Path
    alert_webhook_url: str
    wecom_push_url: str
    wecom_push_token: str
    # Shared secret for machine callers of read-only external endpoints (Bearer / X-Api-Key).
    # Empty disables token auth; session cookies still work.
    external_api_token: str
    # Empty disables the reserved software-Jira bridge. The client does not call the network.
    software_jira_url: str
    software_jira_token: str
    public_url: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_from_name: str
    alert_redirect: str
    # Metrology Web XP bridge (award on METROLOGY project create).
    metrology_xp_url: str = ""
    metrology_xp_token: str = ""
    # Empty means every authenticated account may sign in. Staging sets this
    # so the shared lab host is not open to the whole directory.
    login_allowlist: frozenset[str] = field(default_factory=frozenset)
    # Opt-in sample ELEC project; default off so bootstrap never recreates it.
    # Tests set METJIRA_SEED_SAMPLE_PROJECT=true; a data_dir marker keeps it once-only.
    seed_sample_project: bool = False
    web_dist: Path = field(default=ROOT / "web" / "dist")

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def avatars_dir(self) -> Path:
        return self.data_dir / "avatars"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"


def login_allowed(username: str) -> bool:
    """Empty allowlist means every account may sign in."""
    allow = get_settings().login_allowlist
    if not allow:
        return True
    return username.strip().lower() in allow


_DEFAULT_SECRET = "met-jira-dev-secret-change-me"
_DEFAULT_ADMIN_PASSWORD = "MetJira123!"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(_get("METJIRA_DATA_DIR", str(ROOT / "data"))).resolve()
    secret = _get("METJIRA_SECRET", _DEFAULT_SECRET)
    admin_password = _get("METJIRA_ADMIN_PASSWORD", _DEFAULT_ADMIN_PASSWORD)
    deploy_env = _get("METJIRA_DEPLOY_ENV", "production").lower()
    # Session signing must never ship with the repo default. Admin bootstrap
    # password may stay lab-known on internal hosts; do not block startup on it.
    if deploy_env == "production" and secret == _DEFAULT_SECRET:
        raise RuntimeError(
            "Refusing to start production with default METJIRA_SECRET. "
            "Set a unique value in the environment or .env."
        )
    return Settings(
        data_dir=data_dir,
        database_url=_get(
            "METJIRA_DATABASE_URL",
            "postgresql+psycopg://met_jira:met_jira_dev@127.0.0.1:5433/met_jira",
        ),
        db_pool_size=_int("METJIRA_DB_POOL_SIZE", 5),
        db_max_overflow=_int("METJIRA_DB_MAX_OVERFLOW", 5),
        db_pool_timeout=_int("METJIRA_DB_POOL_TIMEOUT", 5),
        db_pool_recycle=_int("METJIRA_DB_POOL_RECYCLE", 300),
        db_pool_use_lifo=_bool("METJIRA_DB_POOL_USE_LIFO", True),
        db_connect_timeout=_int("METJIRA_DB_CONNECT_TIMEOUT", 5),
        db_application_name=_get("METJIRA_DB_APPLICATION_NAME", f"met-jira-{deploy_env}"),
        # psycopg uses -1 here as the environment-friendly spelling of None,
        # which disables named server-side prepared statements for transaction poolers.
        db_prepare_threshold=_int("METJIRA_DB_PREPARE_THRESHOLD", -1),
        proxy_trusted_ips=_get("METJIRA_PROXY_TRUSTED_IPS", "127.0.0.1"),
        web_host=_get("METJIRA_WEB_HOST", "127.0.0.1"),
        web_port=_int("METJIRA_WEB_PORT", 18801),
        web_workers=max(1, _int("METJIRA_WEB_WORKERS", 2)),
        secret=secret,
        auth_mode=_get("METJIRA_AUTH_MODE", "auto").lower(),
        ad_admins=frozenset(
            name.strip().lower()
            for name in _get("METJIRA_AD_ADMINS").split(",")
            if name.strip()
        ),
        admin_user=_get("METJIRA_ADMIN_USER", "admin"),
        admin_password=admin_password,
        session_hours=_int("METJIRA_SESSION_HOURS", 12),
        # Browsers share cookies across ports, so staging and production need distinct names.
        cookie_name=_get("METJIRA_COOKIE_NAME", "metjira_session"),
        cookie_secure=_bool("METJIRA_COOKIE_SECURE", False),
        deploy_env=deploy_env,
        max_upload_mb=_int("METJIRA_MAX_UPLOAD_MB", 50),
        attachment_reconcile_grace_seconds=_int(
            "METJIRA_ATTACHMENT_RECONCILE_GRACE_SECONDS", 300
        ),
        llm_enabled=_bool("METJIRA_LLM_ENABLED", True),
        llm_base_url=_get("METJIRA_LLM_BASE_URL", "http://10.113.17.65:8001/v1"),
        llm_model=_get("METJIRA_LLM_MODEL", "DeepSeek V4 Flash"),
        llm_timeout=_int("METJIRA_LLM_TIMEOUT", 90),
        org_sync_hour=_int("METJIRA_ORG_SYNC_HOUR", 2),
        due_digest_hour=_int("METJIRA_DUE_DIGEST_HOUR", 8),
        report_weekday=_get("METJIRA_REPORT_WEEKDAY", "fri"),
        scheduler_enabled=_bool("METJIRA_SCHEDULER", True),
        outbox_batch_size=_int("METJIRA_OUTBOX_BATCH_SIZE", 20),
        outbox_lease_seconds=_int("METJIRA_OUTBOX_LEASE_SECONDS", 60),
        outbox_poll_seconds=_int("METJIRA_OUTBOX_POLL_SECONDS", 2),
        login_max_failures=_int("METJIRA_LOGIN_MAX_FAILURES", 5),
        login_window_seconds=_int("METJIRA_LOGIN_WINDOW_SECONDS", 300),
        ai_requests_per_minute=_int("METJIRA_AI_REQUESTS_PER_MINUTE", 20),
        ai_max_concurrent=_int("METJIRA_AI_MAX_CONCURRENT", 2),
        ai_slot_lease_ttl_seconds=_int("METJIRA_AI_SLOT_LEASE_TTL_SECONDS", 90),
        ai_slot_heartbeat_seconds=_int("METJIRA_AI_SLOT_HEARTBEAT_SECONDS", 20),
        backup_dir=Path(_get("METJIRA_BACKUP_DIR", str(Path.home() / ".met_jira_backups"))),
        alert_webhook_url=_get("METJIRA_ALERT_WEBHOOK_URL"),
        wecom_push_url=_get("METJIRA_WECOM_PUSH_URL"),
        wecom_push_token=_get("METJIRA_WECOM_PUSH_TOKEN"),
        external_api_token=_get("METJIRA_EXTERNAL_API_TOKEN"),
        software_jira_url=_get("METJIRA_SOFTWARE_JIRA_URL").rstrip("/"),
        software_jira_token=_get("METJIRA_SOFTWARE_JIRA_TOKEN"),
        metrology_xp_url=_get(
            "METJIRA_METROLOGY_XP_URL",
            "http://127.0.0.1:8120/api/internal/xp/mira-requirement",
        ).rstrip("/"),
        metrology_xp_token=_get("METJIRA_METROLOGY_XP_TOKEN"),
        public_url=_get("METJIRA_PUBLIC_URL").rstrip("/"),
        smtp_host=_get("METJIRA_SMTP_HOST"),
        smtp_port=_int("METJIRA_SMTP_PORT", 465),
        smtp_user=_get("METJIRA_SMTP_USER"),
        smtp_password=_get("METJIRA_SMTP_PASSWORD"),
        mail_from=_get("METJIRA_MAIL_FROM") or _get("METJIRA_SMTP_USER"),
        mail_from_name=_get("METJIRA_MAIL_FROM_NAME", "Mira"),
        # Staging sets this so test alerts never reach real colleagues.
        alert_redirect=_get("METJIRA_ALERT_REDIRECT").lower(),
        login_allowlist=frozenset(
            name.strip().lower()
            for name in _get("METJIRA_LOGIN_ALLOWLIST").split(",")
            if name.strip()
        ),
        seed_sample_project=_bool("METJIRA_SEED_SAMPLE_PROJECT", False),
    )
