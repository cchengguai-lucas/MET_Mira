"""Dependency-aware schedule cascade suggestions for hardware-style sequential work.

Uses ``blocks`` links (source blocks target). Delayed sources push successor due dates
and may bump priority. Suggestions are computed only; apply writes via ``update_issue``.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import (
    ActivityLog,
    Issue,
    IssueLink,
    Project,
    ProjectMember,
    ProjectScheduleBaseline,
    ScheduleRiskEvent,
    Status,
    User,
)
from ..models.issue import PRIORITIES
from ..serialize import iso, issue_query
from . import permissions
from .issues import IssueError, update_issue

SLIP_LOOKBACK_DAYS = 7
PRIORITY_RANK = {name: index for index, name in enumerate(PRIORITIES)}


def _today() -> date:
    return date.today()


def _open_issues(db: Session, project_id: int) -> list[Issue]:
    return list(
        db.scalars(
            issue_query()
            .join(Status, Status.id == Issue.status_id)
            .where(
                Issue.project_id == project_id,
                Issue.type != "epic",
                Status.category != "done",
            )
        )
    )


def _block_edges(db: Session, issue_ids: set[int]) -> list[IssueLink]:
    if not issue_ids:
        return []
    return list(
        db.scalars(
            select(IssueLink).where(
            IssueLink.link_type == "blocks",
            IssueLink.source_id.in_(issue_ids),
            IssueLink.target_id.in_(issue_ids),
        )
        )
    )


def _due_slips(db: Session, issue_ids: set[int], *, since: datetime) -> set[int]:
    """Open issues whose due_date was pushed later in the lookback window."""
    if not issue_ids:
        return set()
    slipped: set[int] = set()
    rows = db.execute(
        select(ActivityLog.issue_id, ActivityLog.old_value, ActivityLog.new_value).where(
            ActivityLog.issue_id.in_(issue_ids),
            ActivityLog.field == "due_date",
            ActivityLog.created_at >= since,
        )
    ).all()
    for issue_id, old_value, new_value in rows:
        if not old_value or not new_value:
            continue
        try:
            old_d = date.fromisoformat(str(old_value)[:10])
            new_d = date.fromisoformat(str(new_value)[:10])
        except ValueError:
            continue
        if new_d > old_d:
            slipped.add(int(issue_id))
    return slipped


def _is_delay_source(issue: Issue, today: date, slipped: set[int]) -> bool:
    if issue.id in slipped:
        return True
    if issue.due_date is not None and issue.due_date < today:
        return True
    if issue.sla_state in {"warning", "breached"}:
        return True
    return False


def _bump_priority(current: str, ceiling: str) -> str | None:
    cur = PRIORITY_RANK.get(current)
    cap = PRIORITY_RANK.get(ceiling)
    if cur is None or cap is None or cur <= cap:
        return None
    # Lower index = higher priority; bump one step toward ceiling.
    nxt = PRIORITIES[cur - 1]
    if PRIORITY_RANK[nxt] < cap:
        return ceiling
    return nxt if nxt != current else None


def _calendar(project: Project) -> tuple[set[int], set[date], set[date]]:
    settings = project.settings or {}
    if settings.get("sla_calendar") != "business":
        return set(range(7)), set(), set()
    hours = settings.get("sla_business_hours") or {}
    # UI stores ISO weekday numbers (Monday=1); Python weekday uses Monday=0.
    workdays = {max(0, min(6, int(day) - 1)) for day in hours.get("workdays", [1, 2, 3, 4, 5])}
    holidays = {
        date.fromisoformat(value)
        for value in settings.get("sla_holidays", [])
        if isinstance(value, str)
    }
    extra = {
        date.fromisoformat(value)
        for value in settings.get("sla_extra_workdays", [])
        if isinstance(value, str)
    }
    return workdays, holidays, extra


def _is_workday(day: date, calendar: tuple[set[int], set[date], set[date]]) -> bool:
    workdays, holidays, extra = calendar
    return day in extra or (day.weekday() in workdays and day not in holidays)


def _add_workdays(
    start: date,
    amount: int,
    calendar: tuple[set[int], set[date], set[date]],
) -> date:
    if amount <= 0:
        return start
    day = start
    remaining = amount
    while remaining:
        day += timedelta(days=1)
        if _is_workday(day, calendar):
            remaining -= 1
    return day


def _workday_distance(
    start: date,
    end: date,
    calendar: tuple[set[int], set[date], set[date]],
) -> int:
    if end == start:
        return 0
    sign = 1 if end > start else -1
    day = start
    total = 0
    while day != end:
        day += timedelta(days=sign)
        if _is_workday(day, calendar):
            total += sign
    return total


def _cycle_components(nodes: set[int], outbound: dict[int, list[int]]) -> list[list[int]]:
    """Tarjan SCCs, including explicit self-cycles."""
    index = 0
    stack: list[int] = []
    on_stack: set[int] = set()
    indexes: dict[int, int] = {}
    low: dict[int, int] = {}
    found: list[list[int]] = []

    def visit(node: int) -> None:
        nonlocal index
        indexes[node] = low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for child in outbound.get(node, []):
            if child not in indexes:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in on_stack:
                low[node] = min(low[node], indexes[child])
        if low[node] != indexes[node]:
            return
        component: list[int] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or node in outbound.get(node, []):
            found.append(component)

    for node in nodes:
        if node not in indexes:
            visit(node)
    return found


def _topological(
    nodes: set[int],
    edges: list[IssueLink],
    excluded: set[int],
) -> tuple[list[int], dict[int, list[IssueLink]]]:
    inbound: dict[int, list[IssueLink]] = defaultdict(list)
    outbound: dict[int, list[int]] = defaultdict(list)
    indegree = {node: 0 for node in nodes if node not in excluded}
    for edge in edges:
        if edge.source_id in excluded or edge.target_id in excluded:
            continue
        inbound[edge.target_id].append(edge)
        outbound[edge.source_id].append(edge.target_id)
        indegree[edge.target_id] += 1
    ready = sorted(node for node, count in indegree.items() if count == 0)
    ordered: list[int] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for child in outbound.get(node, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    return ordered, inbound


def _baseline_state(
    db: Session,
    project: Project,
    issues: list[Issue],
) -> tuple[ProjectScheduleBaseline | None, dict[int, dict[str, Any]], list[str]]:
    baseline = db.scalar(
        select(ProjectScheduleBaseline).where(
            ProjectScheduleBaseline.project_id == project.id,
            ProjectScheduleBaseline.active.is_(True),
        )
    )
    warnings: list[str] = []
    if baseline is None:
        warnings.append("尚未发布计划基线；当前结果为影子分析，延期值仅供参考")
        return None, {
            issue.id: {
                "planned_start_date": iso(issue.planned_start_date),
                "due_date": iso(issue.due_date),
            }
            for issue in issues
        }, warnings
    return baseline, {
        int(row["id"]): row for row in baseline.snapshot.get("issues", [])
        if row.get("id") is not None
    }, warnings


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _urgency(delay_days: int, target_impact: int) -> tuple[str, int]:
    score = max(delay_days, 0) * 4 + max(target_impact, 0) * 8
    if target_impact > 0 or delay_days >= 10:
        return "critical", min(100, max(80, score))
    if delay_days >= 5:
        return "high", min(79, max(60, score))
    if delay_days > 0:
        return "watch", min(59, max(30, score))
    return "normal", min(29, score)


def suggest_cascade(db: Session, project: Project) -> dict[str, Any]:
    """Produce a stable baseline-relative forecast over a cycle-safe dependency DAG."""
    if permissions.is_ongoing(project):
        today = _today()
        return {
            "project_key": project.key,
            "work_mode": "ongoing",
            "today": iso(today),
            "analysis_token": "",
            "baseline_version": None,
            "baseline_mode": "disabled",
            "target_date": None,
            "forecast_target_date": None,
            "target_impact_days": 0,
            "confidence": "low",
            "data_quality": {
                "warnings": ["持续项目不计算项目级排期顺延"],
                "cycles": [],
                "missing_due_keys": [],
            },
            "delay_sources": [],
            "edges": [],
            "edge_count": 0,
            "suggestion_count": 0,
            "suggestions": [],
            "at_risk_keys": [],
            "impacted_gates": [],
        }
    today = _today()
    since = datetime.now(timezone.utc) - timedelta(days=SLIP_LOOKBACK_DAYS)
    issues = _open_issues(db, project.id)
    by_id = {i.id: i for i in issues}
    ids = set(by_id)
    edges = _block_edges(db, ids)
    outbound: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        outbound[edge.source_id].append(edge.target_id)

    cycle_ids = _cycle_components(ids, outbound)
    cycle_nodes = {node for component in cycle_ids for node in component}
    ordered, inbound = _topological(ids, edges, cycle_nodes)
    baseline, baseline_by_id, warnings = _baseline_state(db, project, issues)
    if cycle_ids:
        warnings.append(
            f"发现 {len(cycle_ids)} 组循环阻塞依赖；循环内问题已从预测传播中隔离"
        )

    slipped = _due_slips(db, ids, since=since)
    sources = [i for i in issues if _is_delay_source(i, today, slipped)]
    calendar = _calendar(project)
    baseline_due = {
        issue.id: _parse_date(baseline_by_id.get(issue.id, {}).get("due_date"))
        or issue.due_date
        for issue in issues
    }
    forecast: dict[int, date | None] = {
        issue.id: issue.due_date or baseline_due[issue.id] for issue in issues
    }
    depth: dict[int, int] = {issue.id: 0 for issue in issues}
    constraints: dict[int, list[int]] = defaultdict(list)
    source_roots: dict[int, set[int]] = {issue.id: set() for issue in issues}
    source_ids = {issue.id for issue in sources}
    for source in sources:
        source_roots[source.id].add(source.id)
        if source.due_date and source.due_date < today:
            forecast[source.id] = max(source.due_date, today)

    for node_id in ordered:
        candidates: list[tuple[date, int]] = []
        for edge in inbound.get(node_id, []):
            predecessor_due = forecast.get(edge.source_id)
            if predecessor_due is None:
                continue
            predecessor_base = baseline_due.get(edge.source_id)
            node_base = baseline_due.get(node_id)
            baseline_gap = (
                _workday_distance(predecessor_base, node_base, calendar)
                if predecessor_base and node_base
                else edge.lag_days
            )
            candidate = _add_workdays(
                predecessor_due,
                max(edge.lag_days, baseline_gap),
                calendar,
            )
            candidates.append((candidate, edge.source_id))
        if candidates:
            forced = max(day for day, _predecessor in candidates)
            constraining = [
                predecessor for day, predecessor in candidates if day == forced
            ]
            current = forecast[node_id]
            if current is None or forced > current:
                forecast[node_id] = forced
                constraints[node_id] = constraining
            for predecessor in constraining:
                source_roots[node_id].update(source_roots.get(predecessor, set()))
                if predecessor in source_ids:
                    source_roots[node_id].add(predecessor)
                depth[node_id] = max(depth[node_id], depth.get(predecessor, 0) + 1)

    items: list[dict[str, Any]] = []
    baseline_target = baseline.target_date if baseline else project.target_date
    project_forecast = max((day for day in forecast.values() if day), default=None)
    project_target_impact = (
        max(0, _workday_distance(baseline_target, project_forecast, calendar))
        if baseline_target and project_forecast
        else 0
    )
    for issue_id, issue in by_id.items():
        base_due = baseline_due.get(issue_id)
        predicted = forecast.get(issue_id)
        current_due = issue.due_date
        variance = (
            _workday_distance(base_due, predicted, calendar)
            if base_due and predicted
            else 0
        )
        changed = predicted is not None and (current_due is None or predicted > current_due)
        root_ids = source_roots.get(issue_id, set())
        if not changed and not root_ids:
            continue
        issue_target_impact = (
            max(0, _workday_distance(baseline_target, predicted, calendar))
            if baseline_target and predicted
            else 0
        )
        urgency, urgency_score = _urgency(variance, issue_target_impact)
        root_priorities = [by_id[root].priority for root in root_ids if root in by_id]
        priority_ceiling = min(
            root_priorities,
            key=lambda value: PRIORITY_RANK.get(value, 99),
            default=issue.priority,
        )
        priority_recommendation = _bump_priority(issue.priority, priority_ceiling)
        confidence = "high" if baseline and base_due and predicted else "medium"
        delay_source_keys = sorted(by_id[root].key for root in root_ids)
        predecessor_keys = [
            by_id[pred].key for pred in constraints.get(issue_id, [])
        ]
        evidence: list[str] = []
        if delay_source_keys:
            evidence.append("延误源：" + "、".join(delay_source_keys))
        if predecessor_keys:
            evidence.append("约束前置：" + "、".join(predecessor_keys))
        if baseline is None:
            evidence.append("未发布基线")
        gate_labels = [
            label
            for label in (issue.labels or [])
            if str(label).lower() in {"gate", "milestone", "里程碑", "门节点", "关键门"}
        ]
        reason_parts: list[str] = []
        if delay_source_keys:
            reason_parts.append(f"因 {'、'.join(delay_source_keys)} 延期传导")
        elif predecessor_keys:
            reason_parts.append(f"受 {'、'.join(predecessor_keys)} 完成约束")
        if variance > 0:
            reason_parts.append(f"建议后移 {variance} 个工作日")
        if gate_labels:
            reason_parts.append(f"触及 {'/'.join(gate_labels)}")
        if issue_target_impact > 0:
            reason_parts.append(f"目标日影响 +{issue_target_impact}d")
        reason_brief = "；".join(reason_parts) or "顺序约束需要同步调整"
        items.append(
            {
                "id": issue.id,
                "key": issue.key,
                "title": issue.title,
                "type": issue.type,
                "status": issue.status.name if issue.status else None,
                "baseline_start": baseline_by_id.get(issue_id, {}).get("planned_start_date"),
                "baseline_due": iso(base_due),
                "current_start": iso(issue.planned_start_date),
                "current_due": iso(current_due),
                "forecast_due": iso(predicted),
                "suggested_due": iso(predicted) if changed else None,
                "variance_days": variance,
                "current_priority": issue.priority,
                "suggested_priority": priority_recommendation,
                "schedule_urgency": urgency,
                "urgency_score": urgency_score,
                "target_impact_days": issue_target_impact,
                "reason": (
                    delay_source_keys[0] if delay_source_keys else
                    predecessor_keys[0] if predecessor_keys else None
                ),
                "reason_brief": reason_brief,
                "delay_sources": delay_source_keys,
                "constraining_predecessors": predecessor_keys,
                "depth": depth.get(issue_id, 0),
                "confidence": confidence,
                "evidence": evidence,
                "impacted_gates": gate_labels,
                "expected_lock_version": issue.lock_version,
            }
        )
    items.sort(key=lambda row: (-row["urgency_score"], -row["depth"], row["key"]))

    token_material = {
        "baseline": baseline.version if baseline else None,
        "issues": [
            [issue.id, issue.lock_version, iso(issue.due_date), issue.priority]
            for issue in sorted(issues, key=lambda item: item.id)
        ],
        "links": [
            [edge.id, edge.source_id, edge.target_id, edge.lag_days]
            for edge in sorted(edges, key=lambda item: item.id)
        ],
    }
    analysis_token = hashlib.sha256(
        json.dumps(token_material, separators=(",", ":")).encode()
    ).hexdigest()

    return {
        "project_key": project.key,
        "today": iso(today),
        "analysis_token": analysis_token,
        "baseline_version": baseline.version if baseline else None,
        "baseline_mode": "locked" if baseline else "shadow",
        "target_date": iso(baseline_target),
        "forecast_target_date": iso(project_forecast),
        "target_impact_days": project_target_impact,
        "confidence": (
            "low" if cycle_ids else "high" if baseline else "medium"
        ),
        "data_quality": {
            "warnings": warnings,
            "cycles": [
                sorted(by_id[node].key for node in component)
                for component in cycle_ids
            ],
            "missing_due_keys": sorted(
                issue.key for issue in issues if issue.due_date is None
            ),
        },
        "delay_sources": [
            {
                "key": i.key,
                "title": i.title,
                "baseline_due": iso(baseline_due.get(i.id)),
                "due_date": iso(i.due_date),
                "forecast_due": iso(forecast.get(i.id)),
                "sla_state": i.sla_state,
                "slipped": i.id in slipped,
            }
            for i in sources
        ],
        "edges": [
            {
                "id": edge.id,
                "source_key": by_id[edge.source_id].key,
                "target_key": by_id[edge.target_id].key,
                "lag_days": edge.lag_days,
                "in_cycle": edge.source_id in cycle_nodes and edge.target_id in cycle_nodes,
            }
            for edge in edges
            if edge.source_id in by_id and edge.target_id in by_id
        ],
        "edge_count": len(edges),
        "suggestion_count": len(items),
        "suggestions": items,
        "at_risk_keys": [row["key"] for row in items],
        "impacted_gates": [
            {
                "key": row["key"],
                "title": row["title"],
                "labels": row["impacted_gates"],
                "forecast_due": row["forecast_due"],
                "variance_days": row["variance_days"],
            }
            for row in items
            if row["impacted_gates"]
        ],
    }


def sync_risk_events(
    db: Session,
    project: Project,
    analysis: dict[str, Any],
) -> list[ScheduleRiskEvent]:
    """Upsert one durable risk per affected issue and resolve disappeared risks."""
    known = {
        event.source_issue_id: event
        for event in db.scalars(
            select(ScheduleRiskEvent).where(
                ScheduleRiskEvent.project_id == project.id,
                ScheduleRiskEvent.kind == "schedule_delay",
            )
        )
    }
    seen: set[int] = set()
    out: list[ScheduleRiskEvent] = []
    now = utcnow()
    for suggestion in analysis["suggestions"]:
        issue_id = int(suggestion["id"])
        seen.add(issue_id)
        event = known.get(issue_id)
        payload = {
            "key": suggestion["key"],
            "title": suggestion["title"],
            "baseline_due": suggestion["baseline_due"],
            "current_due": suggestion["current_due"],
            "forecast_due": suggestion["forecast_due"],
            "variance_days": suggestion["variance_days"],
            "target_impact_days": suggestion["target_impact_days"],
            "schedule_urgency": suggestion["schedule_urgency"],
            "delay_sources": suggestion["delay_sources"],
            "constraining_predecessors": suggestion["constraining_predecessors"],
            "confidence": suggestion["confidence"],
            "analysis_token": analysis["analysis_token"],
            "baseline_version": analysis["baseline_version"],
        }
        if event is None:
            fingerprint = hashlib.sha256(
                f"{project.id}:schedule_delay:{issue_id}".encode()
            ).hexdigest()
            event = ScheduleRiskEvent(
                project_id=project.id,
                fingerprint=fingerprint,
                kind="schedule_delay",
                severity=suggestion["schedule_urgency"],
                status="new",
                source_issue_id=issue_id,
                payload=payload,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(event)
        else:
            previous_impact = int((event.payload or {}).get("target_impact_days") or 0)
            previous_variance = int((event.payload or {}).get("variance_days") or 0)
            alerted_version = (event.payload or {}).get("alerted_version")
            worsened = (
                suggestion["target_impact_days"] > previous_impact
                or suggestion["variance_days"] > previous_variance
            )
            if worsened:
                event.status = "worsening"
                event.lock_version += 1
                if event.decision in {"reject", "accept"}:
                    event.decision = None
                    event.decision_note = ""
                    event.decided_at = None
                    event.decided_by_id = None
                    event.snoozed_until = None
            if alerted_version is not None:
                payload["alerted_version"] = alerted_version
            event.severity = suggestion["schedule_urgency"]
            event.payload = payload
            event.last_seen_at = now
        db.flush()
        suggestion["risk_event_id"] = event.id
        suggestion["risk_status"] = event.status
        suggestion["risk_lock_version"] = event.lock_version
        out.append(event)
    for issue_id, event in known.items():
        if issue_id not in seen and event.status in {
            "new",
            "worsening",
            "acknowledged",
            "snoozed",
        }:
            event.status = "resolved"
            event.last_seen_at = now
            event.lock_version += 1
    db.flush()
    return out


def risk_event_out(event: ScheduleRiskEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "lock_version": event.lock_version,
        "fingerprint": event.fingerprint,
        "kind": event.kind,
        "severity": event.severity,
        "status": event.status,
        "source_issue_id": event.source_issue_id,
        "owner_id": event.owner_id,
        "payload": event.payload or {},
        "first_seen_at": iso(event.first_seen_at),
        "last_seen_at": iso(event.last_seen_at),
        "acknowledged_by_id": event.acknowledged_by_id,
        "snoozed_until": iso(event.snoozed_until),
        "decision": event.decision,
        "decision_note": event.decision_note,
        "decided_by_id": event.decided_by_id,
        "decided_at": iso(event.decided_at),
    }


def preview_cascade(
    db: Session,
    project: Project,
    analysis_token: str,
    keys: list[str],
    *,
    sync_priority: bool = False,
) -> dict[str, Any]:
    analysis = suggest_cascade(db, project)
    if analysis["analysis_token"] != analysis_token:
        return {
            "stale": True,
            "analysis_token": analysis["analysis_token"],
            "items": [],
        }
    selected = {
        key.upper() for key in keys
    }
    items = [
        {
            "key": row["key"],
            "due_date": row["suggested_due"],
            "priority": row["suggested_priority"] if sync_priority else None,
            "variance_days": row["variance_days"],
            "target_impact_days": row["target_impact_days"],
        }
        for row in analysis["suggestions"]
        if row["key"] in selected
    ]
    return {
        "stale": False,
        "analysis_token": analysis_token,
        "baseline_version": analysis["baseline_version"],
        "target_date": analysis["target_date"],
        "forecast_target_date": analysis["forecast_target_date"],
        "target_impact_days": analysis["target_impact_days"],
        "sync_priority": sync_priority,
        "items": items,
    }


def apply_cascade(
    db: Session,
    project: Project,
    actor: User,
    items: list[dict[str, Any]],
    *,
    analysis_token: str,
    sync_priority: bool = False,
    note: str | None = None,
) -> dict[str, Any]:
    """Apply only exact, current suggestions and report stale items individually."""
    roles = permissions.project_roles(db, project.id, actor)
    if not permissions.is_project_admin(actor, roles):
        raise IssueError("需要项目管理员权限", 403)
    if permissions.is_ongoing(project):
        raise IssueError("持续项目不支持项目级排期顺延", 400)

    analysis = suggest_cascade(db, project)
    current_token = analysis["analysis_token"]
    if current_token != analysis_token:
        return {
            "applied": [],
            "skipped": [
                {"key": str(raw.get("key") or "").upper(), "reason": "stale_analysis"}
                for raw in items
            ],
            "count": 0,
            "stale": True,
            "analysis_token": current_token,
        }
    sync_risk_events(db, project, analysis)
    suggestions = {row["key"]: row for row in analysis["suggestions"]}
    batch_id = uuid4().hex
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw in items:
        key = str(raw.get("key") or "").upper()
        suggestion = suggestions.get(key)
        if suggestion is None:
            skipped.append({"key": key, "reason": "not_suggested"})
            continue
        issue = db.scalars(issue_query().where(Issue.key == key)).first()
        if issue is None or issue.project_id != project.id:
            skipped.append({"key": key, "reason": "not_found"})
            continue
        if issue.status and issue.status.category == "done":
            skipped.append({"key": key, "reason": "done"})
            continue
        if (
            raw.get("expected_lock_version") != suggestion["expected_lock_version"]
            or issue.lock_version != suggestion["expected_lock_version"]
        ):
            skipped.append({"key": key, "reason": "stale_issue"})
            continue
        changes: dict[str, Any] = {}
        if raw.get("due_date"):
            requested_due = str(raw["due_date"])[:10]
            if requested_due != suggestion["suggested_due"]:
                skipped.append({"key": key, "reason": "not_suggested"})
                continue
            changes["due_date"] = date.fromisoformat(requested_due)
        if sync_priority and raw.get("priority") in PRIORITIES:
            if raw["priority"] != suggestion["suggested_priority"]:
                skipped.append({"key": key, "reason": "not_suggested"})
                continue
            changes["priority"] = raw["priority"]
        if not changes:
            skipped.append({"key": key, "reason": "empty"})
            continue
        try:
            update_issue(db, issue, actor, changes, enforce=True)
            rendered = {
                field: iso(value) if isinstance(value, date) else value
                for field, value in changes.items()
            }
            applied.append({"key": key, "changes": rendered})
            db.add(
                ActivityLog(
                    issue_id=issue.id,
                    actor_id=actor.id,
                    action="cascade_apply",
                    new_value=note[:500] if note else None,
                    meta={
                        "batch_id": batch_id,
                        "analysis_token": analysis_token,
                        "baseline_version": analysis["baseline_version"],
                        "changes": rendered,
                    },
                )
            )
            event = db.get(ScheduleRiskEvent, suggestion.get("risk_event_id"))
            if event is not None:
                event.status = "accepted"
                event.decision = "accept"
                event.decision_note = note or ""
                event.decided_by_id = actor.id
                event.decided_at = utcnow()
                event.lock_version += 1
        except IssueError as error:
            skipped.append({"key": key, "reason": str(error)})

    db.commit()
    return {
        "applied": applied,
        "skipped": skipped,
        "count": len(applied),
        "stale": False,
        "batch_id": batch_id,
        "analysis_token": current_token,
    }


def decide_risk(
    db: Session,
    project: Project,
    actor: User,
    issue_key: str,
    *,
    action: str,
    expected_version: int,
    note: str = "",
    snoozed_until: datetime | None = None,
) -> dict[str, Any]:
    roles = permissions.project_roles(db, project.id, actor)
    if not permissions.is_project_admin(actor, roles):
        raise IssueError("需要项目管理员权限", 403)
    analysis = suggest_cascade(db, project)
    sync_risk_events(db, project, analysis)
    suggestion = next(
        (row for row in analysis["suggestions"] if row["key"] == issue_key.upper()),
        None,
    )
    if suggestion is None:
        raise IssueError("风险已消失或不属于当前项目", 409)
    event = db.get(ScheduleRiskEvent, suggestion["risk_event_id"])
    if event is None:
        raise IssueError("风险事件不存在", 404)
    if event.lock_version != expected_version:
        raise IssueError("风险已更新，请刷新后重试", 409)
    now = utcnow()
    if action == "snooze":
        if snoozed_until is None or snoozed_until <= now:
            raise IssueError("请选择未来的提醒时间")
        event.status = "snoozed"
        event.snoozed_until = snoozed_until
    elif action == "acknowledge":
        event.status = "acknowledged"
        event.acknowledged_by_id = actor.id
    else:
        event.status = "accepted" if action == "accept" else "rejected"
        event.decision = action
        event.decision_note = note
        event.decided_by_id = actor.id
        event.decided_at = now
    event.lock_version += 1
    issue = db.get(Issue, event.source_issue_id)
    if issue is not None:
        db.add(
            ActivityLog(
                issue_id=issue.id,
                actor_id=actor.id,
                action="schedule_risk_decision",
                new_value=action,
                meta={"event_id": event.id, "note": note[:500]},
            )
        )
    db.commit()
    db.refresh(event)
    return risk_event_out(event)


def managed_projects(db: Session, user: User) -> list[Project]:
    """Projects the user may administer (system admin → all non-archived visible)."""
    hidden = permissions.hidden_project_ids(db, user)
    stmt = select(Project).where(Project.archived.is_(False)).order_by(Project.key)
    if hidden:
        stmt = stmt.where(Project.id.notin_(hidden))
    projects = list(db.scalars(stmt))
    if user.is_admin:
        return projects
    admin_ids = set(
        db.scalars(
            select(ProjectMember.project_id).where(
                ProjectMember.user_id == user.id,
                ProjectMember.role == "admin",
            )
        )
    )
    return [p for p in projects if p.lead_id == user.id or p.id in admin_ids]


def has_portfolio_access(db: Session, user: User) -> bool:
    """True when the user administers at least one project (or is admin).

    Covers both delivery and ongoing projects so leads/admins can open the
    manager workspaces (交付管理 / 持续运维).
    """
    if user.is_admin:
        return True
    return bool(managed_projects(db, user))


def default_portfolio_view(db: Session, user: User) -> str | None:
    """Pick delivery or ongoing for the home landing when the URL has no view.

    System admins land on the mode with more managed projects. Ties and
    non-admins keep delivery. Returns None when the user has no portfolio access.
    """
    if not has_portfolio_access(db, user):
        return None
    if not user.is_admin:
        return "delivery"
    projects = managed_projects(db, user)
    ongoing_n = sum(1 for project in projects if permissions.is_ongoing(project))
    delivery_n = len(projects) - ongoing_n
    return "ongoing" if ongoing_n > delivery_n else "delivery"
