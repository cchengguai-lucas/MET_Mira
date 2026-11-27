"""Project statistics: distributions, health, quality, burndown and cumulative flow."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased, selectinload

from ..models import (
    ActivityLog,
    Issue,
    IssueLink,
    IssueMachine,
    Machine,
    MasterAsset,
    Project,
    Sprint,
    Status,
    User,
)
from ..serialize import iso, issue_brief, issue_query, user_brief
from . import permissions, profiles, workflow

STALE_DAYS = 14
DUE_SOON_DAYS = 7
FLOW_DAYS = 28
RISK_LIST_SIZE = 50
QUEUE_SIZE = 20
# Days in the current status after which open work counts as aging (warn) / over-aged (danger).
AGING_WARN_DAYS = 7
AGING_DANGER_DAYS = 14
AGING_LIST_SIZE = 200
RISK_LISTS = ("overdue", "due_soon", "blocked", "stale", "unassigned", "no_due", "net_flow")


def _end_of(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _summary_issues(db: Session, project_id: int, include_subtasks: bool) -> list[Issue]:
    """Lean load for summary charts — no description / people / epic graph."""
    stmt = (
        select(Issue)
        .where(Issue.project_id == project_id)
        .options(
            selectinload(Issue.status),
            selectinload(Issue.assignee),
            selectinload(Issue.machine_links)
            .selectinload(IssueMachine.machine)
            .selectinload(Machine.group),
        )
    )
    if not include_subtasks:
        stmt = stmt.where(Issue.type != "subtask")
    return list(db.scalars(stmt))


def summary(db: Session, project: Project, days: int = 30,
            include_subtasks: bool = True) -> dict[str, Any]:
    issues = _summary_issues(db, project.id, include_subtasks)
    statuses = list(
        db.scalars(select(Status).where(Status.project_id == project.id)
                   .order_by(Status.sort_order))
    )
    by_status = Counter(i.status_id for i in issues)
    open_issues = [i for i in issues if i.status.category != "done"]
    by_assignee: Counter[str] = Counter(
        i.assignee.display_name if i.assignee else "未分配" for i in open_issues
    )
    by_group: dict[str, dict[str, int]] = defaultdict(lambda: {"open": 0, "done": 0})
    for issue in issues:
        groups = {m.group.name for m in issue.machines if m.group} or {"未指定机台"}
        for group in groups:
            by_group[group]["done" if issue.status.category == "done" else "open"] += 1
    today = date.today()
    work = [i for i in issues if i.type != "epic"]
    history = StatusHistory(db, project, work)
    trend = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        completed, rejected = history.finished_between(work, _start_of(day), _end_of(day))
        trend.append(
            {
                "date": day.isoformat(),
                "created": sum(1 for i in work if _aware(i.created_at).date() == day),
                "completed": len(completed),
                "rejected": len(rejected),
            }
        )
    tested = [i for i in issues if i.test_passed is not None]
    profile = profiles.profile_of(project)
    profile_distributions = {}
    configured = {
        str(field["key"]): field for field in (profile.definition or {}).get("fields") or []
    }
    for field in profile.custom_fields:
        if not configured.get(field.key, {}).get("report", False):
            continue
        values = [
            (issue.extra or {}).get(field.key) for issue in issues
            if (issue.extra or {}).get(field.key) not in (None, "", [])
        ]
        if field.kind == "master_ref":
            ids = {
                item for value in values
                for item in (value if isinstance(value, list) else [value])
                if isinstance(item, int)
            }
            assets = {
                asset.id: asset.name
                for asset in db.scalars(select(MasterAsset).where(MasterAsset.id.in_(ids)))
            }
            counts = Counter(
                assets.get(item, str(item)) for value in values
                for item in (value if isinstance(value, list) else [value])
            )
        else:
            counts = Counter(str(value) for value in values)
        profile_distributions[field.key] = {
            "label": field.label, "values": dict(counts)
        }
    window = _test_results(db, project, _start_of(today - timedelta(days=days - 1)),
                           {i.id for i in issues})
    return {
        "total": len(issues),
        "open": len(open_issues),
        "overdue": sum(1 for i in open_issues if i.due_date and i.due_date < today),
        "by_status": [
            {"id": s.id, "name": s.name, "color": s.color, "category": s.category,
             "count": by_status.get(s.id, 0)}
            for s in statuses
        ],
        "by_type": dict(Counter(i.type for i in issues)),
        "by_priority": dict(Counter(i.priority for i in open_issues)),
        "by_assignee": [{"name": n, "count": c} for n, c in by_assignee.most_common(15)],
        "by_machine_group": [{"name": n, **v} for n, v in sorted(by_group.items())],
        "by_scope": (
            dict(Counter(s for i in issues for s in (i.update_scope or [])))
            if any("update_scope" in section.fields for section in profile.sections) else {}
        ),
        "profile_distributions": profile_distributions,
        "trend": trend,
        "test": {
            "tested": len(tested),
            "passed": sum(1 for i in tested if i.test_passed),
            "failed": sum(1 for i in tested if not i.test_passed),
        },
        "test_window": {
            "days": days,
            "passed": sum(1 for r in window if r.new_value == "是"),
            "failed": sum(1 for r in window if r.new_value == "否"),
        },
    }


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _project_issues(db: Session, project: Project, include_subtasks: bool) -> list[Issue]:
    """Work items of a project; epics are containers, so they never count as work."""
    stmt = (select(Issue).options(selectinload(Issue.status))
            .where(Issue.project_id == project.id, Issue.type != "epic"))
    if not include_subtasks:
        stmt = stmt.where(Issue.type != "subtask")
    return list(db.scalars(stmt))


def _completed_status(status: Status) -> bool:
    return workflow.is_completed(status)


class StatusHistory:
    """Each issue's status over time, replayed from its transition log by status id.

    Rows written before ids were logged are matched by name; a name that no longer exists
    (the status was renamed since) keeps the issue in its previous status instead of
    dropping it from the replay.
    """

    def __init__(self, db: Session, project: Project, issues: list[Issue]):
        self.statuses = {s.id: s for s in db.scalars(
            select(Status).where(Status.project_id == project.id).order_by(Status.sort_order))}
        by_name = {s.name: s.id for s in self.statuses.values()}
        self.initial: dict[int, int] = {}
        self.moves: dict[int, list[tuple[datetime, int | None, int | None]]] = defaultdict(list)
        ids = [i.id for i in issues]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for row in db.scalars(
                select(ActivityLog)
                .where(ActivityLog.issue_id.in_(chunk),
                       (ActivityLog.field == "status_id") | (ActivityLog.action == "created"))
                .order_by(ActivityLog.created_at, ActivityLog.id)
            ):
                if row.action == "created":
                    status_id = (row.meta or {}).get("status_id")
                    if status_id in self.statuses:
                        self.initial[row.issue_id] = status_id
                    continue
                old = row.old_ref if row.old_ref in self.statuses else by_name.get(row.old_value)
                new = row.new_ref if row.new_ref in self.statuses else by_name.get(row.new_value)
                self.moves[row.issue_id].append((_aware(row.created_at), old, new))

    def first_status(self, issue: Issue) -> int:
        if issue.id in self.initial:
            return self.initial[issue.id]
        moves = self.moves.get(issue.id)
        if moves and moves[0][1] is not None:
            return moves[0][1]
        return issue.status_id

    def status_at(self, issue: Issue, cutoff: datetime) -> Status | None:
        if _aware(issue.created_at) > cutoff:
            return None
        current = self.first_status(issue)
        for when, _old, new in self.moves.get(issue.id, ()):
            if when > cutoff:
                break
            if new is not None:
                current = new
        return self.statuses.get(current)

    def finishes(self, issue: Issue) -> list[tuple[datetime, bool]]:
        """Every entry into a done status as (time, completed); False means rejected."""
        events = []
        first = self.statuses.get(self.first_status(issue))
        if first is not None and first.category == "done":
            events.append((_aware(issue.created_at), _completed_status(first)))
        for when, _old, new in self.moves.get(issue.id, ()):
            status = self.statuses.get(new) if new is not None else None
            if status is not None and status.category == "done":
                events.append((when, _completed_status(status)))
        if not events and issue.status.category == "done":
            events.append((_aware(issue.resolved_at or issue.updated_at),
                           _completed_status(issue.status)))
        return events

    def finished_between(self, issues: list[Issue], lo: datetime,
                         hi: datetime) -> tuple[set[int], set[int]]:
        """Issues completed / rejected within [lo, hi]; an issue completed in it counts once."""
        completed: set[int] = set()
        rejected: set[int] = set()
        for issue in issues:
            for when, done in self.finishes(issue):
                if lo <= when <= hi:
                    (completed if done else rejected).add(issue.id)
        return completed, rejected - completed

    def entered(self, issue: Issue, status_ids: set[int], lo: datetime,
                hi: datetime) -> int:
        return sum(1 for when, _old, new in self.moves.get(issue.id, ())
                   if new in status_ids and lo <= when <= hi)

    def segments(self, issue: Issue, now: datetime) -> list[tuple[int, datetime, datetime]]:
        """Consecutive (status id, entered, left) stays; the last one is still running."""
        start = _aware(issue.created_at)
        current = self.first_status(issue)
        stays = []
        for when, _old, new in self.moves.get(issue.id, ()):
            if new is None:
                continue
            when = max(when, start)
            stays.append((current, start, when))
            start, current = when, new
        stays.append((current, start, max(now, start)))
        return stays

    def started_at(self, issue: Issue, now: datetime) -> datetime | None:
        """First time the issue entered an in-progress status."""
        for status_id, entered, _left in self.segments(issue, now):
            status = self.statuses.get(status_id)
            if status is not None and status.category == "doing":
                return entered
        return None

    def completed_at(self, issue: Issue) -> datetime | None:
        """When an issue that is completed now got there (the last completion)."""
        if not _completed_status(issue.status):
            return None
        done = [when for when, completed in self.finishes(issue) if completed]
        return done[-1] if done else None

    def has_history(self, issue: Issue) -> bool:
        return bool(self.moves.get(issue.id))


def _week_buckets(days: int) -> list[tuple[date, date]]:
    """Consecutive 7-day buckets ending today, oldest first, covering the requested window."""
    today = date.today()
    count = max(2, math.ceil(days / 7))
    return [(today - timedelta(days=7 * k + 6), today - timedelta(days=7 * k))
            for k in range(count - 1, -1, -1)]


def _test_results(db: Session, project: Project, since: datetime,
                  issue_ids: set[int]) -> list[ActivityLog]:
    rows = db.scalars(
        select(ActivityLog).join(Issue, Issue.id == ActivityLog.issue_id)
        .where(Issue.project_id == project.id, ActivityLog.field == "test_passed",
               ActivityLog.created_at >= since)
    )
    return [r for r in rows if r.issue_id in issue_ids and r.new_value in ("是", "否")]


def _load(db: Session, ids: set[int]) -> dict[int, Issue]:
    if not ids:
        return {}
    return {i.id: i for i in db.scalars(issue_query().where(Issue.id.in_(ids)))}


def _blocked_ids(db: Session, open_ids: set[int]) -> set[int]:
    if not open_ids:
        return set()
    return set(db.scalars(
        select(IssueLink.target_id).where(IssueLink.link_type == "blocks",
                                          IssueLink.target_id.in_(open_ids),
                                          IssueLink.source_id.in_(open_ids))
    ))


def _score(open_count: int, risks: dict[str, list[Issue]], created: int,
           completed: int) -> tuple[int, str, list[dict[str, Any]]]:
    """Start from 100 and deduct capped penalties, so one kind of risk cannot dominate."""
    return _score_counts(open_count, {key: len(items) for key, items in risks.items()},
                         created, completed)


def _score_counts(open_count: int, n: dict[str, int], created: int,
                  completed: int) -> tuple[int, str, list[dict[str, Any]]]:
    n = dict(n)
    n["net_flow"] = created - completed
    penalties = {
        "overdue": min(30, 6 * n["overdue"]),
        "blocked": min(16, 4 * n["blocked"]),
        "stale": min(15, 3 * n["stale"]),
        "unassigned": min(15, 3 * n["unassigned"]),
        "no_due": 10 if open_count and n["no_due"] * 2 > open_count else 0,
        "net_flow": min(15, created - completed) if created > completed else 0,
    }
    score = max(0, 100 - sum(penalties.values()))
    level = "good" if score >= 80 else "attention" if score >= 60 else "risk"
    signals = sorted(
        ({"key": key, "penalty": value, "count": n[key]}
         for key, value in penalties.items() if value),
        key=lambda s: -s["penalty"],
    )
    return score, level, signals


def dashboard_health(
    db: Session,
    projects: list[Project],
) -> list[dict[str, Any]]:
    """Batch the dashboard cards without replaying every project's full ORM history."""
    if not projects:
        return []
    project_ids = [project.id for project in projects]
    today = date.today()
    now = datetime.now(timezone.utc)
    soon = today + timedelta(days=DUE_SOON_DAYS)
    stale_before = now - timedelta(days=STALE_DAYS)
    since = now - timedelta(days=FLOW_DAYS)
    open_row = Status.category != "done"

    def count_if(condition):
        return func.sum(case((condition, 1), else_=0))

    aggregates = {
        row.project_id: row
        for row in db.execute(
            select(
                Issue.project_id,
                func.count(Issue.id).label("total"),
                count_if(open_row).label("open"),
                count_if(open_row & (Status.category == "doing")).label("doing"),
                count_if(open_row & (Issue.due_date < today)).label("overdue"),
                count_if(open_row & Issue.due_date.between(today, soon)).label("due_soon"),
                count_if(open_row & (Issue.updated_at < stale_before)).label("stale"),
                count_if(open_row & Issue.assignee_id.is_(None)).label("unassigned"),
                count_if(open_row & Issue.due_date.is_(None)).label("no_due"),
                count_if(Issue.created_at >= since).label("created"),
                count_if(
                    (Status.category == "done")
                    & (Status.name != workflow.REJECTED)
                    & (func.coalesce(Issue.resolved_at, Issue.updated_at) >= since)
                ).label("completed"),
                count_if(
                    (Status.name == workflow.REJECTED) & (Issue.updated_at >= since)
                ).label("rejected"),
            )
            .join(Status, Status.id == Issue.status_id)
            .where(Issue.project_id.in_(project_ids), Issue.type != "epic")
            .group_by(Issue.project_id)
        )
    }
    source_issue = aliased(Issue)
    source_status = aliased(Status)
    blocked = dict(
        db.execute(
            select(Issue.project_id, func.count(Issue.id.distinct()))
            .join(Status, Status.id == Issue.status_id)
            .join(IssueLink, IssueLink.target_id == Issue.id)
            .join(source_issue, source_issue.id == IssueLink.source_id)
            .join(source_status, source_status.id == source_issue.status_id)
            .where(
                Issue.project_id.in_(project_ids),
                Issue.type != "epic",
                Status.category != "done",
                source_status.category != "done",
                IssueLink.link_type == "blocks",
            )
            .group_by(Issue.project_id)
        ).all()
    )
    output = []
    for project in projects:
        row = aggregates.get(project.id)
        if row is None or not row.total:
            continue
        risk_counts = {
            "overdue": int(row.overdue or 0),
            "due_soon": int(row.due_soon or 0),
            "blocked": int(blocked.get(project.id, 0)),
            "stale": int(row.stale or 0),
            "unassigned": int(row.unassigned or 0),
            "no_due": int(row.no_due or 0),
        }
        created = int(row.created or 0)
        completed = int(row.completed or 0)
        scored = dict(risk_counts)
        if permissions.is_ongoing(project):
            scored["no_due"] = 0
        score, level, signals = _score_counts(int(row.open or 0), scored,
                                              created, completed)
        output.append({
            "key": project.key,
            "name": project.name,
            "color": project.color,
            "score": score,
            "level": level,
            "signals": signals,
            "counts": {
                "total": int(row.total),
                "open": int(row.open or 0),
                "doing": int(row.doing or 0),
                **risk_counts,
            },
            "flow": {
                "days": FLOW_DAYS,
                "created": created,
                "completed": completed,
                "rejected": int(row.rejected or 0),
            },
        })
    return output


def health_core(
    db: Session, project: Project, include_subtasks: bool = True
) -> tuple[dict[str, Any], dict[str, list[Issue]], list[Issue], StatusHistory]:
    issues = _project_issues(db, project, include_subtasks)
    today = date.today()
    now = datetime.now(timezone.utc)
    open_issues = [i for i in issues if i.status.category != "done"]
    blocked = _blocked_ids(db, {i.id for i in open_issues})
    soon = today + timedelta(days=DUE_SOON_DAYS)
    stale_before = now - timedelta(days=STALE_DAYS)
    risks = {
        "overdue": [i for i in open_issues if i.due_date and i.due_date < today],
        "due_soon": [i for i in open_issues if i.due_date and today <= i.due_date <= soon],
        "blocked": [i for i in open_issues if i.id in blocked],
        "stale": [i for i in open_issues if _aware(i.updated_at) < stale_before],
        "unassigned": [i for i in open_issues if i.assignee_id is None],
        "no_due": [i for i in open_issues if i.due_date is None],
    }
    since = now - timedelta(days=FLOW_DAYS)
    history = StatusHistory(db, project, issues)
    fresh = [i for i in issues if _aware(i.created_at) >= since]
    created = len(fresh)
    completed_ids, rejected_ids = history.finished_between(issues, since, now)
    completed = len(completed_ids)
    risks["net_flow"] = ([i for i in fresh if i.id not in completed_ids
                          and i.status.category != "done"] if created > completed else [])
    scored = dict(risks)
    if permissions.is_ongoing(project):
        scored["no_due"] = []
    score, level, signals = _score(len(open_issues), scored, created, completed)
    core = {
        "score": score,
        "level": level,
        "signals": signals,
        "counts": {
            "total": len(issues),
            "open": len(open_issues),
            "doing": sum(1 for i in open_issues if i.status.category == "doing"),
            **{key: len(items) for key, items in risks.items() if key != "net_flow"},
        },
        "flow": {"days": FLOW_DAYS, "created": created, "completed": completed,
                 "rejected": len(rejected_ids)},
    }
    return core, risks, issues, history


def health(db: Session, project: Project, days: int = 30,
           include_subtasks: bool = True) -> dict[str, Any]:
    core, risks, issues, history = health_core(db, project, include_subtasks)
    throughput = []
    for start, end in _week_buckets(days):
        lo, hi = _start_of(start), _end_of(end)
        completed, rejected = history.finished_between(issues, lo, hi)
        throughput.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "created": sum(1 for i in issues if lo <= _aware(i.created_at) <= hi),
            "completed": len(completed),
            "rejected": len(rejected),
            "open": sum(1 for i in issues
                        if (s := history.status_at(i, hi)) is not None and s.category != "done"),
        })
    order = {
        "overdue": lambda i: i.due_date,
        "due_soon": lambda i: i.due_date,
        "stale": lambda i: _aware(i.updated_at),
        "net_flow": lambda i: -_aware(i.created_at).timestamp(),
    }
    picked: dict[str, list[int]] = {}
    for key in RISK_LISTS:
        items = sorted(risks[key], key=order.get(key, lambda i: i.number))
        picked[key] = [i.id for i in items[:RISK_LIST_SIZE]]
    wanted = {i for ids in picked.values() for i in ids}
    loaded = _load(db, wanted)
    return {
        **core,
        "throughput": throughput,
        "lists": {key: [issue_brief(loaded[i]) for i in ids if i in loaded]
                  for key, ids in picked.items()},
    }


def quality(db: Session, project: Project, days: int = 30,
            include_subtasks: bool = True) -> dict[str, Any]:
    settings = project.settings or {}
    test_status = settings.get("test_status", "测试中")
    issues = _project_issues(db, project, include_subtasks)
    ids = {i.id for i in issues}
    open_issues = [i for i in issues if i.status.category != "done"]
    in_test = [i for i in open_issues if i.status.name == test_status]
    failing = [i for i in open_issues if workflow.is_fail_status(project, i.status)]
    buckets = _week_buckets(days)
    since = _start_of(buckets[0][0])
    results = _test_results(db, project, since, ids)
    history = StatusHistory(db, project, issues)
    reopen_ids = {sid for sid, s in history.statuses.items() if s.name == workflow.REOPENED}

    weekly = []
    for start, end in buckets:
        lo, hi = _start_of(start), _end_of(end)
        week = [r for r in results if lo <= _aware(r.created_at) <= hi]
        weekly.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "passed": sum(1 for r in week if r.new_value == "是"),
            "failed": sum(1 for r in week if r.new_value == "否"),
            "reopened": sum(history.entered(i, reopen_ids, lo, hi) for i in issues),
        })
    passed = sum(w["passed"] for w in weekly)
    failed = sum(w["failed"] for w in weekly)
    failed_ids = {r.issue_id for r in results if r.new_value == "否"}
    shown = [i.id for i in sorted(failing + in_test, key=lambda i: i.number)][:QUEUE_SIZE]
    loaded = _load(db, set(shown))
    return {
        "days": (date.today() - buckets[0][0]).days + 1,
        "test_status": test_status,
        "in_test": len(in_test),
        "failing": len(failing),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed * 100 / (passed + failed)) if passed + failed else None,
        "failed_issues": len(failed_ids),
        "reopened": sum(w["reopened"] for w in weekly),
        "weekly": weekly,
        "queue": [issue_brief(loaded[i]) for i in shown if i in loaded],
    }


def burndown(db: Session, sprint: Sprint) -> dict[str, Any]:
    issues = list(db.scalars(issue_query().where(Issue.sprint_id == sprint.id)))
    keys = set((sprint.stats or {}).get("committed_keys") or []) | set(
        (sprint.stats or {}).get("completed_keys") or []
    )
    if keys:
        extra = db.scalars(issue_query().where(Issue.key.in_(keys)))
        known = {i.id for i in issues}
        issues += [i for i in extra if i.id not in known]
    project = db.get(Project, sprint.project_id)
    assert project is not None
    history = StatusHistory(db, project, issues)
    start = sprint.start_date or (_aware(sprint.started_at).date() if sprint.started_at
                                  else date.today())
    end = sprint.end_date or (start + timedelta(days=13))
    days = max((end - start).days, 1)
    total_points = sum(i.story_points or 0 for i in issues)
    total_count = len(issues)
    series = []
    last_day = min(end, date.today())
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        ideal_count = total_count * (1 - offset / days)
        ideal_points = total_points * (1 - offset / days)
        point: dict[str, Any] = {
            "date": day.isoformat(),
            "ideal_count": round(ideal_count, 2),
            "ideal_points": round(ideal_points, 2),
        }
        if day <= last_day:
            cutoff = _end_of(day)
            remaining = [
                i for i in issues
                if (s := history.status_at(i, cutoff)) is None or not workflow.is_completed(s)
            ]
            point["remaining_count"] = len(remaining)
            point["remaining_points"] = sum(i.story_points or 0 for i in remaining)
        series.append(point)
    return {
        "sprint": {"id": sprint.id, "name": sprint.name, "state": sprint.state},
        "total_count": total_count,
        "total_points": total_points,
        "series": series,
    }


def cumulative_flow(db: Session, project: Project, days: int = 30,
                    include_subtasks: bool = True) -> dict[str, Any]:
    issues = _project_issues(db, project, include_subtasks)
    history = StatusHistory(db, project, issues)
    statuses = list(history.statuses.values())
    today = date.today()
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        cutoff = _end_of(day)
        counts: Counter[int] = Counter()
        for issue in issues:
            status = history.status_at(issue, cutoff)
            if status is not None:
                counts[status.id] += 1
        series.append({"date": day.isoformat(), **{s.name: counts.get(s.id, 0)
                                                    for s in statuses}})
    return {
        "statuses": [{"id": s.id, "name": s.name, "color": s.color} for s in statuses],
        "series": series,
    }


def _days(delta: timedelta) -> float:
    return round(delta.total_seconds() / 86400, 1)


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * share
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)


def _spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p85": None, "avg": None}
    return {"count": len(values), "median": _percentile(values, 0.5),
            "p85": _percentile(values, 0.85), "avg": round(sum(values) / len(values), 1)}


def aging_level(days: float) -> str | None:
    if days >= AGING_DANGER_DAYS:
        return "danger"
    if days >= AGING_WARN_DAYS:
        return "warn"
    return None


def flow(db: Session, project: Project, days: int = 30,
         include_subtasks: bool = True) -> dict[str, Any]:
    """Cycle / lead time of work completed in the window, time per status and aging WIP.

    Lead time runs from creation to the last completion; cycle time from the first entry into
    an in-progress status. Work that skipped every in-progress status has no cycle time.
    """
    issues = _project_issues(db, project, include_subtasks)
    history = StatusHistory(db, project, issues)
    now = datetime.now(timezone.utc)
    since = _start_of(date.today() - timedelta(days=days - 1))

    done = []
    for issue in issues:
        finished = history.completed_at(issue)
        if finished is None or finished < since:
            continue
        started = history.started_at(issue, now)
        done.append({
            "key": issue.key, "title": issue.title, "type": issue.type,
            "completed": finished.date().isoformat(),
            "lead": _days(finished - _aware(issue.created_at)),
            "cycle": _days(finished - started) if started and started <= finished else None,
        })
    done.sort(key=lambda p: p["completed"])

    stays: dict[int, list[float]] = defaultdict(list)
    current: dict[int, list[float]] = defaultdict(list)
    open_issues = [i for i in issues if i.status.category != "done"]
    for issue in issues:
        segments = history.segments(issue, now)
        for status_id, entered, left in segments[:-1]:
            if left >= since:
                stays[status_id].append(_days(left - entered))
        if issue.status.category != "done":
            status_id, entered, _ = segments[-1]
            current[status_id].append(_days(now - entered))
    per_status = []
    for status in history.statuses.values():
        if status.category == "done":
            continue
        spread = _spread(stays.get(status.id, []))
        here = current.get(status.id, [])
        per_status.append({
            "id": status.id, "name": status.name, "color": status.color,
            "category": status.category, **spread, "current": len(here),
            "current_avg": round(sum(here) / len(here), 1) if here else None,
        })

    aging = []
    for issue in open_issues:
        segments = history.segments(issue, now)
        started = history.started_at(issue, now)
        in_status = _days(now - segments[-1][1])
        aging.append((issue.id, {
            "days_in_status": in_status,
            "days_in_progress": _days(now - started) if started else None,
            "age": _days(now - _aware(issue.created_at)),
            "level": aging_level(in_status),
        }))
    aging.sort(key=lambda row: -row[1]["days_in_status"])
    aging = aging[:AGING_LIST_SIZE]
    loaded = _load(db, {issue_id for issue_id, _ in aging})
    with_history = sum(1 for i in issues if history.has_history(i))
    return {
        "days": days,
        "completed": done,
        "cycle": _spread([p["cycle"] for p in done if p["cycle"] is not None]),
        "lead": _spread([p["lead"] for p in done]),
        "statuses": per_status,
        "aging": [{**issue_brief(loaded[issue_id]), **extra}
                  for issue_id, extra in aging if issue_id in loaded],
        "thresholds": {"warn": AGING_WARN_DAYS, "danger": AGING_DANGER_DAYS},
        "coverage": {"with_history": with_history, "total": len(issues)},
    }


def load(db: Session, project: Project, include_subtasks: bool = True) -> dict[str, Any]:
    """Open work per assignee and per assignee's department; current load only, no output."""
    issues = [i for i in _project_issues(db, project, include_subtasks)
              if i.status.category != "done"]
    history = StatusHistory(db, project, issues)
    now = datetime.now(timezone.utc)
    today = date.today()
    loaded = _load(db, {i.id for i in issues})

    def counts(items: list[Issue]) -> dict[str, Any]:
        return {
            "open": len(items),
            "todo": sum(1 for i in items if i.status.category == "todo"),
            "doing": sum(1 for i in items if i.status.category == "doing"),
            "overdue": sum(1 for i in items if i.due_date and i.due_date < today),
            "aging": sum(1 for i in items
                         if _days(now - history.segments(i, now)[-1][1]) >= AGING_DANGER_DAYS),
            "points": sum(i.story_points or 0 for i in items),
        }

    def ordered(items: list[Issue]) -> list[dict[str, Any]]:
        items = sorted(items, key=lambda i: (i.due_date or date.max, i.number))
        return [issue_brief(loaded[i.id]) for i in items if i.id in loaded]

    by_person: dict[int, list[Issue]] = defaultdict(list)
    unassigned: list[Issue] = []
    for issue in issues:
        (by_person[issue.assignee_id] if issue.assignee_id else unassigned).append(issue)
    users = {u.id: u for u in db.scalars(
        select(User).options(selectinload(User.org_unit)).where(User.id.in_(list(by_person))))}
    people = []
    by_unit: dict[int | None, list[int]] = defaultdict(list)
    for user_id, items in by_person.items():
        user = users.get(user_id)
        if user is None:
            continue
        by_unit[user.org_unit_id].append(user_id)
        people.append({"user": user_brief(user),
                       "unit": user.org_unit.label if user.org_unit else None,
                       **counts(items), "issues": ordered(items)})
    people.sort(key=lambda p: (-p["open"], -p["doing"], p["user"]["display_name"]))
    units = []
    for unit_id, member_ids in by_unit.items():
        unit = users[member_ids[0]].org_unit
        items = [i for uid in member_ids for i in by_person[uid]]
        units.append({"id": unit_id, "label": unit.label if unit else None,
                      "path": unit.path if unit else None, "people": len(member_ids),
                      **counts(items)})
    units.sort(key=lambda u: (-u["open"], u["label"] or ""))
    return {
        "people": people,
        "units": units,
        "unassigned": {**counts(unassigned), "issues": ordered(unassigned)},
        "thresholds": {"warn": AGING_WARN_DAYS, "danger": AGING_DANGER_DAYS},
    }


def status_since(db: Session, issues: list[Issue]) -> dict[int, datetime]:
    """When each issue entered its current status: its last status change, else creation."""
    since = {i.id: _aware(i.created_at) for i in issues}
    ids = list(since)
    for start in range(0, len(ids), 500):
        rows = db.execute(
            select(ActivityLog.issue_id, func.max(ActivityLog.created_at))
            .where(ActivityLog.issue_id.in_(ids[start:start + 500]),
                   ActivityLog.field == "status_id")
            .group_by(ActivityLog.issue_id)
        )
        for issue_id, changed in rows:
            since[issue_id] = max(since[issue_id], _aware(changed))
    return since


def completion_curve(
    db: Session, project: Project, days: int = 90, *, include_subtasks: bool = True,
) -> dict[str, Any]:
    """Time (X) vs completion % (Y): actual cumulative done vs linear plan baseline."""
    today = date.today()
    start_day = today - timedelta(days=max(7, days) - 1)
    issues = _project_issues(db, project, include_subtasks)
    # Scope: issues created on/before today (exclude future-dated noise).
    scope = [i for i in issues if _aware(i.created_at).date() <= today]
    total = len(scope)
    history = StatusHistory(db, project, scope)

    # Ideal line: project birth → max(due, sprint end, today+7) linear 0→100.
    birth = min((_aware(i.created_at).date() for i in scope), default=start_day)
    due_candidates = [i.due_date for i in scope if i.due_date]
    plan_end = max(due_candidates) if due_candidates else today + timedelta(days=14)
    if plan_end <= birth:
        plan_end = birth + timedelta(days=14)
    span = max(1, (plan_end - birth).days)

    points: list[dict[str, Any]] = []
    day = start_day
    while day <= today:
        cutoff = _end_of(day)
        done = 0
        for issue in scope:
            if _aware(issue.created_at).date() > day:
                continue
            status = history.status_at(issue, cutoff)
            if (
                status is not None
                and status.category == "done"
                and status.name != workflow.REJECTED
            ):
                done += 1
        # Denominator: work that existed by this day (created on/before day).
        denom = sum(1 for i in scope if _aware(i.created_at).date() <= day) or 1
        actual_pct = round(100.0 * done / denom, 1)
        elapsed = max(0, (day - birth).days)
        plan_pct = round(min(100.0, 100.0 * elapsed / span), 1)
        points.append({
            "date": day.isoformat(),
            "actual_pct": actual_pct,
            "plan_pct": plan_pct,
            "done": done,
            "total": denom,
        })
        day += timedelta(days=1)

    latest = points[-1] if points else {"actual_pct": 0, "plan_pct": 0}
    return {
        "days": len(points),
        "total_issues": total,
        "birth": birth.isoformat(),
        "plan_end": plan_end.isoformat(),
        "actual_pct": latest.get("actual_pct", 0),
        "plan_pct": latest.get("plan_pct", 0),
        "points": points,
    }


def portfolio_overview(
    db: Session,
    user: User,
    scope: str | None = None,
) -> dict[str, Any]:
    """Hardware-PM control tower over the caller's authorized project scope."""
    from . import schedule_cascade

    requested_scope = scope or ("all" if user.is_admin else "lead")
    if requested_scope not in {"all", "lead", "managed"}:
        requested_scope = "all" if user.is_admin else "lead"
    projects = schedule_cascade.managed_projects(db, user)
    if requested_scope == "lead":
        projects = [project for project in projects if project.lead_id == user.id]
    projects = [project for project in projects if permissions.is_delivery(project)]
    health_rows = {row["key"]: row for row in dashboard_health(db, projects)}
    cards: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    totals = {
        "projects": len(projects),
        "open": 0,
        "overdue": 0,
        "blocked": 0,
        "cascade": 0,
        "due_slips": 0,
        "sla_warning": 0,
        "sla_breached": 0,
        "target_at_risk": 0,
        "new_risks": 0,
        "worsening_risks": 0,
        "decisions": 0,
    }
    since = datetime.now(timezone.utc) - timedelta(days=schedule_cascade.SLIP_LOOKBACK_DAYS)
    for project in projects:
        cascade = schedule_cascade.suggest_cascade(db, project)
        events = schedule_cascade.sync_risk_events(db, project, cascade)
        risk_counts = Counter(event.status for event in events)
        health = health_rows.get(project.key, {})
        counts = health.get("counts") or {}
        open_n = int(counts.get("open", 0))
        overdue_n = int(counts.get("overdue", 0))
        blocked_n = int(counts.get("blocked", 0))
        cascade_n = int(cascade["suggestion_count"])
        slips = sum(1 for s in cascade["delay_sources"] if s.get("slipped"))
        # SLA aggregates on open issues (lightweight).
        sla_rows = db.execute(
            select(Issue.sla_state, func.count())
            .join(Status, Status.id == Issue.status_id)
            .where(
                Issue.project_id == project.id,
                Issue.type != "epic",
                Status.category != "done",
                Issue.sla_state.in_(("warning", "breached")),
            )
            .group_by(Issue.sla_state)
        ).all()
        sla = {state: int(n) for state, n in sla_rows}
        totals["open"] += open_n
        totals["overdue"] += overdue_n
        totals["blocked"] += blocked_n
        totals["cascade"] += cascade_n
        totals["due_slips"] += slips
        totals["sla_warning"] += sla.get("warning", 0)
        totals["sla_breached"] += sla.get("breached", 0)
        target_at_risk = int(cascade["target_impact_days"] > 0)
        decisions = sum(
            risk_counts.get(status, 0) for status in ("new", "worsening", "acknowledged")
        )
        totals["target_at_risk"] += target_at_risk
        totals["new_risks"] += risk_counts.get("new", 0)
        totals["worsening_risks"] += risk_counts.get("worsening", 0)
        totals["decisions"] += decisions
        curve = completion_curve(db, project, days=90)
        today = date.today()
        actual_pct = float(curve["actual_pct"])
        plan_pct = float(curve["plan_pct"])
        delta_pct = round(actual_pct - plan_pct, 1)
        if cascade["target_impact_days"] > 0:
            pace = "behind"
        elif delta_pct >= 3:
            pace = "ahead"
        elif delta_pct <= -3:
            pace = "behind"
        else:
            pace = "on_track"
        done_n = int(curve["points"][-1]["done"]) if curve["points"] else 0
        total_n = int(curve.get("total_issues") or 0)
        goal_blurb = (project.description or "").strip()
        if goal_blurb:
            goal_blurb = goal_blurb.splitlines()[0].strip()[:180]
        target_end = cascade["target_date"] or curve["plan_end"]
        birth_value = (
            project.plan_start_date.isoformat()
            if project.plan_start_date
            else curve["birth"]
        )
        risk_rank = min(
            100,
            int(cascade["target_impact_days"]) * 8
            + risk_counts.get("worsening", 0) * 20
            + risk_counts.get("new", 0) * 10
            + overdue_n * 5
            + (20 if cascade["data_quality"]["cycles"] else 0),
        )
        cards.append({
            "id": project.id,
            "key": project.key,
            "name": project.name,
            "color": project.color,
            "icon": project.icon,
            "cover_url": (
                f"/api/projects/{project.key}/cover?v={project.cover_key}"
                if project.cover_key else None
            ),
            "health": {
                "score": health.get("score"),
                "level": health.get("level") or "good",
                "counts": counts,
            },
            "cascade_count": cascade_n,
            "due_slips": slips,
            "sla_warning": sla.get("warning", 0),
            "sla_breached": sla.get("breached", 0),
            "risk_rank": risk_rank,
            "target": {
                "committed_date": cascade["target_date"],
                "forecast_date": cascade["forecast_target_date"],
                "impact_days": cascade["target_impact_days"],
                "confidence": cascade["confidence"],
                "status": (
                    "at_risk" if cascade["target_impact_days"] > 0
                    else "no_baseline" if cascade["baseline_mode"] == "shadow"
                    else "on_track"
                ),
                "baseline_version": cascade["baseline_version"],
            },
            "risk_events": {
                "new": risk_counts.get("new", 0),
                "worsening": risk_counts.get("worsening", 0),
                "snoozed": risk_counts.get("snoozed", 0),
                "decisions": decisions,
            },
            "risk_items": [
                {
                    **(event.payload or {}),
                    "id": event.id,
                    "status": event.status,
                    "severity": event.severity,
                    "first_seen_at": event.first_seen_at.isoformat(),
                    "last_seen_at": event.last_seen_at.isoformat(),
                }
                for event in events
                if event.status in {"new", "worsening", "acknowledged"}
            ],
            "impacted_gates": cascade["impacted_gates"],
            "data_quality": cascade["data_quality"],
            "delay_sources": cascade["delay_sources"][:5],
            "goal": {
                "title": project.name,
                "blurb": goal_blurb,
                "birth": birth_value,
                "target_end": target_end,
                "days_remaining": (date.fromisoformat(target_end) - today).days,
                "actual_pct": actual_pct,
                "plan_pct": plan_pct,
                "delta_pct": delta_pct,
                "pace": pace,
                "done": done_n,
                "total": total_n,
                "open": open_n,
            },
            "completion": {
                "actual_pct": actual_pct,
                "plan_pct": plan_pct,
                "birth": curve["birth"],
                "plan_end": curve["plan_end"],
                "spark": [
                    {"date": p["date"], "actual_pct": p["actual_pct"], "plan_pct": p["plan_pct"]}
                    for p in curve["points"][:: max(1, len(curve["points"]) // 24)]
                ],
                "points": [
                    {"date": p["date"], "actual_pct": p["actual_pct"], "plan_pct": p["plan_pct"]}
                    for p in curve["points"]
                ],
            },
        })
        for sug in cascade["suggestions"][:5]:
            actions.append({
                "project_key": project.key,
                "project_name": project.name,
                **sug,
            })
    # Prefer deeper / overdue-driven actions first.
    actions.sort(
        key=lambda action: (
            -int(action.get("urgency_score") or 0),
            -int(action.get("target_impact_days") or 0),
            action.get("project_key"),
            action.get("key"),
        )
    )
    cards.sort(key=lambda card: (-card["risk_rank"], card["key"]))
    recent_activity = portfolio_recent_activity(db, projects, user)
    last_by_project = _last_activity_by_project(recent_activity)
    for card in cards:
        card["last_activity"] = last_by_project.get(card["key"])
    db.commit()
    return {
        "portfolio_access": True,
        "scope": requested_scope,
        "totals": totals,
        "projects": cards,
        "actions": actions[:20],
        "recent_activity": recent_activity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slip_lookback_days": schedule_cascade.SLIP_LOOKBACK_DAYS,
        "activity_since": since.isoformat(),
    }


PORTFOLIO_ACTIVITY_LIMIT = 20
ONGOING_FLOW_DAYS = 14
ONGOING_THROUGHPUT_DAYS = 42
ONGOING_QUEUE_SIZE = 8


def portfolio_recent_activity(
    db: Session,
    projects: list[Project],
    user: User,
    *,
    limit: int = PORTFOLIO_ACTIVITY_LIMIT,
) -> list[dict[str, Any]]:
    """Latest issue activity across projects, newest first, capped per project.

    A plain global Top-N lets one busy project fill the tower feed. Cap each
    project so the panel surfaces activity from across the current scope.
    """
    if not projects or limit <= 0:
        return []
    by_id = {project.id: project for project in projects}
    per_project = max(1, math.ceil(limit / max(len(projects), 1)))
    ranked = (
        select(
            ActivityLog.id.label("activity_id"),
            Issue.project_id.label("project_id"),
            func.row_number().over(
                partition_by=Issue.project_id,
                order_by=ActivityLog.id.desc(),
            ).label("rn"),
        )
        .join(Issue, Issue.id == ActivityLog.issue_id)
        .where(Issue.project_id.in_(by_id))
    )
    visible = permissions.visible_issue_clause(db, user)
    if visible is not None:
        ranked = ranked.where(visible)
    ranked_subq = ranked.subquery()
    selected_ids = [
        row.activity_id
        for row in db.execute(
            select(ranked_subq.c.activity_id)
            .where(ranked_subq.c.rn <= per_project)
            .order_by(ranked_subq.c.activity_id.desc())
            .limit(limit)
        )
    ]
    if not selected_ids:
        return []
    rows = list(
        db.scalars(
            select(ActivityLog)
            .options(selectinload(ActivityLog.actor))
            .where(ActivityLog.id.in_(selected_ids))
            .order_by(ActivityLog.id.desc())
        )
    )
    issues = {
        issue.id: issue
        for issue in db.scalars(
            select(Issue).where(Issue.id.in_({row.issue_id for row in rows}))
        )
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        issue = issues.get(row.issue_id)
        if issue is None:
            continue
        project = by_id.get(issue.project_id)
        if project is None:
            continue
        items.append({
            "id": row.id,
            "actor": user_brief(row.actor),
            "action": row.action,
            "field": row.field,
            "old_value": row.old_value,
            "new_value": row.new_value,
            "created_at": iso(row.created_at),
            "project_key": project.key,
            "project_name": project.name,
            "project_color": project.color,
            "issue": {
                "key": issue.key,
                "title": issue.title,
                "type": issue.type,
            },
        })
    return items


def _last_activity_by_project(
    items: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.get("project_key")
        if isinstance(key, str) and key not in latest:
            latest[key] = item
    return latest


def _ongoing_queue_item(issue: Issue) -> dict[str, Any]:
    status = issue.status
    return {
        "id": issue.id,
        "key": issue.key,
        "title": issue.title,
        "priority": issue.priority,
        "due_date": issue.due_date.isoformat() if issue.due_date else None,
        "sla_state": issue.sla_state,
        "status": {
            "id": status.id,
            "name": status.name,
            "category": status.category,
            "color": status.color,
        },
        "assignee": user_brief(issue.assignee),
        "updated_at": _aware(issue.updated_at).isoformat() if issue.updated_at else None,
    }


def _pick_queue(issues: list[Issue], limit: int = ONGOING_QUEUE_SIZE) -> list[dict[str, Any]]:
    return [_ongoing_queue_item(issue) for issue in issues[:limit]]


def ongoing_portfolio_overview(
    db: Session,
    user: User,
    scope: str | None = None,
) -> dict[str, Any]:
    """Ops control tower over ongoing (non-delivery) projects the caller manages."""
    from . import schedule_cascade

    requested_scope = scope or ("all" if user.is_admin else "lead")
    if requested_scope not in {"all", "lead", "managed"}:
        requested_scope = "all" if user.is_admin else "lead"
    projects = [
        project for project in schedule_cascade.managed_projects(db, user)
        if permissions.is_ongoing(project)
    ]
    if requested_scope == "lead":
        projects = [project for project in projects if project.lead_id == user.id]

    now = datetime.now(timezone.utc)
    flow_since = now - timedelta(days=ONGOING_FLOW_DAYS)
    cards: list[dict[str, Any]] = []
    totals = {
        "projects": len(projects),
        "open": 0,
        "doing": 0,
        "overdue": 0,
        "blocked": 0,
        "sla_warning": 0,
        "sla_breached": 0,
        "stale": 0,
        "unassigned": 0,
        "created": 0,
        "completed": 0,
    }

    for project in projects:
        core, risks, issues, history = health_core(db, project, include_subtasks=True)
        open_issues = [issue for issue in issues if issue.status.category != "done"]
        todo_n = sum(1 for issue in open_issues if issue.status.category == "todo")
        doing_n = int(core["counts"].get("doing", 0))
        overdue = risks["overdue"]
        blocked = risks["blocked"]
        stale = risks["stale"]
        unassigned = risks["unassigned"]
        sla_warning = [issue for issue in open_issues if issue.sla_state == "warning"]
        sla_breached = [issue for issue in open_issues if issue.sla_state == "breached"]

        created_ids = {
            issue.id for issue in issues if _aware(issue.created_at) >= flow_since
        }
        completed_ids, _rejected = history.finished_between(issues, flow_since, now)
        created_n = len(created_ids)
        completed_n = len(completed_ids)
        net = created_n - completed_n

        throughput = []
        for start, end in _week_buckets(ONGOING_THROUGHPUT_DAYS):
            lo, hi = _start_of(start), _end_of(end)
            week_done, _ = history.finished_between(issues, lo, hi)
            throughput.append({
                "start": start.isoformat(),
                "end": end.isoformat(),
                "created": sum(
                    1 for issue in issues if lo <= _aware(issue.created_at) <= hi
                ),
                "completed": len(week_done),
            })

        load_counter: Counter[int | None] = Counter(
            issue.assignee_id for issue in open_issues
        )
        people = {
            user_row.id: user_row
            for user_row in db.scalars(
                select(User).where(
                    User.id.in_([uid for uid in load_counter if uid is not None])
                )
            )
        } if any(uid is not None for uid in load_counter) else {}
        assignee_load = []
        for uid, count in load_counter.most_common(5):
            assignee_load.append({
                "user": user_brief(people.get(uid)) if uid is not None else None,
                "open": count,
            })

        # Prefer overdue/SLA issues, then blocked/stale, then unassigned.
        urgent = sorted(
            {issue.id: issue for issue in overdue + sla_breached}.values(),
            key=lambda issue: (
                0 if issue.sla_state == "breached" else 1,
                issue.due_date or date.max,
                issue.number,
            ),
        )
        stuck = sorted(
            {
                issue.id: issue
                for issue in blocked + stale
                if issue.id not in {row.id for row in urgent}
            }.values(),
            key=lambda issue: (_aware(issue.updated_at) or now, issue.number),
        )
        claim = sorted(
            [
                issue for issue in unassigned
                if issue.id not in {row.id for row in urgent}
                and issue.id not in {row.id for row in stuck}
            ],
            key=lambda issue: (issue.due_date or date.max, issue.number),
        )

        open_n = int(core["counts"].get("open", 0))
        overdue_n = len(overdue)
        blocked_n = len(blocked)
        stale_n = len(stale)
        unassigned_n = len(unassigned)
        sla_warning_n = len(sla_warning)
        sla_breached_n = len(sla_breached)
        score = int(core.get("score") or 0)
        pressure_rank = min(
            100,
            overdue_n * 8
            + blocked_n * 6
            + sla_breached_n * 10
            + sla_warning_n * 5
            + stale_n * 3
            + unassigned_n * 2
            + max(0, net) * 4
            + max(0, (100 - score) // 5),
        )

        totals["open"] += open_n
        totals["doing"] += doing_n
        totals["overdue"] += overdue_n
        totals["blocked"] += blocked_n
        totals["sla_warning"] += sla_warning_n
        totals["sla_breached"] += sla_breached_n
        totals["stale"] += stale_n
        totals["unassigned"] += unassigned_n
        totals["created"] += created_n
        totals["completed"] += completed_n

        goal_blurb = (project.description or "").strip()
        if goal_blurb:
            goal_blurb = goal_blurb.splitlines()[0].strip()[:180]

        cards.append({
            "id": project.id,
            "key": project.key,
            "name": project.name,
            "color": project.color,
            "icon": project.icon,
            "cover_url": (
                f"/api/projects/{project.key}/cover?v={project.cover_key}"
                if project.cover_key else None
            ),
            "work_mode": "ongoing",
            "health": {
                "score": core.get("score"),
                "level": core.get("level") or "good",
                "counts": core.get("counts") or {},
            },
            "pressure_rank": pressure_rank,
            "flow": {
                "days": ONGOING_FLOW_DAYS,
                "created": created_n,
                "completed": completed_n,
                "net": net,
            },
            "sla": {
                "warning": sla_warning_n,
                "breached": sla_breached_n,
            },
            "backlog": {
                "overdue": overdue_n,
                "blocked": blocked_n,
                "stale": stale_n,
                "unassigned": unassigned_n,
            },
            "flow_status": {
                "todo": todo_n,
                "doing": doing_n,
                "sla_warning": sla_warning_n,
                "sla_breached": sla_breached_n,
            },
            "throughput": throughput,
            "assignee_load": assignee_load,
            "queues": {
                "urgent": _pick_queue(urgent),
                "stuck": _pick_queue(stuck),
                "unassigned": _pick_queue(claim),
            },
            "goal": {
                "title": project.name,
                "blurb": goal_blurb,
                "open": open_n,
                "doing": doing_n,
                "todo": todo_n,
            },
        })

    cards.sort(key=lambda card: (-card["pressure_rank"], card["key"]))
    recent_activity = portfolio_recent_activity(db, projects, user)
    last_by_project = _last_activity_by_project(recent_activity)
    for card in cards:
        card["last_activity"] = last_by_project.get(card["key"])
    return {
        "portfolio_access": True,
        "scope": requested_scope,
        "totals": totals,
        "projects": cards,
        "recent_activity": recent_activity,
        "generated_at": now.isoformat(),
        "flow_days": ONGOING_FLOW_DAYS,
    }