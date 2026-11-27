"""Project summary (Jira-style "Summary" tab) and timeline rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, selectinload

from ..db import utcnow
from ..models import (
    ActivityLog,
    Issue,
    Project,
    ProjectScheduleBaseline,
    Sprint,
    Status,
    User,
)
from ..serialize import iso, issue_ref, status_out, user_brief
from . import permissions, workflow
from .reports import _aware

PRIORITIES = ("highest", "high", "medium", "low", "lowest")


def _issues(db: Session, project: Project, user: User) -> list[Issue]:
    stmt = (
        select(Issue)
        .options(selectinload(Issue.status), selectinload(Issue.assignee),
                 selectinload(Issue.epic), selectinload(Issue.parent))
        .where(Issue.project_id == project.id)
    )
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        stmt = stmt.where(visible)
    return list(db.scalars(stmt))


def _day(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    return _aware(value).date() if isinstance(value, datetime) else value


def summary(db: Session, project: Project, user: User) -> dict[str, Any]:
    issues = _issues(db, project, user)
    now, today = utcnow(), date.today()
    week_ago = now - timedelta(days=7)
    done = [i for i in issues if workflow.is_completed(i.status)]
    open_issues = [i for i in issues if not workflow.is_closed(i.status)]
    statuses = list(db.scalars(
        select(Status).where(Status.project_id == project.id).order_by(Status.sort_order)
    ))
    by_status = Counter(i.status_id for i in issues)
    by_priority = Counter(i.priority for i in issues)
    by_type = Counter(i.type for i in issues)

    workload: dict[int | None, dict[str, Any]] = {}
    for issue in open_issues:
        row = workload.setdefault(issue.assignee_id, {"user": user_brief(issue.assignee),
                                                      "count": 0})
        row["count"] += 1

    children: dict[int, list[Issue]] = defaultdict(list)
    for issue in issues:
        if issue.epic_id and issue.type != "epic":
            children[issue.epic_id].append(issue)
    epics = sorted((i for i in issues if i.type == "epic"), key=lambda i: i.number)

    return {
        "total": len(issues),
        "counts": {
            "completed": sum(1 for i in done
                             if i.resolved_at and _aware(i.resolved_at) >= week_ago),
            "updated": sum(1 for i in issues if _aware(i.updated_at) >= week_ago),
            "created": sum(1 for i in issues if _aware(i.created_at) >= week_ago),
            "due_soon": sum(1 for i in open_issues
                            if i.due_date and today <= i.due_date <= today + timedelta(days=7)),
            "overdue": sum(1 for i in open_issues if i.due_date and i.due_date < today),
        },
        "statuses": [{**(status_out(s) or {}), "count": by_status.get(s.id, 0)} for s in statuses],
        "priorities": [{"priority": p, "count": by_priority.get(p, 0)} for p in PRIORITIES],
        "types": [{"type": k, "count": n} for k, n in by_type.most_common()],
        "workload": sorted(workload.values(),
                           key=lambda r: (r["user"] is None, -r["count"])),
        "open": len(open_issues),
        "epics": [
            {
                **(issue_ref(epic) or {}),
                "status": status_out(epic.status),
                "total": len(children[epic.id]),
                "done": sum(1 for c in children[epic.id] if workflow.is_completed(c.status)),
            }
            for epic in epics
        ],
    }


def _personal_statuses(issues: list[Issue]) -> list[dict[str, Any]]:
    """Merge statuses across projects by name; keep top 10 + Other."""
    buckets: dict[str, dict[str, Any]] = {}
    for issue in issues:
        status = issue.status
        row = buckets.setdefault(status.name, {
            "name": status.name,
            "categories": Counter(),
            "colors": Counter(),
            "ids": [],
            "count": 0,
        })
        row["count"] += 1
        row["categories"][status.category] += 1
        row["colors"][status.color] += 1
        row["ids"].append(status.id)
    ranked = sorted(buckets.values(), key=lambda r: (-r["count"], r["name"]))
    top, rest = ranked[:10], ranked[10:]
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(top):
        out.append({
            "id": row["ids"][0],
            "name": row["name"],
            "category": row["categories"].most_common(1)[0][0],
            "color": row["colors"].most_common(1)[0][0],
            "sort_order": idx,
            "count": row["count"],
        })
    if rest:
        out.append({
            "id": -1,
            "name": "其他",
            "category": "todo",
            "color": "#8e8e93",
            "sort_order": 99,
            "count": sum(r["count"] for r in rest),
        })
    return out


def personal_summary(db: Session, user: User) -> dict[str, Any]:
    """My Work progress: issues assigned to the current user across projects."""
    stmt = (
        select(Issue)
        .options(selectinload(Issue.status))
        .where(Issue.assignee_id == user.id)
    )
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        stmt = stmt.where(visible)
    issues = list(db.scalars(stmt))
    now, today = utcnow(), date.today()
    week_ago = now - timedelta(days=7)
    done = [i for i in issues if workflow.is_completed(i.status)]
    open_issues = [i for i in issues if not workflow.is_closed(i.status)]
    by_priority = Counter(i.priority for i in issues)
    by_type = Counter(i.type for i in issues)
    return {
        "total": len(issues),
        "open": len(open_issues),
        "counts": {
            "completed": sum(1 for i in done
                             if i.resolved_at and _aware(i.resolved_at) >= week_ago),
            "updated": sum(1 for i in issues if _aware(i.updated_at) >= week_ago),
            "created": sum(1 for i in issues if _aware(i.created_at) >= week_ago),
            "due_soon": sum(1 for i in open_issues
                            if i.due_date and today <= i.due_date <= today + timedelta(days=7)),
            "overdue": sum(1 for i in open_issues if i.due_date and i.due_date < today),
        },
        "statuses": _personal_statuses(issues),
        "priorities": [{"priority": p, "count": by_priority.get(p, 0)} for p in PRIORITIES],
        "types": [{"type": k, "count": n} for k, n in by_type.most_common()],
    }


def activity_page(db: Session, scope: list[ColumnElement[bool]], page: int,
                  size: int) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(ActivityLog).where(*scope)) or 0
    rows = list(db.scalars(
        select(ActivityLog).options(selectinload(ActivityLog.actor)).where(*scope)
        .order_by(ActivityLog.id.desc()).offset((page - 1) * size).limit(size)
    ))
    issue_map = {
        i.id: i for i in db.scalars(select(Issue).where(Issue.id.in_({a.issue_id for a in rows})))
    }
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [
            {
                "id": a.id,
                "actor": user_brief(a.actor),
                "action": a.action,
                "field": a.field,
                "old_value": a.old_value,
                "new_value": a.new_value,
                "issue": {"key": issue_map[a.issue_id].key, "title": issue_map[a.issue_id].title,
                          "type": issue_map[a.issue_id].type}
                if a.issue_id in issue_map else None,
                "created_at": iso(a.created_at),
            }
            for a in rows
        ],
    }


def project_activity(db: Session, project: Project, user: User, page: int,
                     size: int) -> dict[str, Any]:
    issue_ids = select(Issue.id).where(Issue.project_id == project.id)
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        issue_ids = issue_ids.where(visible)
    return activity_page(db, [ActivityLog.issue_id.in_(issue_ids)], page, size)


def timeline(db: Session, project: Project, user: User) -> dict[str, Any]:
    """Epics and their work as date bars; sub-tasks roll up into their parent."""
    issues = [i for i in _issues(db, project, user) if i.type != "subtask"]
    ongoing = permissions.is_ongoing(project)
    baseline = None if ongoing else db.scalar(
        select(ProjectScheduleBaseline).where(
            ProjectScheduleBaseline.project_id == project.id,
            ProjectScheduleBaseline.active.is_(True),
        )
    )
    baseline_rows = {
        int(row["id"]): row
        for row in (baseline.snapshot.get("issues", []) if baseline else [])
        if row.get("id") is not None
    }
    sprints = list(db.scalars(
        select(Sprint).where(Sprint.project_id == project.id).order_by(Sprint.id)
    ))
    sprint_end = {s.id: s.end_date for s in sprints}

    def bar(issue: Issue) -> dict[str, Any]:
        planned_start = issue.planned_start_date
        start = planned_start or project.plan_start_date or _day(issue.created_at) or date.today()
        if issue.status.category == "done":
            end = _day(issue.resolved_at) or issue.due_date
        else:
            end = issue.due_date or (sprint_end.get(issue.sprint_id) if issue.sprint_id else None)
        if end is not None and end < start:
            end = start
        return {
            "id": issue.id,
            "key": issue.key,
            "title": issue.title,
            "type": issue.type,
            "status": status_out(issue.status),
            "priority": issue.priority,
            "assignee": user_brief(issue.assignee),
            "epic_key": issue.epic.key if issue.epic and issue.type != "epic" else None,
            "sprint_id": issue.sprint_id,
            "start": start.isoformat(),
            "end": end.isoformat() if end else None,
            "planned_start_date": iso(planned_start),
            "due_date": iso(issue.due_date),
            "baseline_start": None if ongoing else baseline_rows.get(issue.id, {}).get(
                "planned_start_date"
            ),
            "baseline_due": None if ongoing else baseline_rows.get(issue.id, {}).get(
                "due_date"
            ),
            "date_quality": {
                "missing_start": False if ongoing else planned_start is None,
                "missing_due": False if ongoing else issue.due_date is None,
            },
            "resolved_at": iso(issue.resolved_at),
        }

    rows = {i.id: bar(i) for i in issues}
    for epic in (i for i in issues if i.type == "epic"):
        mine = [rows[i.id] for i in issues if i.epic_id == epic.id and i.type != "epic"]
        row = rows[epic.id]
        starts = [row["start"], *(c["start"] for c in mine)]
        ends = [e for e in [row["end"], *(c["end"] for c in mine)] if e]
        row["start"] = min(starts)
        row["end"] = max(ends) if ends else None
        row["total"] = len(mine)
        row["done"] = sum(
            1 for c in mine
            if c["status"]["category"] == "done" and c["status"]["name"] != workflow.REJECTED
        )
    ordered = sorted(issues, key=lambda i: i.number)
    return {
        "today": date.today().isoformat(),
        "baseline_version": baseline.version if baseline else None,
        "sprints": [
            {"id": s.id, "name": s.name, "state": s.state,
             "start": iso(s.start_date or _day(s.started_at)),
             "end": iso(s.end_date or _day(s.completed_at))}
            for s in sprints
        ],
        "items": [rows[i.id] for i in ordered],
    }
