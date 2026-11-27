"""Explicit operational entry points for schema and data bootstrap."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .config import get_settings
from .db import engine, init_db, postgres_advisory_lock, session_factory
from .models import Project, User
from .routers.attachments import reconcile_storage
from .security import hash_password
from .services import templates
from .services.workflow import bootstrap_project


def _seed_sample_project_once(db: Session) -> None:
    """Create the lab sample project at most once when explicitly opted in.

    Production / staging leave ``METJIRA_SEED_SAMPLE_PROJECT`` unset so bootstrap
    never recreates ELEC after an operator deletes every project. Tests set the
    flag; a marker under ``data_dir`` prevents a second seed even if the flag
    stays on.
    """
    settings = get_settings()
    if not settings.seed_sample_project:
        return
    marker = settings.data_dir / ".sample_project_seeded"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        return
    if db.scalar(select(Project.id).limit(1)) is not None:
        marker.write_text("skipped-existing\n", encoding="utf-8")
        return
    lead = db.scalar(select(User).where(User.is_admin.is_(True)).limit(1))
    project = Project(
        key="ELEC",
        name="电气组需求",
        description="电气组需求跟踪：需求提出人 → 电气组 → 测试人员",
        lead_id=lead.id if lead else None,
        color="#0a84ff",
    )
    db.add(project)
    db.flush()
    bootstrap_project(db, project, with_machines=True)
    marker.write_text("seeded\n", encoding="utf-8")


def seed(db: Session | None = None) -> None:
    """Create built-in templates and the initial admin (sample project is opt-in)."""
    settings = get_settings()
    owns_session = db is None
    if db is None:
        db = session_factory()()
    try:
        templates.ensure_builtin_templates(db)
        if settings.auth_mode != "ldap" and settings.admin_user:
            admin_user = db.scalar(select(User).where(User.username == settings.admin_user))
            if admin_user is None:
                db.add(
                    User(
                        username=settings.admin_user,
                        display_name="系统管理员",
                        auth_source="local",
                        password_hash=hash_password(settings.admin_password),
                        is_admin=True,
                    )
                )
                db.flush()
        _seed_sample_project_once(db)
        db.commit()
    finally:
        if owns_session:
            db.close()


def _prepare_directories() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_dir.mkdir(parents=True, exist_ok=True)


def _bootstrap_on_connection(connection: Connection) -> dict[str, int]:
    init_db(connection)
    connection.commit()
    with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
        seed(db)
        result = reconcile_storage(db)
        db.commit()
    return result


def bootstrap() -> dict[str, int]:
    """Migrate, seed and reconcile storage exactly once across competing processes."""
    _prepare_directories()
    eng = engine()
    if eng.dialect.name != "postgresql":
        init_db()
        seed()
        with session_factory()() as db:
            return reconcile_storage(db)

    with eng.connect() as connection:
        with postgres_advisory_lock(connection):
            result = _bootstrap_on_connection(connection)
        connection.commit()
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.ops")
    parser.add_argument("command", choices=("bootstrap",))
    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        result = bootstrap()
        print(f"bootstrap complete: {json.dumps(result, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
