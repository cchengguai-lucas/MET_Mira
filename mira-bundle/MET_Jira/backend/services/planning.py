"""Sprint trends and forecasts: velocity, scope change, burn-up, past load and Monte Carlo."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActivityLog, Issue, Project, Sprint, User
from ..serialize import user_brief
from .reports import StatusHistory, _aware, _completed_status, _end_of, _project_issues

VELOCITY_SPRINTS = 3
LOAD_SERIES = 6
FORECAST_RUNS = 2000
FORECAST_HORIZON_DAYS = 365
# Completing a sprint moves its open work out in the same request; the sprint's final state is
# read just before that.
COMPLETION_GRACE = timedelta(seconds=5)

Change = tuple[datetime, Any, Any, int | None]
Move = tuple[datetime, Issue, int | None]


class FieldHistory:
    """One field's value over time, replayed back from today's value through its change log.

    Before a change the field held that change's old value, so no record of the initial value
    is needed. Id fields read the logged ids; story points parse the logged text.
    """

    def __init__(self, db: Session, issues: list[Issue], field: str):
        self.field = field
        self.changes: dict[int, list[Change]] = defaultdict(list)
        ids = [i.id for i in issues]
        for start in range(0, len(ids), 500):
            rows = db.execute(
                select(ActivityLog.issue_id, ActivityLog.created_at, ActivityLog.old_ref,
                       ActivityLog.new_ref, ActivityLog.old_value, ActivityLog.new_value,
                       ActivityLog.actor_id)
                .where(ActivityLog.issue_id.in_(ids[start:start + 500]),
                       ActivityLog.field == field)
                .order_by(ActivityLog.created_at, ActivityLog.id)
            )
            for issue_id, when, old_ref, new_ref, old_text, new_text, actor_id in rows:
                self.changes[issue_id].append((_aware(when), self._parse(old_ref, old_text),
                                               self._parse(new_ref, new_text), actor_id))

    def _parse(self, ref: int | None, text: str | None) -> Any:
        if self.field != "story_points":
            return ref
        try:
            return float(text) if text else None
        except ValueError:
            return None

    def value_at(self, issue: Issue, cutoff: datetime) -> Any:
        for when, old, _new, _actor in self.changes.get(issue.id, ()):
            if when > cutoff:
                return old
        return getattr(issue, self.field)

    def between(self, issue: Issue, lo: datetime, hi: datetime) -> list[Change]:
        return [c for c in self.changes.get(issue.id, ()) if lo < c[0] <= hi]


def _issue_ref(issue: Issue) -> dict[str, Any]:
    return {"key": issue.key, "title": issue.title, "type": issue.type}


class SprintHistory:
    """Who was in which sprint, in which status and with how many points, at any time."""

    def __init__(self, db: Session, project: Project, include_subtasks: bool):
        self.db = db
        self.issues = _project_issues(db, project, include_subtasks)
        self.status = StatusHistory(db, project, self.issues)
        self.sprint = FieldHistory(db, self.issues, "sprint_id")
        self.points = FieldHistory(db, self.issues, "story_points")
        self.now = datetime.now(timezone.utc)

    def window(self, sprint: Sprint) -> tuple[datetime, datetime] | None:
        if sprint.started_at is None:
            return None
        start = _aware(sprint.started_at)
        if sprint.completed_at is not None:
            return start, max(start, _aware(sprint.completed_at) - COMPLETION_GRACE)
        return start, self.now

    def members(self, sprint_id: int, cutoff: datetime) -> list[Issue]:
        return [i for i in self.issues if _aware(i.created_at) <= cutoff
                and self.sprint.value_at(i, cutoff) == sprint_id]

    def completed(self, issue: Issue, cutoff: datetime) -> bool:
        status = self.status.status_at(issue, cutoff)
        return status is not None and _completed_status(status)

    def pts(self, issues: list[Issue], cutoff: datetime) -> float:
        return round(sum(self.points.value_at(i, cutoff) or 0 for i in issues), 1)

    def joins(self, sprint_id: int, lo: datetime, hi: datetime) -> list[Move]:
        """(time, issue, actor id) for every move into the sprint after it started."""
        out = []
        for issue in self.issues:
            created = _aware(issue.created_at)
            if lo < created <= hi and self.sprint.value_at(issue, created) == sprint_id:
                out.append((created, issue, issue.reporter_id))
            for when, _old, new, actor in self.sprint.between(issue, lo, hi):
                if new == sprint_id:
                    out.append((when, issue, actor))
        return out

    def leaves(self, sprint_id: int, lo: datetime, hi: datetime) -> list[Move]:
        return [(when, issue, actor) for issue in self.issues
                for when, old, _new, actor in self.sprint.between(issue, lo, hi)
                if old == sprint_id]

    def committed(self, sprint: Sprint, start: datetime) -> list[Issue]:
        """The snapshot taken when the sprint started, else its members at that moment."""
        keys = (sprint.stats or {}).get("committed_keys")
        if keys is None:
            return self.members(sprint.id, start)
        wanted = set(keys)
        return [i for i in self.issues if i.key in wanted]

    def summary(self, sprint: Sprint) -> dict[str, Any] | None:
        window = self.window(sprint)
        if window is None:
            return None
        start, end = window
        committed = self.committed(sprint, start)
        at_end = self.members(sprint.id, end)
        committed_ids = {i.id for i in committed}
        end_ids = {i.id for i in at_end}
        added = list({i.id: i for _, i, _ in self.joins(sprint.id, start, end)
                      if i.id not in committed_ids}.values())
        removed = [i for i in committed + added if i.id not in end_ids]
        done = [i for i in at_end if self.completed(i, end)]
        done_ids = {i.id for i in done}
        reestimated = 0.0
        for issue in self.issues:
            for when, old, new, _actor in self.points.between(issue, start, end):
                if self.sprint.value_at(issue, when) == sprint.id:
                    reestimated += (new or 0) - (old or 0)
        return {
            "id": sprint.id,
            "name": sprint.name,
            "state": sprint.state,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "planned_end": sprint.end_date.isoformat() if sprint.end_date else None,
            "committed": {"count": len(committed), "points": self.pts(committed, start)},
            "added": {"count": len(added), "points": self.pts(added, end)},
            "removed": {"count": len(removed), "points": self.pts(removed, end)},
            "scope": {"count": len(at_end), "points": self.pts(at_end, end)},
            "completed": {"count": len(done), "points": self.pts(done, end)},
            "committed_completed": len(committed_ids & done_ids),
            "reestimated": round(reestimated, 1),
        }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def velocity(db: Session, project: Project, include_subtasks: bool = True) -> dict[str, Any]:
    """Commitment against completion per started sprint, oldest first."""
    history = SprintHistory(db, project, include_subtasks)
    sprints = db.scalars(select(Sprint).where(Sprint.project_id == project.id,
                                              Sprint.started_at.is_not(None))
                         .order_by(Sprint.started_at))
    rows = [s for s in (history.summary(sprint) for sprint in sprints) if s is not None]
    recent = [r for r in rows if r["state"] == "closed"][-VELOCITY_SPRINTS:]
    committed = sum(r["committed"]["count"] for r in recent)
    return {
        "sprints": rows,
        "average": {
            "sprints": len(recent),
            "count": _mean([r["completed"]["count"] for r in recent]),
            "points": _mean([r["completed"]["points"] for r in recent]),
            "commitment_rate": (round(sum(r["committed_completed"] for r in recent) * 100
                                      / committed) if committed else None),
            "added": _mean([r["added"]["count"] for r in recent]),
        },
    }


def sprint_detail(db: Session, project: Project, sprint: Sprint,
                  include_subtasks: bool = True) -> dict[str, Any]:
    """Daily scope and completion (burn-up and burndown), scope changes and re-estimates."""
    history = SprintHistory(db, project, include_subtasks)
    summary = history.summary(sprint)
    base = {"sprint": {"id": sprint.id, "name": sprint.name, "state": sprint.state,
                       "start_date": sprint.start_date, "end_date": sprint.end_date},
            "summary": summary}
    window = history.window(sprint)
    if window is None or summary is None:
        return {**base, "series": [], "changes": [], "reestimates": []}
    start, end = window
    first = start.date()
    planned = sprint.end_date if sprint.end_date and sprint.end_date >= first else None
    last = max(planned or end.date(), end.date())
    span = max(((planned or last) - first).days, 1)
    committed = summary["committed"]
    series = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        ratio = max(0.0, 1 - offset / span)
        point: dict[str, Any] = {"date": day.isoformat(),
                                 "ideal_count": round(committed["count"] * ratio, 2),
                                 "ideal_points": round(committed["points"] * ratio, 2)}
        if day <= end.date():
            cutoff = min(_end_of(day), end)
            members = history.members(sprint.id, cutoff)
            done = [i for i in members if history.completed(i, cutoff)]
            point.update(scope_count=len(members), scope_points=history.pts(members, cutoff),
                         done_count=len(done), done_points=history.pts(done, cutoff))
            point["remaining_count"] = point["scope_count"] - point["done_count"]
            point["remaining_points"] = round(point["scope_points"] - point["done_points"], 1)
        series.append(point)

    moves = [("added", *e) for e in history.joins(sprint.id, start, end)]
    moves += [("removed", *e) for e in history.leaves(sprint.id, start, end)]
    inside = {i.id for i in history.committed(sprint, start)}
    events = []
    for kind, when, issue, actor in sorted(moves, key=lambda e: e[1]):
        if kind == "added" and issue.id not in inside:
            inside.add(issue.id)
        elif kind == "removed" and issue.id in inside:
            inside.discard(issue.id)
        else:
            continue
        events.append((kind, when, issue, actor))
    estimates = [(when, issue, old, new, actor) for issue in history.issues
                 for when, old, new, actor in history.points.between(issue, start, end)
                 if history.sprint.value_at(issue, when) == sprint.id]
    actor_ids = {e[3] for e in events} | {e[4] for e in estimates}
    names = {u.id: u.display_name for u in db.scalars(
        select(User).where(User.id.in_([a for a in actor_ids if a])))}
    changes = [{"kind": kind, "at": when.isoformat(), **_issue_ref(issue),
                "points": history.points.value_at(issue, when), "actor": names.get(actor)}
               for kind, when, issue, actor in events]
    reestimates = [{"at": when.isoformat(), **_issue_ref(issue), "old": old, "new": new,
                    "actor": names.get(actor)}
                   for when, issue, old, new, actor in sorted(estimates, key=lambda e: e[0])]
    return {**base, "series": series, "changes": changes, "reestimates": reestimates}


def load_history(db: Session, project: Project, days: int = 30,
                 include_subtasks: bool = True) -> dict[str, Any]:
    """Open work per assignee at the end of each day; the busiest people get their own line."""
    issues = _project_issues(db, project, include_subtasks)
    status = StatusHistory(db, project, issues)
    assignee = FieldHistory(db, issues, "assignee_id")
    now = datetime.now(timezone.utc)
    today = date.today()
    dates = [today - timedelta(days=days - 1 - n) for n in range(days)]
    per_day: list[Counter] = []
    for day in dates:
        cutoff = min(_end_of(day), now)
        counts: Counter = Counter()
        for issue in issues:
            current = status.status_at(issue, cutoff)
            if current is not None and current.category != "done":
                counts[assignee.value_at(issue, cutoff)] += 1
        per_day.append(counts)
    peak: Counter = Counter()
    for counts in per_day:
        for user_id, n in counts.items():
            if user_id is not None:
                peak[user_id] = max(peak[user_id], n)
    shown = [user_id for user_id, _ in sorted(peak.items(), key=lambda kv: (-kv[1], kv[0]))]
    shown = shown[:LOAD_SERIES]
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(shown)))}
    series: list[dict[str, Any]] = [
        {"kind": "user", "user": user_brief(users[uid]), "values": [c[uid] for c in per_day]}
        for uid in shown if uid in users
    ]
    others = [sum(n for uid, n in c.items() if uid is not None and uid not in users)
              for c in per_day]
    if any(others):
        series.append({"kind": "others", "user": None, "values": others})
    if any(c[None] for c in per_day):
        series.append({"kind": "unassigned", "user": None, "values": [c[None] for c in per_day]})
    return {"dates": [d.isoformat() for d in dates], "series": series}


def _scope_issues(db: Session, project: Project, issues: list[Issue], scope: str,
                  sprint: Sprint | None) -> tuple[list[Issue], dict[str, Any]]:
    if scope == "sprint" and sprint is not None:
        return ([i for i in issues if i.sprint_id == sprint.id],
                {"kind": "sprint", "id": sprint.id, "name": sprint.name,
                 "end_date": sprint.end_date.isoformat() if sprint.end_date else None})
    if scope.startswith("epic:") and scope[5:].isdigit():
        epic = db.get(Issue, int(scope[5:]))
        if epic is not None and epic.project_id == project.id and epic.type == "epic":
            children = {i.id for i in issues if i.epic_id == epic.id}
            return ([i for i in issues if i.id in children or i.parent_id in children],
                    {"kind": "epic", "id": epic.id, "key": epic.key, "name": epic.title,
                     "end_date": epic.due_date.isoformat() if epic.due_date else None})
    return issues, {"kind": "open", "end_date": None}


def _rank(sorted_values: list[int], share: float) -> int:
    index = math.ceil(share * len(sorted_values)) - 1
    return sorted_values[min(len(sorted_values) - 1, max(0, index))]


def forecast(db: Session, project: Project, days: int = 30, include_subtasks: bool = True,
             scope: str = "sprint", target: date | None = None) -> dict[str, Any]:
    """When the remaining work in a scope may finish, by replaying recent daily throughput.

    Each run draws past days at random until the remaining items are done; the spread of
    runs gives the confidence. Recent days include weekends and quiet days on purpose.
    """
    issues = _project_issues(db, project, include_subtasks)
    status = StatusHistory(db, project, issues)
    today = date.today()
    since = today - timedelta(days=days - 1)
    daily = [0] * days
    for issue in issues:
        finished = status.completed_at(issue)
        if finished is not None and since <= finished.date() <= today:
            daily[(finished.date() - since).days] += 1

    sprint = db.scalar(select(Sprint).where(Sprint.project_id == project.id,
                                            Sprint.state == "active").order_by(Sprint.id))
    epics = db.scalars(select(Issue).where(Issue.project_id == project.id, Issue.type == "epic")
                       .order_by(Issue.number))
    open_by_epic = Counter(i.epic_id for i in issues if i.epic_id and i.status.category != "done")
    options = {
        "sprint": {"id": sprint.id, "name": sprint.name} if sprint else None,
        "epics": [{"id": e.id, "key": e.key, "title": e.title, "open": open_by_epic[e.id]}
                  for e in epics if open_by_epic[e.id]],
    }
    if scope == "sprint" and sprint is None:
        scope = "open"
    chosen, info = _scope_issues(db, project, issues, scope, sprint)
    remaining = sum(1 for i in chosen if i.status.category != "done")
    end_date = date.fromisoformat(info["end_date"]) if info["end_date"] else None
    if target is None:
        target = end_date if end_date and end_date > today else today + timedelta(days=14)
    horizon = max((target - today).days, 0)
    out: dict[str, Any] = {
        "days": days,
        "throughput": {"total": sum(daily), "per_week": round(sum(daily) * 7 / days, 1),
                       "daily": [{"date": (since + timedelta(days=n)).isoformat(), "count": c}
                                 for n, c in enumerate(daily)]},
        "options": options,
        "scope": {**info, "total": len(chosen), "remaining": remaining},
        "runs": FORECAST_RUNS,
        "finish": None, "curve": [], "unreachable": 0.0,
        "target": {"date": target.isoformat(), "probability": None, "items": None},
    }
    if sum(daily) == 0:
        return out

    rng = random.Random(f"{project.id}:{scope}:{days}:{remaining}:{today}")
    finish = []
    for _ in range(FORECAST_RUNS):
        done = elapsed = 0
        while done < remaining and elapsed < FORECAST_HORIZON_DAYS:
            elapsed += 1
            done += rng.choice(daily)
        finish.append(elapsed if done >= remaining else FORECAST_HORIZON_DAYS + 1)
    finish.sort()
    reached = [d for d in finish if d <= FORECAST_HORIZON_DAYS]
    out["unreachable"] = round(1 - len(reached) / FORECAST_RUNS, 3)
    if reached:
        out["finish"] = {f"p{int(share * 100)}": (today + timedelta(days=_rank(finish, share)))
                         .isoformat() if _rank(finish, share) <= FORECAST_HORIZON_DAYS else None
                         for share in (0.5, 0.7, 0.85, 0.95)}
        last = min(_rank(finish, 0.99), FORECAST_HORIZON_DAYS)
        counts = Counter(finish)
        running = 0
        for d in range(0, last + 1):
            running += counts.get(d, 0)
            out["curve"].append({"date": (today + timedelta(days=d)).isoformat(),
                                 "probability": round(running * 100 / FORECAST_RUNS, 1)})

    sums = sorted(sum(rng.choice(daily) for _ in range(horizon)) for _ in range(FORECAST_RUNS))
    out["target"] = {
        "date": target.isoformat(),
        "probability": round(sum(1 for d in finish if d <= horizon) * 100 / FORECAST_RUNS, 1),
        "items": {"p50": _rank(sums, 0.5), "p85": _rank(sums, 0.15), "p95": _rank(sums, 0.05)},
    }
    return out
