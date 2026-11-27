from __future__ import annotations

import hashlib
import json

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import current_user, get_project, require_admin, require_project_creator
from ..models import (
    Board,
    DepartmentTemplate,
    DepartmentTemplateVersion,
    Issue,
    IssueLink,
    Machine,
    MachineGroup,
    OrgUnit,
    Project,
    ProjectMember,
    ProjectScheduleBaseline,
    ProjectTemplateMigration,
    Sprint,
    Status,
    User,
)
from ..schemas import (
    BaselinePublishIn,
    CoverPresetIn,
    MachineGroupIn,
    MachineIn,
    MemberBulkIn,
    MemberIn,
    MemberRolesBulkIn,
    ProjectIn,
    ProjectUpdate,
    StatusIn,
)
from ..serialize import iso, machine_out, project_out, sprint_out, status_out, user_brief
from ..services import (
    covers,
    permissions,
    profiles,
    project_icons,
    project_purge,
    sla,
    template_migration,
    templates,
    workflow_audit,
)
from ..services.issues import collect_project_labels
from ..services.software import descendant_ids, software_roots
from ..services.workflow import (
    attach_status_column,
    bootstrap_project,
    customized_types,
    detach_status_column,
    effective_transitions,
    rename_status_column,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _baseline_material(db: Session, project: Project) -> tuple[dict, list[str], list[str]]:
    issues = list(
        db.scalars(
            select(Issue)
            .options(selectinload(Issue.status))
            .where(Issue.project_id == project.id)
            .order_by(Issue.number)
        )
    )
    issue_ids = {issue.id for issue in issues}
    links = list(
        db.scalars(
            select(IssueLink)
            .where(
                IssueLink.link_type == "blocks",
                (IssueLink.source_id.in_(issue_ids) | IssueLink.target_id.in_(issue_ids)),
            )
            .order_by(IssueLink.id)
        )
    ) if issue_ids else []
    internal_links = [
        link for link in links
        if link.source_id in issue_ids and link.target_id in issue_ids
    ]
    blockers: list[str] = []
    warnings: list[str] = []
    if project.plan_start_date is None:
        blockers.append("请先设置项目计划开始日期")
    if project.target_date is None:
        blockers.append("请先设置项目明确目标日期")
    if not issues:
        blockers.append("项目尚无可纳入基线的问题")
    missing_due = [issue.key for issue in issues if issue.due_date is None]
    if missing_due:
        blockers.append(f"{len(missing_due)} 个问题缺少截止日期")
    missing_start = [issue.key for issue in issues if issue.planned_start_date is None]
    if missing_start:
        warnings.append(f"{len(missing_start)} 个问题缺少计划开始日期，将按项目开始日期解释")
    cross_project = len(links) - len(internal_links)
    if cross_project:
        blockers.append(f"{cross_project} 条跨项目阻塞依赖需要拆分或明确归属")
    if project.target_date is not None:
        outside = [
            issue.key
            for issue in issues
            if issue.due_date and issue.due_date > project.target_date
        ]
        if outside:
            warnings.append(f"{len(outside)} 个问题的当前截止日期晚于项目目标")
    snapshot = {
        "project": {
            "key": project.key,
            "plan_start_date": iso(project.plan_start_date),
            "target_date": iso(project.target_date),
        },
        "issues": [
            {
                "id": issue.id,
                "key": issue.key,
                "title": issue.title,
                "type": issue.type,
                "planned_start_date": iso(issue.planned_start_date),
                "due_date": iso(issue.due_date),
                "status_category": issue.status.category if issue.status else None,
                "lock_version": issue.lock_version,
            }
            for issue in issues
        ],
        "links": [
            {
                "id": link.id,
                "source_id": link.source_id,
                "target_id": link.target_id,
                "type": link.link_type,
                "lag_days": link.lag_days,
            }
            for link in internal_links
        ],
    }
    return snapshot, blockers, warnings


def _baseline_out(
    baseline: ProjectScheduleBaseline | None,
    blockers: list[str],
    warnings: list[str],
) -> dict:
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "baseline": None if baseline is None else {
            "id": baseline.id,
            "version": baseline.version,
            "active": baseline.active,
            "target_date": iso(baseline.target_date),
            "scope_hash": baseline.scope_hash,
            "note": baseline.note,
            "created_by_id": baseline.created_by_id,
            "created_at": iso(baseline.created_at),
        },
    }


@router.get("/profile-catalog")
def profile_catalog(_user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict:
    out = []
    for template in templates.catalog(db):
        if template.current_version is None:
            continue
        profile = profiles.from_definition(template.current_version.definition)
        out.append({
            **profiles.layout(profile),
            "template_id": template.id,
            "version_id": template.current_version.id,
            "version": template.current_version.version,
            "description": template.description,
        })
    return {"profiles": out}


@router.get("/profile-recommendations")
def profile_recommendations(
    lead_id: int | None = None, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    lead = db.get(User, lead_id) if lead_id else user
    if lead is None:
        raise HTTPException(404, "用户不存在")
    rows = templates.recommendation_details(db, lead.org_unit_id)
    return {
        "org_unit_id": lead.org_unit_id,
        "profiles": [
            {
                **profiles.layout(profiles.from_definition(template.current_version.definition)),
                "template_id": template.id,
                "version_id": template.current_version_id,
                "recommended_by": {
                    "id": unit.id, "name": unit.label, "path": unit.path,
                },
            }
            for template, unit in rows if template.current_version
        ],
    }


@router.get("")
def list_projects(
    include_archived: bool = False,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Project).options(selectinload(Project.lead)).order_by(Project.key)
    if not include_archived:
        stmt = stmt.where(Project.archived.is_(False))
    hidden = permissions.hidden_project_ids(db, user)
    projects = [p for p in db.scalars(stmt) if p.id not in hidden]
    counts = dict(
        db.execute(
            select(Issue.project_id, func.count(Issue.id))
            .join(Status, Status.id == Issue.status_id)
            .where(Status.category != "done")
            .group_by(Issue.project_id)
        ).all()
    )
    totals = dict(
        db.execute(select(Issue.project_id, func.count(Issue.id)).group_by(Issue.project_id)).all()
    )
    mine = set(
        db.scalars(select(ProjectMember.project_id).where(ProjectMember.user_id == user.id))
    )
    administered = set(db.scalars(select(ProjectMember.project_id).where(
        ProjectMember.user_id == user.id, ProjectMember.role == "admin"
    )))
    return {
        "projects": [
            {
                **project_out(p),
                "open_count": counts.get(p.id, 0),
                "total_count": totals.get(p.id, 0),
                "is_member": user.is_admin or p.id in mine or p.lead_id == user.id,
                "can_admin": user.is_admin or p.id in administered or p.lead_id == user.id,
            }
            for p in projects
        ]
    }


@router.post("")
def create_project(
    body: ProjectIn, creator: User = Depends(require_project_creator),
    db: Session = Depends(get_db),
) -> dict:
    key = body.key.upper()
    if db.scalar(select(Project.id).where(Project.key == key)):
        raise HTTPException(409, "项目 Key 已存在")
    version = templates.published_version(db, body.profile)
    if version is None:
        raise HTTPException(400, "未知或已停用的项目模板")
    try:
        icon = project_icons.normalize(body.icon)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    mode = permissions.normalize_work_mode(body.work_mode)
    delivery = mode == "delivery"
    project = Project(
        key=key,
        name=body.name,
        description=body.description,
        lead_id=body.lead_id or creator.id,
        color=body.color,
        icon=icon,
        plan_start_date=body.plan_start_date if delivery else None,
        target_date=body.target_date if delivery else None,
        settings={
            "profile": body.profile,
            "visibility": body.visibility,
            "work_mode": mode,
        },
        template_version_id=version.id,
        template_version=version,
    )
    db.add(project)
    db.flush()
    bootstrap_project(db, project, with_machines=body.with_machines)
    db.add(ProjectMember(project_id=project.id, user_id=project.lead_id, role="admin"))
    db.commit()
    return project_out(project)


@router.get("/{key}/schedule-baseline")
def schedule_baseline(
    key: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user)
    if permissions.is_ongoing(project):
        baseline = db.scalar(
            select(ProjectScheduleBaseline).where(
                ProjectScheduleBaseline.project_id == project.id,
                ProjectScheduleBaseline.active.is_(True),
            )
        )
        return _baseline_out(baseline, ["持续项目不发布排期基线"], [])
    _snapshot, blockers, warnings = _baseline_material(db, project)
    baseline = db.scalar(
        select(ProjectScheduleBaseline).where(
            ProjectScheduleBaseline.project_id == project.id,
            ProjectScheduleBaseline.active.is_(True),
        )
    )
    return _baseline_out(baseline, blockers, warnings)


@router.post("/{key}/schedule-baseline")
def publish_schedule_baseline(
    key: str,
    body: BaselinePublishIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    if permissions.is_ongoing(project):
        raise HTTPException(400, "持续项目不发布排期基线")
    snapshot, blockers, warnings = _baseline_material(db, project)
    if blockers:
        raise HTTPException(409, {"message": "项目计划尚未达到基线发布条件", "blockers": blockers})
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    scope_hash = hashlib.sha256(canonical.encode()).hexdigest()
    current = db.scalar(
        select(ProjectScheduleBaseline).where(
            ProjectScheduleBaseline.project_id == project.id,
            ProjectScheduleBaseline.active.is_(True),
        )
    )
    if current is not None and current.scope_hash == scope_hash:
        return _baseline_out(current, [], warnings)
    version = (db.scalar(
        select(func.max(ProjectScheduleBaseline.version)).where(
            ProjectScheduleBaseline.project_id == project.id
        )
    ) or 0) + 1
    if current is not None:
        current.active = False
    baseline = ProjectScheduleBaseline(
        project_id=project.id,
        version=version,
        active=True,
        target_date=project.target_date,
        scope_hash=scope_hash,
        snapshot=snapshot,
        note=body.note,
        created_by_id=user.id,
    )
    db.add(baseline)
    db.commit()
    db.refresh(baseline)
    return _baseline_out(baseline, [], warnings)


@router.get("/{key}")
def project_detail(key: str, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    roles = permissions.project_roles(db, project.id, user)
    profile = profiles.profile_of(project)
    statuses = db.scalars(
        select(Status).where(Status.project_id == project.id).order_by(Status.sort_order)
    )
    groups = db.scalars(
        select(MachineGroup)
        .options(selectinload(MachineGroup.machines))
        .where(MachineGroup.project_id == project.id)
        .order_by(MachineGroup.sort_order, MachineGroup.id)
    )
    members = db.scalars(
        select(ProjectMember)
        .options(selectinload(ProjectMember.user).selectinload(User.org_unit))
        .where(ProjectMember.project_id == project.id)
    )
    boards = db.scalars(select(Board).where(Board.project_id == project.id).order_by(Board.id))
    sprints = db.scalars(
        select(Sprint)
        .where(Sprint.project_id == project.id, Sprint.state != "closed")
        .order_by(Sprint.id)
    )
    epics = db.scalars(
        select(Issue)
        .where(Issue.project_id == project.id, Issue.type == "epic")
        .order_by(Issue.number.desc())
    )
    return {
        "project": project_out(project),
        "my_roles": sorted(roles),
        "is_admin": permissions.is_project_admin(user, roles),
        "statuses": [status_out(s) for s in statuses],
        "workflows": effective_transitions(project),
        "customized_workflows": customized_types(project),
        "machine_groups": [
            {
                "id": g.id,
                "name": g.name,
                "sort_order": g.sort_order,
                "machines": [machine_out(m) for m in g.machines],
            }
            for g in groups
        ],
        "members": [
            {
                "id": m.id,
                "role": m.role,
                "user": {
                    **(user_brief(m.user) or {}),
                    "org_unit_id": m.user.org_unit_id,
                    "org_path": m.user.org_unit.path if m.user.org_unit else None,
                },
            }
            for m in members
        ],
        "boards": [{"id": b.id, "name": b.name, "kind": b.kind} for b in boards],
        "sprints": [sprint_out(s) for s in sprints],
        "epics": [{"id": e.id, "key": e.key, "title": e.title} for e in epics],
        "roles": list(profile.roles),
        "profile": profiles.layout(profile),
        "software_units": [
            {"id": u.id, "path": u.path, "label": u.label}
            for u in software_roots(db, project)
        ],
        # Catalog for pickers: settings ∪ labels already used on issues.
        "labels": collect_project_labels(db, project),
    }


@router.post("/{key}/template-migration/preview")
def preview_template_migration(
    key: str, body: dict, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    version = db.get(DepartmentTemplateVersion, int(body.get("target_version_id") or 0))
    if version is None:
        raise HTTPException(404, "目标模板版本不存在")
    try:
        return template_migration.preview(db, project, version)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/{key}/template-migration/apply")
def apply_template_migration(
    key: str, body: dict, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    version = db.get(DepartmentTemplateVersion, int(body.get("target_version_id") or 0))
    if version is None:
        raise HTTPException(404, "目标模板版本不存在")
    try:
        record = template_migration.apply(
            db, project, version, dict(body.get("mapping") or {}), user.id
        )
        db.commit()
    except ValueError as error:
        db.rollback()
        raise HTTPException(400, str(error)) from error
    return {"id": record.id, "status": record.status, "result": record.result}


@router.get("/{key}/template-migrations")
def list_template_migrations(
    key: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    project = get_project(db, key, user, "admin")
    rows = db.scalars(
        select(ProjectTemplateMigration)
        .where(ProjectTemplateMigration.project_id == project.id)
        .order_by(ProjectTemplateMigration.id.desc())
    )
    return {"migrations": [
        {
            "id": row.id, "status": row.status, "source_version_id": row.source_version_id,
            "target_version_id": row.target_version_id, "preview": row.preview,
            "mapping": row.mapping, "result": row.result, "created_at": row.created_at,
            "applied_at": row.applied_at,
        }
        for row in rows
    ]}


@router.get("/{key}/template-migrations/{migration_id}/report.json")
def download_template_migration_report(
    key: str, migration_id: int, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    project = get_project(db, key, user, "admin")
    row = db.get(ProjectTemplateMigration, migration_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(404, "模板迁移记录不存在")
    content = {
        "id": row.id, "project": project.key, "status": row.status,
        "source_version_id": row.source_version_id, "target_version_id": row.target_version_id,
        "preview": row.preview, "mapping": row.mapping, "result": row.result,
        "created_by_id": row.created_by_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
    }
    return JSONResponse(content, headers={
        "Content-Disposition": (
            f'attachment; filename="{project.key}-template-migration-{row.id}.json"'
        )
    })


@router.get("/{key}/cover")
def project_cover(key: str, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> FileResponse:
    project = get_project(db, key, user)
    path = (
        covers.cover_path(project.id, project.cover_key)
        if project.cover_key else None
    )
    if path is None or not path.is_file():
        raise HTTPException(404, "封面不存在")
    return FileResponse(
        path, media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/{key}/cover")
async def upload_project_cover(
    key: str, file: UploadFile, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    data = await file.read(covers.MAX_BYTES + 1)
    old_key = project.cover_key
    try:
        cover_key = await to_thread.run_sync(covers.stage, project.id, data)
    except covers.CoverError as exc:
        raise HTTPException(400, str(exc)) from exc
    project.cover_key = cover_key
    try:
        db.commit()
    except Exception:
        db.rollback()
        await to_thread.run_sync(covers.cover_path(project.id, cover_key).unlink, True)
        raise
    if old_key:
        await to_thread.run_sync(covers.cover_path(project.id, old_key).unlink, True)
    return project_out(project)


@router.put("/{key}/cover-preset")
def apply_project_cover_preset(
    key: str, body: CoverPresetIn, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    try:
        covers.apply_preset(project, body.preset)
    except covers.CoverError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return project_out(project)


@router.delete("/{key}/cover")
def delete_project_cover(
    key: str, user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user, "admin")
    covers.clear(project)
    db.commit()
    return project_out(project)


@router.put("/{key}")
def update_project(
    key: str, body: ProjectUpdate, user: User = Depends(current_user),
    db: Session = Depends(get_db)
) -> dict:
    project = get_project(db, key, user, "admin")
    data = body.model_dump(exclude_unset=True)
    if "icon" in data:
        try:
            data["icon"] = project_icons.normalize(data["icon"])
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    visibility = (data.get("settings") or {}).get("visibility")
    if visibility is not None and visibility not in permissions.VISIBILITIES:
        raise HTTPException(400, "无效的项目可见性")
    settings_patch = data.get("settings")
    if settings_patch is not None and "work_mode" in settings_patch:
        try:
            settings_patch["work_mode"] = permissions.normalize_work_mode(
                settings_patch["work_mode"]
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    profile_key = (data.get("settings") or {}).get("profile")
    if profile_key is not None:
        exists = db.scalar(select(DepartmentTemplate.id).where(
            DepartmentTemplate.key == profile_key
        ))
        if exists is None:
            raise HTTPException(400, "未知的项目模板")
        if profile_key != profiles.profile_of(project).key:
            raise HTTPException(409, "更换项目模板必须使用模板迁移向导")
    if "transition_gates" in (data.get("settings") or {}):
        try:
            selected = profiles.profile_of(project)
            data["settings"]["transition_gates"] = profiles.validate_gates(
                selected or profiles.ELECTRICAL, data["settings"]["transition_gates"]
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
    if "transitions" in (data.get("settings") or {}):
        statuses = sorted(project.statuses, key=lambda status: status.sort_order)
        try:
            data["settings"]["transitions"] = workflow_audit.validate_transitions(
                [(status.name, status.category) for status in statuses],
                data["settings"]["transitions"],
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    sla_changed = False
    previous_mode = permissions.work_mode(project)
    if "settings" in data and data["settings"] is not None:
        incoming = data.pop("settings")
        try:
            sla.validate_settings(incoming)
        except (TypeError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        unknown = set(incoming.get("sla_paused_statuses") or []) - {
            s.name for s in project.statuses
        }
        if unknown:
            raise HTTPException(400, f"未知的状态：{'、'.join(sorted(unknown))}")
        before = project.settings or {}
        sla_changed = any(
            k.startswith("sla_") and incoming[k] != before.get(k) for k in incoming
        )
        project.settings = {**before, **incoming}
    if permissions.work_mode(project) == "ongoing" and previous_mode != "ongoing":
        from ..models import ScheduleRiskEvent
        open_statuses = ("new", "worsening", "acknowledged", "snoozed")
        for event in db.scalars(
            select(ScheduleRiskEvent).where(
                ScheduleRiskEvent.project_id == project.id,
                ScheduleRiskEvent.status.in_(open_statuses),
            )
        ):
            event.status = "resolved"
    if "archived" in data and data["archived"] != project.archived:
        archive_info = (
            {"archived_at": utcnow().isoformat(), "archived_by": user.display_name}
            if data["archived"] else {"archived_at": None, "archived_by": None}
        )
        project.settings = {**(project.settings or {}), **archive_info}
    for field, value in data.items():
        setattr(project, field, value)
    if sla_changed:
        db.flush()
        sla.resync_project(db, project)
    from ..services import live

    live.queue(
        db,
        project_key=project.key,
        kind="project",
        actor_id=user.id,
        fields=sorted({*data.keys(), *(["settings"] if settings_patch is not None else [])}),
    )
    db.commit()
    return project_out(project)


@router.get("/{key}/purge-preview")
def purge_preview(key: str, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, admin)
    return {"key": project.key, "archived": project.archived,
            "counts": project_purge.purge_counts(db, project)}


@router.delete("/{key}")
def delete_project(key: str, confirm: str = "", admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, admin)
    if not project.archived:
        raise HTTPException(409, "请先归档项目，再永久删除")
    if confirm.strip().upper() != project.key:
        raise HTTPException(400, "确认的项目 Key 不匹配")
    return {"ok": True, "key": project.key,
            "deleted": project_purge.purge_project(db, project, admin)}


@router.post("/{key}/members")
def add_member(key: str, body: MemberIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    if body.role not in profiles.profile_of(project).roles:
        raise HTTPException(400, "项目模板不支持该角色")
    if db.get(User, body.user_id) is None:
        raise HTTPException(404, "用户不存在")
    exists = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == body.user_id,
            ProjectMember.role == body.role,
        )
    )
    if exists is None:
        db.add(ProjectMember(project_id=project.id, user_id=body.user_id, role=body.role))
        db.commit()
    return {"ok": True}


@router.post("/{key}/members/bulk")
def add_members_by_org(key: str, body: MemberBulkIn, user: User = Depends(current_user),
                       db: Session = Depends(get_db)) -> dict:
    """Give every enabled user of an org unit (and, by default, its sub-units) the role."""
    project = get_project(db, key, user, "admin")
    if body.role not in profiles.profile_of(project).roles:
        raise HTTPException(400, "项目模板不支持该角色")
    unit = db.get(OrgUnit, body.org_unit_id)
    if unit is None or unit.removed:
        raise HTTPException(404, "组织不存在")
    unit_ids = {unit.id}
    if body.include_children:
        units = list(db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False))))
        unit_ids = descendant_ids(units, unit.id)
    user_ids = set(db.scalars(
        select(User.id).where(User.org_unit_id.in_(unit_ids), User.enabled.is_(True))
    ))
    existing = set(db.scalars(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project.id,
                                            ProjectMember.role == body.role)
    ))
    added = sorted(user_ids - existing)
    for user_id in added:
        db.add(ProjectMember(project_id=project.id, user_id=user_id, role=body.role))
    db.commit()
    return {"added": len(added), "skipped": len(user_ids & existing), "total": len(user_ids)}


@router.patch("/{key}/members/roles")
def update_member_roles(
    key: str,
    body: MemberRolesBulkIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Set, add, or remove roles for existing project members in one transaction."""
    project = get_project(db, key, user, "admin")
    user_ids = set(body.user_ids)
    if body.set_roles is not None and (body.add_roles or body.remove_roles):
        raise HTTPException(400, "设置完整角色时不能同时增删角色")
    add_roles = set(body.add_roles)
    remove_roles = set(body.remove_roles)
    if body.set_roles is None and not add_roles and not remove_roles:
        raise HTTPException(400, "请指定要设置、新增或移除的角色")
    if add_roles & remove_roles:
        raise HTTPException(400, "同一角色不能同时新增和移除")
    requested = set(body.set_roles or []) | add_roles | remove_roles
    supported = set(profiles.profile_of(project).roles)
    if any(not role or len(role) > 32 for role in requested) or not requested <= supported:
        raise HTTPException(400, "项目模板不支持该角色")
    rows = list(
        db.scalars(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id.in_(user_ids),
            )
        )
    )
    found = {row.user_id for row in rows}
    if found != user_ids:
        raise HTTPException(404, "项目成员不存在")
    by_user: dict[int, dict[str, ProjectMember]] = {}
    for row in rows:
        by_user.setdefault(row.user_id, {})[row.role] = row
    added = removed = 0
    for user_id in user_ids:
        current = set(by_user[user_id])
        desired = (
            set(body.set_roles)
            if body.set_roles is not None
            else (current | add_roles) - remove_roles
        )
        for role in current - desired:
            db.delete(by_user[user_id][role])
            removed += 1
        for role in desired - current:
            db.add(ProjectMember(project_id=project.id, user_id=user_id, role=role))
            added += 1
    db.commit()
    return {"users": len(user_ids), "added": added, "removed": removed}


@router.delete("/{key}/members/{member_id}")
def remove_member(key: str, member_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    member = db.get(ProjectMember, member_id)
    if member is None or member.project_id != project.id:
        raise HTTPException(404, "成员不存在")
    db.delete(member)
    db.commit()
    return {"ok": True}


@router.post("/{key}/statuses")
def add_status(key: str, body: StatusIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    order = body.sort_order
    if order is None:
        order = (db.scalar(select(func.max(Status.sort_order)).where(
            Status.project_id == project.id)) or 0) + 1
    status = Status(project_id=project.id, name=body.name, category=body.category,
                    color=body.color, sort_order=order)
    db.add(status)
    db.flush()
    attach_status_column(db, project.id, status.id)
    from ..services import live

    live.queue(db, project_key=project.key, kind="project", actor_id=user.id, fields=["statuses"])
    db.commit()
    return status_out(status) or {}


@router.put("/{key}/statuses/{status_id}")
def update_status(key: str, status_id: int, body: StatusIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    status = db.get(Status, status_id)
    if status is None or status.project_id != project.id:
        raise HTTPException(404, "状态不存在")
    old_name = status.name
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(status, field, value)
    if old_name != status.name:
        settings = dict(project.settings or {})
        transitions = effective_transitions(project)
        renamed = {}
        for issue_type, rules in transitions.items():
            renamed[issue_type] = {
                (status.name if src == old_name else src): [
                    status.name if dst == old_name else dst for dst in dsts
                ]
                for src, dsts in rules.items()
            }
        settings["transitions"] = renamed
        for k in ("test_status", "pass_status", "fail_return_status", "dev_complete_status"):
            if settings.get(k) == old_name:
                settings[k] = status.name
        gates = dict(settings.get("transition_gates") or {})
        if old_name in gates:
            gates[status.name] = gates.pop(old_name)
            settings["transition_gates"] = gates
        props = dict(settings.get("status_properties") or {})
        if old_name in props:
            props[status.name] = props.pop(old_name)
            settings["status_properties"] = props
        project.settings = settings
        rename_status_column(db, project.id, status.id, old_name, status.name)
    from ..services import live

    live.queue(db, project_key=project.key, kind="project", actor_id=user.id, fields=["statuses"])
    db.commit()
    return status_out(status) or {}


@router.delete("/{key}/statuses/{status_id}")
def delete_status(key: str, status_id: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    status = db.get(Status, status_id)
    if status is None or status.project_id != project.id:
        raise HTTPException(404, "状态不存在")
    if db.scalar(select(func.count(Issue.id)).where(Issue.status_id == status_id)):
        raise HTTPException(409, "仍有问题处于该状态，无法删除")
    detach_status_column(db, project.id, status_id)
    db.delete(status)
    db.commit()
    return {"ok": True}


@router.post("/{key}/machine-groups")
def add_machine_group(key: str, body: MachineGroupIn, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "member")
    order = body.sort_order
    if order is None:
        order = (db.scalar(select(func.max(MachineGroup.sort_order)).where(
            MachineGroup.project_id == project.id)) or 0) + 1
    group = MachineGroup(project_id=project.id, name=body.name.strip(), sort_order=order)
    db.add(group)
    db.commit()
    return {"id": group.id, "name": group.name, "sort_order": group.sort_order, "machines": []}


@router.put("/{key}/machine-groups/{group_id}")
def update_machine_group(key: str, group_id: int, body: MachineGroupIn,
                         user: User = Depends(current_user), db: Session = Depends(get_db)
                         ) -> dict:
    project = get_project(db, key, user, "admin")
    group = db.get(MachineGroup, group_id)
    if group is None or group.project_id != project.id:
        raise HTTPException(404, "机台组不存在")
    group.name = body.name.strip()
    if body.sort_order is not None:
        group.sort_order = body.sort_order
    db.commit()
    return {"ok": True}


@router.delete("/{key}/machine-groups/{group_id}")
def delete_machine_group(key: str, group_id: int, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    group = db.get(MachineGroup, group_id)
    if group is None or group.project_id != project.id:
        raise HTTPException(404, "机台组不存在")
    db.delete(group)
    db.commit()
    return {"ok": True}


@router.post("/{key}/machines")
def add_machine(key: str, body: MachineIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "member")
    group = db.get(MachineGroup, body.group_id)
    if group is None or group.project_id != project.id:
        raise HTTPException(404, "机台组不存在")
    order = body.sort_order
    if order is None:
        order = (db.scalar(select(func.max(Machine.sort_order)).where(
            Machine.group_id == group.id)) or 0) + 1
    machine = Machine(group_id=group.id, name=body.name.strip(), sort_order=order,
                      active=body.active)
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine_out(machine)


@router.put("/{key}/machines/{machine_id}")
def update_machine(key: str, machine_id: int, body: MachineIn,
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    machine = db.get(Machine, machine_id)
    if machine is None or machine.group.project_id != project.id:
        raise HTTPException(404, "机台不存在")
    group = db.get(MachineGroup, body.group_id)
    if group is None or group.project_id != project.id:
        raise HTTPException(404, "机台组不存在")
    machine.name = body.name.strip()
    machine.active = body.active
    machine.group_id = group.id
    if body.sort_order is not None:
        machine.sort_order = body.sort_order
    db.commit()
    return machine_out(machine)


@router.delete("/{key}/machines/{machine_id}")
def delete_machine(key: str, machine_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    machine = db.get(Machine, machine_id)
    if machine is None or machine.group.project_id != project.id:
        raise HTTPException(404, "机台不存在")
    db.delete(machine)
    db.commit()
    return {"ok": True}
