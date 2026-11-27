from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import (
    current_user,
    get_issue,
    get_project,
    issue_by_id,
    require_writable_issue,
    require_writable_project,
)
from ..models import (
    ActivityLog,
    ApprovalRequest,
    Attachment,
    Comment,
    Issue,
    IssueLink,
    Project,
    Status,
    User,
    Watcher,
)
from ..schemas import (
    ApprovalDecisionIn,
    BulkUpdate,
    CloneIn,
    IssueCreate,
    IssueFields,
    IssuePatch,
    LinkIn,
    RankIn,
    TransitionRequestIn,
)
from ..serialize import (
    attachment_out,
    iso,
    issue_brief,
    issue_brief_enriched,
    issue_query,
    issue_ref,
    status_out,
    user_brief,
)
from ..services import approvals, concurrency, permissions, profiles, subtasks, workflow
from ..services.issues import (
    FIELD_LABELS,
    RANK_STEP,
    IssueError,
    clone_issue,
    create_issue,
    log,
    notify,
    update_issue,
    watch,
)
from .attachments import discard_upload, finalize_upload, store_upload

router = APIRouter(prefix="/api", tags=["issues"])

LINK_LABELS = {
    "blocks": ("阻塞", "被阻塞"),
    "duplicates": ("重复于", "被重复"),
    "relates": ("关联", "关联"),
}


def issue_detail(db: Session, issue: Issue, user: User) -> dict:
    project = db.get(Project, issue.project_id)
    assert project is not None
    roles = permissions.project_roles(db, project.id, user)
    statuses = workflow.project_statuses(db, project.id)
    targets = [
        s for s in workflow.allowed_targets(project, issue.type, issue.status, statuses)
        if (
            not workflow.requires_assignee_accept(project, issue.status, s)
            or permissions.is_project_admin(user, roles)
            or issue.assignee_id == user.id
        ) and (
            not workflow.is_verification_status(project, s)
            or permissions.may_enter_verification(user, project, issue, roles)
        )
    ]
    children = [
        child
        for child in db.scalars(
            issue_query()
            .where(or_(Issue.parent_id == issue.id, Issue.epic_id == issue.id))
            .order_by(Issue.rank)
        )
        if permissions.can_view_issue(
            user,
            child,
            child.project,
            permissions.project_roles(db, child.project_id, user),
        )
    ]
    links_out = []
    for link in db.scalars(
        select(IssueLink).where(
            or_(IssueLink.source_id == issue.id, IssueLink.target_id == issue.id)
        )
    ):
        outward = link.source_id == issue.id
        other = db.scalars(
            issue_query().where(Issue.id == (link.target_id if outward else link.source_id))
        ).first()
        if other is None or other.parent_id == issue.id or other.id == issue.parent_id:
            continue
        if not permissions.can_view_issue(
            user, other, other.project, permissions.project_roles(db, other.project_id, user)
        ):
            continue
        label = LINK_LABELS.get(link.link_type, (link.link_type, link.link_type))
        links_out.append(
            {
                "id": link.id,
                "link_type": link.link_type,
                "lag_days": link.lag_days,
                "label": label[0] if outward else label[1],
                "issue": {**issue_ref(other), "status": status_out(other.status)},
            }
        )
    watchers = db.scalars(
        select(User).join(Watcher, Watcher.user_id == User.id).where(Watcher.issue_id == issue.id)
    )
    watcher_list = [user_brief(w) for w in watchers]
    attachments = db.scalars(
        select(Attachment)
        .options(selectinload(Attachment.uploader))
        .where(Attachment.issue_id == issue.id)
        .order_by(Attachment.id)
    )
    comment_count = db.scalar(select(func.count(Comment.id)).where(Comment.issue_id == issue.id))
    profile = profiles.profile_of(project)
    custom_users = {}
    for field in profile.custom_fields:
        value = (issue.extra or {}).get(field.key)
        if field.kind == "user" and isinstance(value, int):
            referenced = db.get(User, value)
            if referenced is not None:
                custom_users[field.key] = user_brief(referenced)
    parent = issue.parent
    parent_visible = parent is not None and permissions.can_view_issue(
        user,
        parent,
        parent.project,
        permissions.project_roles(db, parent.project_id, user),
    )
    return {
        **issue_brief(issue),
        "description": issue.description,
        "extra": issue.extra or {},
        "children": [issue_brief(c) for c in children],
        "parent_progress": (
            subtasks.progress(db, [issue.parent_id]).get(issue.parent_id)
            if issue.parent_id and parent_visible else None
        ),
        "parent_info": ({**issue_ref(parent), "status": status_out(parent.status),
                         "assignee": user_brief(parent.assignee)}
                        if parent_visible and parent is not None else None),
        "links": links_out,
        "watchers": watcher_list,
        "watching": any(w and w["id"] == user.id for w in watcher_list),
        "attachments": [attachment_out(a) for a in attachments],
        "comment_count": comment_count,
        "custom_field_users": custom_users,
        "editable_sections": sorted(permissions.editable_sections(user, issue, roles)),
        "transitions": [status_out(s) for s in targets if s.id != issue.status_id],
        "test_result_editable": workflow.test_result_editable(project, issue, statuses),
        "software_test_wait": _software_test_wait(issue),
        "software_jira_people": _software_jira_people(db, issue),
        "can_delete": permissions.is_project_admin(user, roles) or issue.reporter_id == user.id,
        "approval_requests": [
            approvals.out(row) for row in db.scalars(
                select(ApprovalRequest).where(ApprovalRequest.issue_id == issue.id)
                .order_by(ApprovalRequest.id.desc()).limit(20)
            )
        ],
    }


def _software_jira_people(db: Session, issue: Issue) -> dict:
    from ..services.software_jira import match_directory_user

    raw = issue.software_jira_snapshot
    snapshot = raw if isinstance(raw, dict) else {}
    return {
        "assignee": user_brief(match_directory_user(db, snapshot.get("assignee"))),
        "tester": user_brief(match_directory_user(db, snapshot.get("tester"))),
    }


def _software_test_wait(issue: Issue) -> str | None:
    from ..services.software_jira import software_test_wait_text

    return software_test_wait_text(issue)


def _reload(db: Session, issue_id: int) -> Issue:
    db.expire_all()
    return db.scalars(issue_query().where(Issue.id == issue_id)).one()


@router.post("/issues")
def create(body: IssueCreate, user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> dict:
    project = get_project(db, body.project, user, need="member")
    require_writable_project(db, project, user)
    data = body.model_dump(exclude={"project"}, exclude_none=True)
    issue = create_issue(db, project, user, data)
    db.commit()
    return issue_detail(db, _reload(db, issue.id), user)


STAGED_TOKEN = re.compile(r"^[a-z0-9]{6,32}$")
STAGED_IMAGE = re.compile(r"!\[[^\]\n]*\]\(staged:([a-z0-9]{6,32})\)\n?")
MAX_STAGED_FILES = 30


def _resolve_staged(text: str, links: dict[str, str]) -> str:
    """Swap ``![..](staged:token)`` placeholders for attachment links; drop unmatched ones."""
    def swap(match: re.Match[str]) -> str:
        link = links.get(match.group(1))
        if link is None:
            return ""
        return link + ("\n" if match.group(0).endswith("\n") else "")
    return STAGED_IMAGE.sub(swap, text)


@router.post("/issues/with-attachments")
async def create_with_attachments(
    payload: str = Form(...), tokens: str = Form("[]"),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    """Create an issue whose Markdown fields reference screenshots picked before it existed."""
    try:
        body = IssueCreate.model_validate_json(payload)
        token_list = json.loads(tokens)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(400, "附件参数无效") from exc
    if (not isinstance(token_list, list) or len(token_list) != len(files)
            or len(files) > MAX_STAGED_FILES
            or not all(isinstance(tk, str) and STAGED_TOKEN.match(tk) for tk in token_list)):
        raise HTTPException(400, "附件参数无效")
    project = get_project(db, body.project, user, need="member")
    require_writable_project(db, project, user)
    issue = create_issue(db, project, user, body.model_dump(exclude={"project"}, exclude_none=True))
    db.flush()
    stored: list[Attachment] = []
    try:
        links: dict[str, str] = {}
        for token, upload in zip(token_list, files, strict=True):
            att = await store_upload(db, issue, user, upload)
            stored.append(att)
            db.flush()
            url = f"/api/attachments/{att.id}"
            alt = re.sub(r"[\[\]\n]", "", att.filename)
            links[token] = (f"![{alt}]({url}?inline=true)"
                            if att.content_type.startswith("image/") else f"[{alt}]({url})")
            log(db, issue, user, "attachment", new=att.filename)
        for field in ("description", "verify_method", "verify_criteria", "remark"):
            value = getattr(issue, field)
            if value:
                setattr(issue, field, _resolve_staged(value, links))
        if issue.extra:
            issue.extra = {
                key: _resolve_staged(value, links) if isinstance(value, str) else value
                for key, value in issue.extra.items()
            }
        db.commit()
    except BaseException:
        db.rollback()
        for att in stored:
            discard_upload(att)
        raise
    for att in stored:
        finalize_upload(att)
    return issue_detail(db, _reload(db, issue.id), user)


@router.get("/issues/{key}")
def read(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    from ..services.software_jira import absorb_snapshot

    issue = get_issue(db, key, user)
    if absorb_snapshot(db, issue):
        db.commit()
        issue = _reload(db, issue.id)
    return issue_detail(db, issue, user)


@router.get("/issues/{key}/parent-candidates")
def parent_candidates(key: str, q: str = "", user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> dict:
    """Issues that may become this issue's parent: same project, not a sub-task or epic."""
    issue = get_issue(db, key, user)
    stmt = select(Issue).options(selectinload(Issue.status)).where(
        Issue.project_id == issue.project_id, Issue.id != issue.id,
        Issue.type.notin_(("subtask", "epic")),
    )
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        stmt = stmt.where(visible)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Issue.key.ilike(like), Issue.title.ilike(like)))
    rows = db.scalars(stmt.order_by(Issue.updated_at.desc()).limit(20))
    return {"issues": [{**issue_ref(i), "status": status_out(i.status)} for i in rows]}


@router.patch("/issues/{key}")
def patch(key: str, body: IssuePatch, user: User = Depends(current_user),
          db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    issue = concurrency.lock_versioned(db, Issue, issue.id, body.expected_version)
    changes = body.model_dump(
        exclude_unset=True,
        exclude={"close_subtasks", "expected_version"},
    )
    if not changes:
        return issue_detail(db, issue, user)
    # reopen_reason is not an issue column — update_issue pops it for the transition log/notify.
    try:
        update_issue(db, issue, user, changes, close_subtasks=body.close_subtasks)
    except IssueError as error:
        if error.code == "software_pending":
            db.commit()
        raise
    db.commit()
    return issue_detail(db, _reload(db, issue.id), user)


@router.post("/issues/{key}/software-jira/refresh")
def refresh_software_jira(
    key: str, user: User = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    """Re-read the software Jira relay. The snapshot stays read-only."""
    from ..services.issues import IssueError
    from ..services.software_jira import SoftwareJiraNotConfigured, sync_snapshot

    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    if not (issue.software_jira_key or "").strip():
        raise IssueError("请先填写软件 Jira 单号")
    try:
        sync_snapshot(issue, required=True)
    except SoftwareJiraNotConfigured as error:
        raise IssueError(str(error)) from error
    db.commit()
    return issue_detail(db, _reload(db, issue.id), user)


@router.post("/issues/{key}/transition-requests")
def request_transition(
    key: str, body: TransitionRequestIn, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    issue = concurrency.lock_versioned(db, Issue, issue.id, body.expected_version)
    target = db.get(Status, body.status_id)
    if target is None or target.project_id != issue.project_id:
        raise HTTPException(404, "状态不存在")
    row = approvals.request(db, issue, target, user, body.comment)
    db.commit()
    return approvals.out(row)


@router.post("/approval-requests/{request_id}/decision")
def approval_decision(
    request_id: int, body: ApprovalDecisionIn, user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ApprovalRequest, request_id)
    if row is None:
        raise HTTPException(404, "审批不存在")
    row = concurrency.lock_versioned(
        db,
        ApprovalRequest,
        row.id,
        body.expected_version,
    )
    issue = get_issue(db, db.get(Issue, row.issue_id).key, user)
    issue = concurrency.lock_versioned(db, Issue, issue.id)
    complete = approvals.decide(db, row, issue, user, body.approve, body.comment)
    if complete:
        try:
            update_issue(
                db, issue, user, {"status_id": row.target_status_id},
                enforce=False, skip_approval_gate=True,
            )
        except IssueError as error:
            row.status = "cancelled"
            row.finished_at = utcnow()
            db.commit()
            raise HTTPException(error.status_code, error.message) from error
    db.commit()
    return {"request": approvals.out(row), "issue": issue_detail(db, _reload(db, issue.id), user)}


@router.post("/issues/{key}/clone")
def clone(key: str, body: CloneIn | None = None, user: User = Depends(current_user),
          db: Session = Depends(get_db)) -> dict:
    source = get_issue(db, key, user)
    project = db.get(Project, source.project_id)
    assert project is not None
    get_project(db, project.key, user, need="member")
    opts = body or CloneIn()
    issue = clone_issue(
        db, source, user,
        include_assignee=opts.include_assignee,
        include_sprint=opts.include_sprint,
        relate=opts.relate,
    )
    db.commit()
    return issue_detail(db, _reload(db, issue.id), user)


@router.delete("/issues/{key}")
def delete(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    roles = require_writable_issue(db, issue, user)
    if not (permissions.is_project_admin(user, roles) or issue.reporter_id == user.id):
        raise HTTPException(403, "只有报告人或项目管理员可以删除")
    db.execute(update(Issue).where(Issue.parent_id == issue.id)
               .values(parent_id=None, type="task"))
    db.execute(update(Issue).where(Issue.epic_id == issue.id).values(epic_id=None))
    db.delete(issue)
    db.commit()
    return {"ok": True}


@router.post("/issues/bulk")
def bulk(body: BulkUpdate, user: User = Depends(current_user),
         db: Session = Depends(get_db)) -> dict:
    allowed = set(IssueFields.model_fields)
    unknown = set(body.changes) - allowed
    if unknown:
        raise HTTPException(400, f"不支持批量修改：{', '.join(sorted(unknown))}")
    validated = IssueFields.model_validate(body.changes).model_dump(exclude_unset=True)
    results = {"updated": [], "failed": []}
    for key in body.keys[:500]:
        issue_id = None
        try:
            issue = get_issue(db, key, user)
            require_writable_issue(db, issue, user)
            issue_id = issue.id
            with db.begin_nested():
                changed = update_issue(db, issue, user, dict(validated))
            results["updated"].append({"key": issue.key, "changed": changed})
        except (IssueError, HTTPException) as error:
            if isinstance(error, IssueError) and error.code == "software_pending":
                from ..services.software_jira import mark_software_wait

                row = db.get(Issue, issue_id)
                if row is not None:
                    mark_software_wait(row, True)
            message = error.message if isinstance(error, IssueError) else error.detail
            results["failed"].append({"key": key, "error": message})
    db.commit()
    return results


@router.post("/issues/{key}/rank")
def rank(key: str, body: RankIn, user: User = Depends(current_user),
         db: Session = Depends(get_db)) -> dict:
    """Drag & drop: reorder between neighbours, optionally change status / sprint."""
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    issue = concurrency.lock_versioned(db, Issue, issue.id, body.expected_version)
    changes: dict = {}
    if body.status_id is not None and body.status_id != issue.status_id:
        changes["status_id"] = body.status_id
        if body.reopen_reason:
            changes["reopen_reason"] = body.reopen_reason
    if body.move_to_backlog:
        if issue.sprint_id is not None:
            changes["sprint_id"] = None
    elif body.sprint_id is not None and body.sprint_id != issue.sprint_id:
        changes["sprint_id"] = body.sprint_id
    before = get_issue(db, body.before_key, user) if body.before_key else None
    after = get_issue(db, body.after_key, user) if body.after_key else None
    for neighbour in (before, after):
        if neighbour is not None and neighbour.project_id != issue.project_id:
            raise HTTPException(400, "排序锚点必须属于同一项目")
    if before is not None and after is not None:
        new_rank = (before.rank + after.rank) / 2
        if abs(after.rank - before.rank) < 1e-6:
            _rebalance(db, issue.project_id)
            db.refresh(before)
            db.refresh(after)
            new_rank = (before.rank + after.rank) / 2
    elif before is not None:
        new_rank = before.rank + RANK_STEP
    elif after is not None:
        new_rank = after.rank - RANK_STEP
    else:
        new_rank = issue.rank
    changes["rank"] = new_rank
    try:
        update_issue(db, issue, user, changes, close_subtasks=body.close_subtasks)
    except IssueError as error:
        if error.code == "software_pending":
            db.commit()
        raise
    db.commit()
    return issue_brief_enriched(db, _reload(db, issue.id))


def _rebalance(db: Session, project_id: int) -> None:
    # Lock the project row so concurrent drags cannot interleave a full rewrite.
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        return
    rows = db.scalars(
        select(Issue).where(Issue.project_id == project_id).order_by(Issue.rank).with_for_update()
    )
    for index, row in enumerate(rows, start=1):
        row.rank = index * RANK_STEP
    db.flush()


@router.get("/issues/{key}/activity")
def activity(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    rows = db.scalars(
        select(ActivityLog)
        .options(selectinload(ActivityLog.actor))
        .where(ActivityLog.issue_id == issue.id)
        .order_by(ActivityLog.id.desc())
        .limit(300)
    )
    return {
        "activity": [
            {
                "id": r.id,
                "actor": user_brief(r.actor),
                "action": r.action,
                "field": r.field,
                "label": FIELD_LABELS.get(r.field, r.field) if r.field else None,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "meta": r.meta or {},
                "created_at": iso(r.created_at),
            }
            for r in rows
        ]
    }


@router.post("/issues/{key}/watch")
def watch_issue(key: str, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    watch(db, issue.id, user.id)
    db.commit()
    return {"watching": True}


@router.delete("/issues/{key}/watch")
def unwatch_issue(key: str, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    db.execute(
        sql_delete(Watcher).where(
            Watcher.issue_id == issue.id,
            Watcher.user_id == user.id,
        )
    )
    db.commit()
    return {"watching": False}


@router.post("/issues/{key}/links")
def add_link(key: str, body: LinkIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    target = get_issue(db, body.target_key, user)
    if target.id == issue.id:
        raise HTTPException(400, "不能关联自身")
    if body.link_type == "blocks" and target.project_id != issue.project_id:
        raise HTTPException(400, "阻塞依赖只能连接同一项目目标内的问题")
    if target.type == "subtask" and issue.type not in ("subtask", "epic"):
        return _adopt_subtask(db, issue, target, user, body.reparent)
    exists = db.scalar(
        select(IssueLink).where(
            IssueLink.source_id == issue.id,
            IssueLink.target_id == target.id,
            IssueLink.link_type == body.link_type,
        )
    )
    if exists is None:
        db.add(IssueLink(
            source_id=issue.id,
            target_id=target.id,
            link_type=body.link_type,
            lag_days=body.lag_days,
            created_by_id=user.id,
        ))
        log(db, issue, user, "link", new=f"{LINK_LABELS[body.link_type][0]} {target.key}")
        notify(db, [target.assignee_id], user, target, "link",
               f"{user.display_name} 将 {issue.key} 与 {target.key} 建立了关联")
        db.commit()
    return issue_detail(db, _reload(db, issue.id), user)


def _adopt_subtask(db: Session, issue: Issue, target: Issue, user: User, reparent: bool) -> dict:
    """Linking a sub-task makes it one of this issue's sub-tasks instead of a plain link."""
    if target.parent_id == issue.id:
        return {**issue_detail(db, issue, user), "link_result": "already_subtask"}
    if target.project_id != issue.project_id:
        raise HTTPException(400, "子任务只能挂在同一项目的问题下")
    if target.parent_id is not None and not reparent:
        message = (f"{target.key} 目前是 {target.parent.key} 的子任务，"
                   f"要改为 {issue.key} 的子任务吗？")
        raise IssueError(message, 409, code="subtask_has_parent")
    update_issue(db, target, user, {"parent_id": issue.id})
    log(db, issue, user, "link", new=f"子任务 {target.key}")
    db.commit()
    return {**issue_detail(db, _reload(db, issue.id), user), "link_result": "subtask_added"}


@router.delete("/links/{link_id}")
def delete_link(link_id: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    link = db.get(IssueLink, link_id)
    if link is None:
        raise HTTPException(404, "关联不存在")
    issue = issue_by_id(db, link.source_id, user)
    if issue is None:
        raise HTTPException(404, "关联不存在")
    require_writable_issue(db, issue, user)
    log(db, issue, user, "unlink", old=str(link.target_id))
    db.delete(link)
    db.commit()
    return {"ok": True}


@router.get("/projects/{key}/issues")
def project_issues(
    key: str,
    limit: int = Query(1000, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    project = get_project(db, key, user)
    rows = db.scalars(
        issue_query().where(Issue.project_id == project.id).order_by(Issue.number).limit(limit)
    )
    rows = list(rows)
    return {"issues": subtasks.briefs(db, rows)}


@router.get("/statuses/{status_id}")
def status_info(status_id: int, _user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    status = db.get(Status, status_id)
    if status is None:
        raise HTTPException(404)
    return status_out(status) or {}
