from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, get_project
from ..models import (
    ActivityLog,
    AiReport,
    Issue,
    OrgUnit,
    Project,
    ProjectMember,
    ScheduleRiskEvent,
    Sprint,
    Status,
    User,
    Watcher,
)
from ..schemas import (
    CascadeApplyIn,
    CascadePreviewIn,
    ScheduleRiskDecisionIn,
)
from ..serialize import iso, issue_brief, issue_query
from ..services import (
    alerts,
    overview,
    permissions,
    planning,
    reminders,
    report_html,
    reports,
    schedule_cascade,
)
from ..services.issues import IssueError
from ..services.software import descendant_ids

router = APIRouter(prefix="/api", tags=["reports"])
ACTIVITY_PAGE_SIZE = 15


@router.get("/projects/{key}/overview")
def project_overview(key: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    return overview.summary(db, get_project(db, key, user), user)


@router.get("/projects/{key}/activity")
def project_activity(key: str, page: int = Query(1, ge=1),
                     size: int = Query(ACTIVITY_PAGE_SIZE, ge=1, le=50),
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return overview.project_activity(db, get_project(db, key, user), user, page, size)


@router.get("/projects/{key}/timeline")
def project_timeline(key: str, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    return overview.timeline(db, get_project(db, key, user), user)


@router.get("/projects/{key}/reports/summary")
def summary(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
            user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.summary(db, get_project(db, key, user), days, include_subtasks=subtasks)


@router.get("/projects/{key}/reports/health")
def health(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
           user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.health(db, get_project(db, key, user), days, include_subtasks=subtasks)


@router.get("/projects/{key}/reports/completion-curve")
def completion_curve(key: str, days: int = Query(90, ge=14, le=365), subtasks: bool = True,
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.completion_curve(
        db, get_project(db, key, user), days, include_subtasks=subtasks,
    )


@router.get("/projects/{key}/schedule/cascade")
def schedule_cascade_get(key: str, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    return schedule_cascade.suggest_cascade(db, get_project(db, key, user))


@router.post("/projects/{key}/schedule/analysis")
def schedule_analysis(key: str, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    analysis = schedule_cascade.suggest_cascade(db, project)
    schedule_cascade.sync_risk_events(db, project, analysis)
    db.commit()
    return analysis


@router.post("/projects/{key}/schedule/cascade/preview")
def schedule_cascade_preview(
    key: str,
    body: CascadePreviewIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, need="admin")
    return schedule_cascade.preview_cascade(
        db,
        project,
        body.analysis_token,
        body.keys,
        sync_priority=body.sync_priority,
    )


@router.post("/projects/{key}/schedule/cascade/apply")
def schedule_cascade_apply(key: str, body: CascadeApplyIn,
                           user: User = Depends(current_user),
                           db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, need="admin")
    if permissions.is_ongoing(project):
        raise HTTPException(400, "持续项目不支持项目级排期顺延")
    try:
        return schedule_cascade.apply_cascade(
            db,
            project,
            user,
            [item.model_dump() for item in body.items],
            analysis_token=body.analysis_token,
            sync_priority=body.sync_priority,
            note=body.note,
        )
    except IssueError as error:
        raise HTTPException(error.status_code, str(error)) from error


@router.get("/projects/{key}/schedule/risks")
def schedule_risks(key: str, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    rows = db.scalars(
        select(ScheduleRiskEvent)
        .where(ScheduleRiskEvent.project_id == project.id)
        .order_by(ScheduleRiskEvent.last_seen_at.desc())
    )
    return {"risks": [schedule_cascade.risk_event_out(row) for row in rows]}


@router.post("/projects/{key}/schedule/risks/{issue_key}/decision")
def schedule_risk_decision(
    key: str,
    issue_key: str,
    body: ScheduleRiskDecisionIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, need="admin")
    try:
        return schedule_cascade.decide_risk(
            db,
            project,
            user,
            issue_key,
            action=body.action,
            expected_version=body.expected_version,
            note=body.note,
            snoozed_until=body.snoozed_until,
        )
    except IssueError as error:
        raise HTTPException(error.status_code, str(error)) from error


@router.get("/portfolio/overview")
def portfolio_overview(
    scope: str | None = Query(default=None, pattern="^(all|lead|managed)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not schedule_cascade.has_portfolio_access(db, user):
        raise HTTPException(403, "需要系统管理员或项目管理员权限")
    return reports.portfolio_overview(db, user, scope)


@router.get("/portfolio/ongoing")
def portfolio_ongoing(
    scope: str | None = Query(default=None, pattern="^(all|lead|managed)$"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not schedule_cascade.has_portfolio_access(db, user):
        raise HTTPException(403, "需要系统管理员或项目管理员权限")
    return reports.ongoing_portfolio_overview(db, user, scope)


@router.get("/projects/{key}/reports/quality")
def quality(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
            user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.quality(db, get_project(db, key, user), days, include_subtasks=subtasks)


@router.get("/projects/{key}/reports/units")
def report_units(key: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    """Org units involved in this project's issues, in tree order, counting sub-units."""
    project = get_project(db, key, user)
    units = list(db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False))))
    hidden: set[int] = set()
    for unit in units:
        if unit.hidden:
            hidden |= descendant_ids(units, unit.id)
    units = [u for u in units if u.id not in hidden]
    by_id = {u.id: u for u in units}
    rows = db.execute(
        select(Issue.id, Issue.assignee_id, Issue.reporter_id, Issue.tester_id, Status.category)
        .join(Status, Status.id == Issue.status_id)
        .where(Issue.project_id == project.id)
    ).all()
    people = {uid for row in rows for uid in row[1:4] if uid}
    unit_of = dict(db.execute(
        select(User.id, User.org_unit_id).where(User.id.in_(people))
    ).all()) if people else {}
    issues: dict[int, set[int]] = {}
    open_issues: dict[int, set[int]] = {}
    for issue_id, *involved, category in rows:
        touched: set[int] = set()
        for uid in involved:
            unit_id = unit_of.get(uid)
            while unit_id in by_id and unit_id not in touched:
                touched.add(unit_id)
                unit_id = by_id[unit_id].parent_id
        for unit_id in touched:
            issues.setdefault(unit_id, set()).add(issue_id)
            if category != "done":
                open_issues.setdefault(unit_id, set()).add(issue_id)
    children: dict[int | None, list[OrgUnit]] = {}
    for unit in units:
        parent = unit.parent_id if unit.parent_id in by_id else None
        children.setdefault(parent, []).append(unit)
    ordered: list[dict] = []

    def walk(parent: int | None, depth: int) -> None:
        for unit in sorted(children.get(parent, []), key=lambda u: (u.sort_order, u.label)):
            if unit.id in issues:
                ordered.append({"id": unit.id, "label": unit.label, "path": unit.path,
                                "depth": depth, "issues": len(issues[unit.id]),
                                "open": len(open_issues.get(unit.id, ()))})
                walk(unit.id, depth + 1)

    walk(None, 0)
    return {"total": len(rows), "open": sum(1 for r in rows if r[4] != "done"), "units": ordered}


@router.get("/projects/{key}/reports/cfd")
def cfd(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
        user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.cumulative_flow(db, get_project(db, key, user), days,
                                   include_subtasks=subtasks)


@router.get("/projects/{key}/reports/flow")
def flow(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
         user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return reports.flow(db, get_project(db, key, user), days, include_subtasks=subtasks)


@router.get("/projects/{key}/reports/load")
def load(key: str, subtasks: bool = True, user: User = Depends(current_user),
         db: Session = Depends(get_db)) -> dict:
    return reports.load(db, get_project(db, key, user), include_subtasks=subtasks)


def _report_sprint(db: Session, project: Project, sprint_id: int | None) -> Sprint:
    """The requested sprint, else the active one, else the latest closed one."""
    if sprint_id is None:
        sprint = db.scalar(
            select(Sprint).where(Sprint.project_id == project.id, Sprint.state != "future")
            .order_by(Sprint.state.asc(), Sprint.id.desc())
        )
    else:
        sprint = db.get(Sprint, sprint_id)
    if sprint is None or sprint.project_id != project.id:
        raise HTTPException(404, "没有可用的冲刺")
    return sprint


@router.get("/projects/{key}/reports/burndown")
def burndown(key: str, sprint_id: int | None = None, user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    return reports.burndown(db, _report_sprint(db, project, sprint_id))


@router.get("/projects/{key}/reports/velocity")
def velocity(key: str, subtasks: bool = True, user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> dict:
    return planning.velocity(db, get_project(db, key, user), include_subtasks=subtasks)


@router.get("/projects/{key}/reports/sprint")
def sprint_report(key: str, sprint_id: int | None = None, subtasks: bool = True,
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    return planning.sprint_detail(db, project, _report_sprint(db, project, sprint_id),
                                  include_subtasks=subtasks)


@router.get("/projects/{key}/reports/load/history")
def load_history(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return planning.load_history(db, get_project(db, key, user), days,
                                 include_subtasks=subtasks)


@router.get("/projects/{key}/reports/forecast")
def forecast(key: str, days: int = Query(30, ge=7, le=180), subtasks: bool = True,
             scope: str = "sprint", target: date | None = None,
             user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if target is not None and target < date.today():
        raise HTTPException(400, "目标日期不能早于今天")
    return planning.forecast(db, get_project(db, key, user), days, include_subtasks=subtasks,
                             scope=scope, target=target)


def _can_delete_report(report: AiReport, user: User, is_admin: bool) -> bool:
    return is_admin or (report.created_by_id is not None and report.created_by_id == user.id)


@router.get("/projects/{key}/ai-reports")
def ai_reports(key: str, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    is_admin = permissions.is_project_admin(user, permissions.project_roles(db, project.id, user))
    rows = db.scalars(
        select(AiReport).where(AiReport.project_id == project.id)
        .order_by(AiReport.id.desc()).limit(50)
    )
    return {
        "reports": [
            {"id": r.id, "period": r.period, "title": r.title, "content": r.content,
             "scheduled": r.scheduled, "has_charts": bool(r.metrics),
             "created_at": iso(r.created_at),
             "can_delete": _can_delete_report(r, user, is_admin), "can_email": is_admin}
            for r in rows
        ]
    }


PREVIEW_CSP = ("default-src 'none'; img-src data: https: http:; style-src 'unsafe-inline'; "
               "base-uri 'self'; form-action 'none'; frame-ancestors 'self'")


@router.get("/projects/{key}/ai-reports/{report_id}/html", response_class=HTMLResponse)
def ai_report_html(key: str, report_id: int, download: bool = False,
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """The report exactly as it is emailed (signature included), as a standalone page."""
    project = get_project(db, key, user)
    report = db.get(AiReport, report_id)
    if report is None or report.project_id != project.id:
        raise HTTPException(404, "报告不存在")
    body = alerts.signed_html(reminders.report_mail_html(project, report, preview=True),
                              alerts.logo_data_uri())
    headers = {"Content-Security-Policy": PREVIEW_CSP, "Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename=\"mira-report-{report.id}.html\"; "
            f"filename*=UTF-8''{quote(report.title)}.html")
    return HTMLResponse(report_html.preview_document(report.title, body), headers=headers)


@router.post("/projects/{key}/ai-reports/{report_id}/email")
def email_ai_report(key: str, report_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    report = db.get(AiReport, report_id)
    if report is None or report.project_id != project.id:
        raise HTTPException(404, "报告不存在")
    if not permissions.is_project_admin(user, permissions.project_roles(db, project.id, user)):
        raise HTTPException(403, "只有项目管理员可以发送报告邮件")
    if not alerts.mail_configured():
        raise HTTPException(409, "提醒邮件未配置")
    result = reminders.mail_report(db, project, report)
    if not result["recipients"]:
        raise HTTPException(409, "项目没有设置负责人或管理员邮箱")
    db.commit()
    return result


@router.delete("/projects/{key}/ai-reports/{report_id}")
def delete_ai_report(key: str, report_id: int, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    report = db.get(AiReport, report_id)
    if report is None or report.project_id != project.id:
        raise HTTPException(404, "报告不存在")
    is_admin = permissions.is_project_admin(user, permissions.project_roles(db, project.id, user))
    if not _can_delete_report(report, user, is_admin):
        raise HTTPException(403, "只有报告创建者或项目管理员可以删除")
    db.delete(report)
    db.commit()
    return {"ok": True}


@router.get("/dashboard/overview")
def dashboard_overview(user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
    """Personal progress for My Work (status / priority / last-7-day counts)."""
    return overview.personal_summary(db, user)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from ..services import workflow

    not_done = Issue.status_id.in_(select(Status.id).where(Status.category != "done"))
    completed = Issue.status_id.in_(
        select(Status.id).where(Status.category == "done", Status.name != workflow.REJECTED)
    )
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        not_done = and_(not_done, visible)
        completed = and_(completed, visible)
    assigned = db.scalars(
        issue_query().where(Issue.assignee_id == user.id, not_done)
        .order_by(Issue.updated_at.desc()).limit(100)
    )
    reported = db.scalars(
        issue_query().where(Issue.reporter_id == user.id, not_done)
        .order_by(Issue.updated_at.desc()).limit(50)
    )
    testing_rows = list(
        db.scalars(
            issue_query().where(Issue.tester_id == user.id, not_done)
            .order_by(Issue.updated_at.desc()).limit(50)
        )
    )
    done_rows = db.scalars(
        issue_query().where(Issue.assignee_id == user.id, completed)
        .order_by(Issue.updated_at.desc()).limit(50)
    )
    projects = {p.id: p for p in db.scalars(select(Project))}
    to_test = [
        i for i in testing_rows
        if i.status.name == (projects[i.project_id].settings or {}).get("test_status", "测试中")
    ]
    return {
        "assigned": [issue_brief(i) for i in assigned],
        "reported": [issue_brief(i) for i in reported],
        "to_test": [issue_brief(i) for i in to_test],
        "testing": [issue_brief(i) for i in testing_rows],
        "completed": [issue_brief(i) for i in done_rows],
    }


@router.get("/dashboard/health")
def dashboard_health(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """Health of every active project with work in it; members' projects first, riskiest first."""
    mine = set(db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)))
    severity = {"risk": 0, "attention": 1, "good": 2}
    hidden = permissions.hidden_project_ids(db, user)
    projects = [
        project for project in db.scalars(select(Project).where(Project.archived.is_(False)))
        if project.id not in hidden
    ]
    by_key = {project.key: project for project in projects}
    rows = reports.dashboard_health(db, projects)
    for row in rows:
        project = by_key[row["key"]]
        row["is_member"] = user.is_admin or project.id in mine or project.lead_id == user.id
    rows.sort(key=lambda r: (not r["is_member"], severity[r["level"]], r["key"]))
    return {"projects": rows}


@router.get("/dashboard/activity")
def watched_activity(page: int = Query(1, ge=1), size: int = Query(ACTIVITY_PAGE_SIZE, ge=1, le=50),
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    watched = select(Watcher.issue_id).where(Watcher.user_id == user.id)
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        watched = watched.join(Issue, Issue.id == Watcher.issue_id).where(visible)
    scope = [ActivityLog.issue_id.in_(watched), ActivityLog.actor_id != user.id]
    return overview.activity_page(db, scope, page, size)
