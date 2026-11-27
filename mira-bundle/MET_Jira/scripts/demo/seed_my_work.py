#!/usr/bin/env python3
"""Fill one person's “我的工作” page on staging with realistic work in the demo projects.

Creates issues assigned to, reported by and tested by the user (some overdue, some due soon,
several waiting for their test), makes them a member of the demo projects, and adds recent
colleague comments and transitions so “关注的动态” and the notification centre have content.

Run from the staging worktree after seed_demo.py:

    METJIRA_DATA_DIR=~/.met_jira_staging METJIRA_DEPLOY_ENV=staging \\
        python3 scripts/demo/seed_my_work.py --user houcheng.fan --reset

Issues it creates are marked in ``extra["_demo"]`` so ``--reset`` removes exactly those.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import deque
from datetime import date, timedelta
from typing import Any

from seed_demo import NOW, ROOT, _aware, check_database, restamp_sla, silence_outbound

sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.db import session_factory  # noqa: E402
from backend.models import (  # noqa: E402
    ActivityLog,
    Issue,
    Notification,
    Project,
    ProjectMember,
    Status,
    User,
)
from backend.services import profiles, workflow  # noqa: E402
from backend.services.issues import add_comment, create_issue, update_issue, watch  # noqa: E402

MARK = "my_work"
REJECTED = "已驳回"

# (project, type, title, target status, my role, priority, due in days, age in days)
WORK: list[tuple[str, str, str, str, str, str, int, int]] = [
    ("ELEC", "requirement", "PM-C 射频功率曲线复核", "研发中", "assignee", "high", -3, 12),
    ("ELEC", "bug", "Orion-1 急停复位后报警不清除", "研发中", "assignee", "highest", 1, 2),
    ("ELEC", "task", "编写 TM 主控 PLC V4.0 升级方案", "待评估", "assignee", "medium", 6, 1),
    ("ELEC", "requirement", "Aurora-2 腔体压力联锁增加延时", "方案讨论", "assignee", "high", 4, 5),
    ("ELEC", "story", "作为维护人员，我希望在 HMI 查看 IO 实时状态", "研发中", "assignee",
     "medium", 10, 8),
    ("ELEC", "bug", "Load Port 光栅误报导致停机", "测试未通过", "assignee", "high", -1, 9),
    ("ELEC", "requirement", "机台告警推送至企业微信", "重新打开", "assignee", "medium", 3, 15),
    ("ELEC", "requirement", "ALD 新机台电气图纸评审", "方案讨论", "reporter", "medium", 8, 3),
    ("ELEC", "bug", "HMI 配方页面切换卡顿", "研发中", "reporter", "high", 2, 6),
    ("ELEC", "requirement", "安全 PLC 增加门禁联锁日志", "待评估", "reporter", "low", 20, 1),
    ("ELEC", "task", "整理 2026Q4 电气备件采购清单", "研发中", "reporter", "medium", -2, 10),
    ("ELEC", "bug", "真空计读数漂移修复验证", "测试中", "tester", "high", 1, 7),
    ("ELEC", "requirement", "Chiller 改走 EtherCAT", "测试中", "tester", "medium", 3, 14),
    ("ELEC", "story", "作为工艺工程师，我希望导出温度曲线", "测试中", "tester", "medium", 5, 11),
    ("ELEC", "bug", "TM 抓片偶发超时修复", "研发中", "tester", "high", 4, 4),
    ("ELEC", "requirement", "UPS 切电保护联调", "测试中", "tester", "highest", -1, 16),
    ("MECH", "requirement", "Orion-2 腔体夹具改型设计", "设计中", "assignee", "high", -4, 18),
    ("MECH", "task", "Load Port 支架图纸更新", "图纸评审", "assignee", "medium", 2, 6),
    ("MECH", "bug", "EFEM 门板与机械手干涉", "试装", "assignee", "highest", 1, 4),
    ("MECH", "requirement", "Aurora 冷却水管路重新布置", "待评估", "assignee", "medium", 12, 1),
    ("MECH", "requirement", "PM-B 加热器护罩改为不锈钢", "设计中", "reporter", "medium", 7, 5),
    ("MECH", "bug", "机械手末端执行器异常磨损", "终检", "reporter", "high", 0, 9),
    ("PROD", "requirement", "Aurora-3 整机装配排产", "已排产", "assignee", "high", 5, 3),
    ("PROD", "task", "Orion-1 返修件质检", "质检", "assignee", "medium", -2, 7),
    ("PROD", "bug", "在制品 WIP 标签打印错误", "在制", "assignee", "high", 1, 2),
    ("PROD", "requirement", "ALD 机台交付前整机测试", "在制", "reporter", "highest", 3, 10),
    ("PROD", "task", "月度产能盘点", "待分派", "reporter", "low", 14, 1),
]

COMMENTS = [
    "@{me} 现场已复现，日志已上传到附件，请帮忙看一下。",
    "@{me} 这个需要你确认一下方案，客户希望本周内给答复。",
    "已更新到最新版本，麻烦 @{me} 有空验证。",
    "备件预计周四到货，到货后安排上机。",
    "和工艺确认过了，参数按第二版执行。",
    "今天下午会同步进展，先记录一下当前阻塞点：等待供应商回复。",
    "补充了测试步骤和判定标准。",
]


def shortest_path(rules: dict[str, list[str]], start: str, goal: str) -> list[str]:
    """Statuses to step through from ``start`` to ``goal``, avoiding 已驳回 on the way."""
    if start == goal:
        return []
    queue, seen = deque([(start, [])]), {start}
    while queue:
        status, path = queue.popleft()
        for nxt in rules.get(status, []):
            if nxt in seen or (nxt == REJECTED and nxt != goal):
                continue
            if nxt == goal:
                return [*path, nxt]
            seen.add(nxt)
            queue.append((nxt, [*path, nxt]))
    raise ValueError(f"no path from {start} to {goal}")


def reset(db: Session) -> int:
    ids = [i.id for i in db.scalars(select(Issue)) if (i.extra or {}).get("_demo") == MARK]
    if ids:
        db.execute(delete(Issue).where(Issue.id.in_(ids)))
        db.commit()
    return len(ids)


def join_projects(db: Session, me: User, projects: dict[str, Project]) -> None:
    for project in projects.values():
        roles = profiles.profile_of(project).roles
        wanted = [r for r in (project.settings.get("profile"), "tester") if r in roles]
        have = set(db.scalars(select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == me.id)))
        for role in wanted:
            if role not in have:
                db.add(ProjectMember(project_id=project.id, user_id=me.id, role=role))
    db.commit()


def colleagues(db: Session, project: Project, me: User) -> list[User]:
    ids = set(db.scalars(select(ProjectMember.user_id).where(
        ProjectMember.project_id == project.id, ProjectMember.user_id != me.id)))
    return list(db.scalars(select(User).where(User.id.in_(ids), User.is_admin.is_(False))))


def required_fields(project: Project, number: int) -> dict[str, Any]:
    keys = {f.key for f in profiles.profile_of(project).custom_fields}
    out: dict[str, Any] = {}
    if "drawing_no" in keys:
        out["drawing_no"] = f"DWG-{2400 + number}"
    if "part_no" in keys:
        out["part_no"] = f"P-{10000 + number}"
    if "work_order" in keys:
        out["work_order"] = f"WO-2610-{number:03d}"
    if "batch_no" in keys:
        out["batch_no"] = f"B2610-{number:02d}"
    return out


def gate_fill(db: Session, project: Project, issue: Issue, step: str, actor: User,
              rng: random.Random) -> None:
    """Fill transition-gate / section fields before walking the flow (matches create UX)."""
    from backend.models import MasterAsset
    from backend.services import approvals

    target = next(s for s in workflow.project_statuses(db, project.id) if s.name == step)
    wanted = set(approvals.missing_fields(issue, target))
    changes: dict[str, Any] = {}
    custom: dict[str, Any] = {}
    for key in sorted(wanted):
        name = key.removeprefix("custom_fields.")
        if profiles.profile_of(project).custom_field(name):
            if name == "material":
                asset = db.scalar(select(MasterAsset).where(MasterAsset.kind == "material"))
                if asset:
                    custom[name] = asset.id
            elif name == "line":
                asset = db.scalar(select(MasterAsset).where(MasterAsset.kind == "line"))
                if asset:
                    custom[name] = asset.id
            elif name == "planned_date":
                custom[name] = (date.today() + timedelta(days=rng.randint(2, 10))).isoformat()
            elif name in {"inspection_result", "quality_result"}:
                custom[name] = "通过"
            elif name == "actual_quantity":
                custom[name] = rng.choice([1, 2, 4])
            elif name == "part_no":
                custom[name] = f"P-{rng.randint(10000, 99999)}"
            continue
        if name == "update_scope" and not (issue.update_scope or []):
            options = (project.settings or {}).get("update_scope_options") or ["硬件", "软件"]
            changes[name] = rng.sample(options, 1)
        elif name == "verify_method" and not issue.verify_method:
            changes[name] = "上机验证 + 72 小时稳定性运行"
        elif name == "verify_criteria" and not issue.verify_criteria:
            changes[name] = "连续 72 小时无相关报警"
        elif name == "tester_id" and issue.tester_id is None:
            changes[name] = actor.id
    if custom:
        changes["custom_fields"] = custom
    if changes:
        update_issue(db, issue, actor, changes, enforce=False)


def build(db: Session, me: User, rng: random.Random) -> list[Issue]:
    projects = {p.key: p for p in db.scalars(select(Project).where(
        Project.key.in_({row[0] for row in WORK}), Project.archived.is_(False)))}
    missing = {row[0] for row in WORK} - set(projects)
    if missing:
        sys.exit(f"demo projects missing: {', '.join(sorted(missing))}; run seed_demo.py first")
    join_projects(db, me, projects)
    team = {key: colleagues(db, p, me) for key, p in projects.items()}
    created: list[Issue] = []
    for number, (key, kind, title, target, role, priority, due, age) in enumerate(WORK, 1):
        project = projects[key]
        other, another = rng.sample(team[key], 2)
        assignee = me if role == "assignee" else other
        reporter = me if role == "reporter" else another
        tests = bool(project.settings.get("test_status"))
        tester = me if role == "tester" else (other if tests else None)
        payload: dict = {
            "type": kind, "title": title, "priority": priority,
            "assignee_id": assignee.id, "reporter_id": reporter.id,
            "tester_id": tester.id if tester else None,
            "due_date": date.today() + timedelta(days=due),
            "description": (
                f"## 背景\n{title}。\n\n"
                "## 期望\n按方案实现并通过现场验证，更新相关文档。\n\n"
                "## 验收标准\n功能符合方案，连续验证无异常。"
            ),
            "custom_fields": required_fields(project, number),
        }
        if kind == "requirement" and profiles.profile_of(project).key == "electrical":
            options = (project.settings or {}).get("update_scope_options") or ["硬件", "软件"]
            payload["update_scope"] = rng.sample(options, rng.choice([1, 2]))
            payload["project_name"] = rng.choice(
                ["Orion 产线升级", "Aurora 三期导入", "ALD 新机台联调"])
        issue = create_issue(db, project, reporter, payload)
        db.flush()
        statuses = {s.name: s for s in workflow.project_statuses(db, project.id)}
        rules = workflow.effective_transitions(project)[kind]
        for step in shortest_path(rules, issue.status.name, target):
            actor = tester if step in {"测试通过", "测试未通过"} and tester else assignee
            gate_fill(db, project, issue, step, actor, rng)
            changes: dict = {"status_id": statuses[step].id}
            if step == "重新打开":
                changes["reopen_reason"] = "测试未通过 / 现场复现，需返工"
            update_issue(db, issue, actor, changes, enforce=False)
        issue.extra = {**(issue.extra or {}), "_demo": MARK}
        created.append(issue)
        stamp(db, issue, age, rng)
    db.commit()
    return created


def stamp(db: Session, issue: Issue, age: int, rng: random.Random) -> None:
    """Spread the issue's history from ``age`` days ago up to a few hours ago."""
    db.flush()
    start = NOW - timedelta(days=age, hours=rng.randint(0, 6))
    end = NOW - timedelta(hours=rng.randint(1, 20) if age > 1 else rng.randint(1, 4))
    logs = list(db.scalars(select(ActivityLog).where(ActivityLog.issue_id == issue.id)
                           .order_by(ActivityLog.id)))
    for index, row in enumerate(logs):
        row.created_at = start + (end - start) * (index / max(1, len(logs) - 1))
    issue.created_at = start
    issue.updated_at = end
    for note in db.scalars(select(Notification).where(Notification.issue_id == issue.id)):
        note.created_at = end
    db.flush()
    db.refresh(issue)
    restamp_sla(issue, issue.project)


def chatter(db: Session, me: User, mine: list[Issue], rng: random.Random) -> int:
    """Recent colleague comments on my issues and on a few other open demo issues I watch."""
    count = 0
    others = [
        i for i in db.scalars(select(Issue).join(Status, Status.id == Issue.status_id).where(
            Status.category != "done", Issue.project_id.in_({i.project_id for i in mine})))
        if (i.extra or {}).get("_demo") != MARK
    ]
    targets = rng.sample(mine, min(10, len(mine))) + rng.sample(others, min(6, len(others)))
    for hours_ago, issue in enumerate(targets, 1):
        team = colleagues(db, issue.project, me)
        author = rng.choice(team)
        watch(db, issue.id, me.id)
        comment = add_comment(db, issue, author, rng.choice(COMMENTS).format(me=me.username))
        db.flush()
        when = NOW - timedelta(hours=hours_ago * 3 + rng.randint(0, 2), minutes=rng.randint(0, 59))
        comment.created_at = when
        for row in db.scalars(select(ActivityLog).where(
                ActivityLog.issue_id == issue.id, ActivityLog.action == "comment")):
            if (row.meta or {}).get("comment_id") == comment.id:
                row.created_at = when
        for note in db.scalars(select(Notification).where(
                Notification.issue_id == issue.id, Notification.created_at > NOW)):
            note.created_at = when
        if issue.updated_at is None or _aware(issue.updated_at) < when:
            issue.updated_at = when
        count += 1
    db.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user", default="houcheng.fan")
    parser.add_argument("--reset", action="store_true", help="remove issues from a previous run")
    parser.add_argument("--seed", type=int, default=20261013)
    args = parser.parse_args()
    check_database(False)
    silence_outbound()
    rng = random.Random(args.seed)
    with session_factory()() as db:
        me = db.scalar(select(User).where(User.username == args.user))
        if me is None:
            sys.exit(f"user {args.user!r} not found")
        if args.reset:
            print(f"removed {reset(db)} issues from a previous run")
        elif any((i.extra or {}).get("_demo") == MARK for i in db.scalars(select(Issue))):
            sys.exit("my-work demo issues already exist; use --reset to rebuild them")
        mine = build(db, me, rng)
        comments = chatter(db, me, mine, rng)
        counts = {role: sum(1 for row in WORK if row[4] == role)
                  for role in ("assignee", "reporter", "tester")}
        print(f"{me.display_name}: {len(mine)} issues {counts}, {comments} colleague comments")


if __name__ == "__main__":
    main()
