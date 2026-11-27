from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..db import get_db, session_factory, utcnow
from ..deps import require_admin
from ..models import AgentAudit, OrgSyncRun, User
from ..schemas import AlertTestIn, LocalUserIn, UserAdminUpdate
from ..security import hash_password
from ..serialize import iso, user_brief, user_full
from ..services.alerts import Mail, mail_configured, push_wecom, send_mail, wecom_configured
from ..services.ldap_auth import load_ldap_settings
from ..services.org_sync import (
    OrgSyncError,
    apply_directory,
    fetch_directory,
    probe_directory,
    run_org_sync,
)
from ..services.outbox import status_metrics
from ..services.reminders import due_digest
from ..services.sla import capacity_snapshot
from ..services.sla import scan as scan_sla

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/capacity")
def capacity(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return capacity_snapshot(db)


@router.post("/sla/scan")
def run_sla_scan(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    return scan_sla(db)


@router.get("/alerts")
def alerts_status(_admin: User = Depends(require_admin)) -> dict:
    settings = get_settings()
    return {
        "wecom_configured": wecom_configured(),
        "mail_configured": mail_configured(),
        "mail_from": settings.mail_from if mail_configured() else None,
        "redirect": settings.alert_redirect or None,
    }


@router.get("/outbox")
def outbox_status(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    return status_metrics(db)


def _test_recipient(body: AlertTestIn, admin: User) -> str:
    """Prefer the form value, then the admin profile, then staging alert_redirect."""
    settings = get_settings()
    email = (body.email or admin.email or settings.alert_redirect or "").strip()
    if "@" not in email:
        raise HTTPException(400, "请填写收件邮箱")
    return email


@router.post("/alerts/test")
async def alerts_test(body: AlertTestIn, admin: User = Depends(require_admin)) -> dict:
    if not wecom_configured():
        raise HTTPException(409, "企业微信推送未配置")
    email = _test_recipient(body, admin)
    markdown = (
        f"**Mira 测试消息**\n> 由 {admin.display_name} 从管理后台发送，收到即表示推送已打通。"
    )
    result = await run_in_threadpool(push_wecom, [email], markdown, redirect=False)
    # Empty delivered + transport error → surface a clear admin-facing failure.
    if result.get("error") and not result.get("delivered"):
        raise HTTPException(502, result["error"])
    return result


@router.post("/reminders/due-digest")
def run_due_digest(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    if not mail_configured():
        raise HTTPException(409, "提醒邮件未配置")
    return due_digest(db)


@router.post("/alerts/test-mail")
async def alerts_test_mail(body: AlertTestIn, admin: User = Depends(require_admin)) -> dict:
    if not mail_configured():
        raise HTTPException(409, "提醒邮件未配置")
    email = _test_recipient(body, admin)
    mail = Mail([email], "[Mira] 测试邮件",
                f"由 {admin.display_name} 从 Mira 管理后台发送，收到即表示提醒邮件已打通。")
    result = await run_in_threadpool(send_mail, [mail], redirect=False)
    if not result["sent"]:
        raise HTTPException(502, "邮件发送失败")
    return result


def run_out(run: OrgSyncRun) -> dict:
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "stats": run.stats or {},
        "diff": run.diff or {},
        "error": run.error,
    }


@router.get("/sync")
def sync_status(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    from ..config import get_settings

    settings = load_ldap_settings()
    app = get_settings()
    runs = list(db.scalars(select(OrgSyncRun).order_by(OrgSyncRun.id.desc()).limit(20)))
    last_success = next((run for run in runs if run.status == "success"), None)
    stale = last_success is None or last_success.finished_at is None
    if last_success and last_success.finished_at:
        finished = last_success.finished_at
        now = utcnow()
        if finished.tzinfo is None and now.tzinfo is not None:
            finished = finished.replace(tzinfo=now.tzinfo)
        stale = now - finished > timedelta(hours=30)
    return {
        "ldap": {
            "enabled": settings.enabled,
            "label": settings.label,
            "host": settings.host,
            "port": settings.port,
            "search_base": settings.search_base,
            "service_account": settings.app_dn,
            "can_sync": settings.can_sync,
        },
        "scheduler": {
            "enabled": app.scheduler_enabled,
            "org_sync_hour": app.org_sync_hour,
        },
        "health": {
            "ok": settings.can_sync and not stale and (not runs or runs[0].status == "success"),
            "stale": stale,
            "last_success_at": iso(last_success.finished_at) if last_success else None,
            "latest_status": runs[0].status if runs else "never",
            "action": (
                "配置服务账号后运行全量同步"
                if not settings.can_sync
                else "运行全量同步并检查错误"
                if stale or (runs and runs[0].status != "success")
                else None
            ),
        },
        "runs": [run_out(r) for r in runs],
    }


@router.post("/sync")
async def trigger_sync(admin: User = Depends(require_admin)) -> dict:
    def _run() -> dict:
        with session_factory()() as db:
            try:
                return run_out(run_org_sync(db, trigger="manual", user_id=admin.id))
            except OrgSyncError as error:
                raise HTTPException(409, str(error)) from error

    return await run_in_threadpool(_run)


@router.get("/sync/probe")
async def sync_probe(_admin: User = Depends(require_admin)) -> dict:
    return await run_in_threadpool(probe_directory)


@router.post("/sync/preview")
async def sync_preview(_admin: User = Depends(require_admin)) -> dict:
    """Fetch and calculate a diff inside a rolled-back savepoint."""
    def _preview() -> dict:
        directory = fetch_directory()
        with session_factory()() as db:
            savepoint = db.begin_nested()
            try:
                stats, diff = apply_directory(db, directory)
                return {"stats": stats, "diff": diff}
            finally:
                savepoint.rollback()
                db.rollback()

    try:
        return await run_in_threadpool(_preview)
    except OrgSyncError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/users")
def admin_users(
    q: str = "",
    limit: int = Query(100, ge=1, le=1000),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(User).options(selectinload(User.org_unit), selectinload(User.manager))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.display_name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    users = db.scalars(stmt.order_by(
        User.is_admin.desc(), User.can_create_projects.desc(), User.display_name,
    ).limit(limit))
    return {"total": total, "users": [user_full(u) for u in users]}


@router.put("/users/{user_id}")
def admin_update_user(
    user_id: int,
    body: UserAdminUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id and body.is_admin is False:
        raise HTTPException(400, "不能取消自己的管理员权限")
    changes = body.model_dump(exclude_unset=True)
    password = changes.pop("password", None)
    if password is not None:
        if user.auth_source != "local":
            raise HTTPException(400, "AD 账号的密码请在域控中修改")
        user.password_hash = hash_password(password)
    for field, value in changes.items():
        setattr(user, field, value)
    db.commit()
    return user_full(user)


@router.post("/users")
def admin_create_local_user(
    body: LocalUserIn, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已存在")
    user = User(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        is_admin=body.is_admin,
        can_create_projects=body.can_create_projects or body.is_admin,
        auth_source="local",
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    return user_full(user)


@router.get("/agent/audits")
def agent_audits(
    limit: int = Query(100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(select(AgentAudit).order_by(AgentAudit.id.desc()).limit(limit))
    result = []
    for row in rows:
        user = db.get(User, row.user_id) if row.user_id else None
        result.append(
            {
                "id": row.id,
                "user": user_brief(user),
                "kind": row.kind,
                "prompt": row.prompt[:500],
                "tool_calls": row.tool_calls,
                "result": row.result[:1000],
                "ok": row.ok,
                "latency_ms": row.latency_ms,
                "created_at": iso(row.created_at),
            }
        )
    return {"audits": result}
