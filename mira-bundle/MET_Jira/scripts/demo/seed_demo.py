#!/usr/bin/env python3
"""Staging showcase data: publish the latest built-in department templates and build one demo
project per template with every situation the product handles.

The data is produced by replaying eight weeks of work through the real services (workflow
rules, gates, approvals, sprints, comments) in time order; every row is then stamped with the
simulated time, so trends, cumulative flow, aging, velocity and the forecast look like a team
that has been working for a while.

Run from the staging worktree (it refuses any database other than ``met_jira_staging``):

    METJIRA_DATA_DIR=~/.met_jira_staging METJIRA_DEPLOY_ENV=staging \\
        python3 scripts/demo/seed_demo.py --reset

Outbound mail, WeCom pushes and webhooks are disabled while it runs; in-app notifications are
still created so the notification centre has content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select, update  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.config import get_settings  # noqa: E402
from backend.db import session_factory  # noqa: E402
from backend.models import (  # noqa: E402
    ActivityLog,
    ApprovalRequest,
    Comment,
    DepartmentTemplateVersion,
    Issue,
    IssueLink,
    Machine,
    MachineGroup,
    MasterAsset,
    Notification,
    OrgUnit,
    OrgUnitTemplateRecommendation,
    Project,
    ProjectAsset,
    ProjectMember,
    ProjectScheduleBaseline,
    Sprint,
    Status,
    User,
)
from backend.services import approvals, profiles, sla, software, templates, workflow  # noqa: E402
from backend.services.issues import (  # noqa: E402
    IssueError,
    add_comment,
    create_issue,
    update_issue,
    watch,
)
from backend.services.reports import _aware  # noqa: E402

LOCAL = datetime.now().astimezone().tzinfo
NOW = datetime.now(LOCAL)
TODAY = NOW.date()
STAMPED = (ActivityLog, Comment, Notification, ApprovalRequest, IssueLink)
# Two-week sprints: three closed, one active (started a week ago) and one planned.
SPRINT_WINDOWS = [(-49, -35), (-35, -21), (-21, -7), (-7, 7), (7, 21)]


# ---------------------------------------------------------------- department content


@dataclass(frozen=True)
class Department:
    key: str
    name: str
    profile: str
    color: str
    description: str
    lead_unit: str
    team_role: str
    team_units: tuple[str, ...]
    verify_role: str
    verify_units: tuple[str, ...]
    requester_units: tuple[str, ...]
    owner_units: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    bugs: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    stories: tuple[str, ...] = ()
    epics: tuple[str, ...] = ()
    subtasks: tuple[str, ...] = ()
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommend_units: tuple[str, ...] = ()


ELECTRICAL = Department(
    key="ELEC", name="电气需求（演示）", profile="electrical", color="#0a84ff",
    description="演示项目：电气组需求从受理、方案讨论、研发到测试验收的完整流程。",
    lead_unit="MET / Electrical Engineering", team_role="electrical",
    team_units=("MET / Electrical Engineering",), verify_role="tester",
    verify_units=("MET / Lab", "MET / Software / Software QA"),
    requester_units=("MET / CVD / Process R&D", "MET / ETCH / ICP", "MET / Account Team / Services",
                     "MET / MDP / BEOL&MOL"),
    owner_units=("MET / Software / Development",),
    projects=("Orion 产线升级", "Aurora 三期导入", "ALD 新机台联调", "客户 C 定制改造",
              "电气安全合规 2026"),
    requirements=(
        "PM-A 温度闭环增益整定", "EFEM 掉片传感器灵敏度校准", "LL 腔体数位真空计替换",
        "TM 主控 PLC 升级至 V4.0", "射频互锁增加双通道冗余", "急停回路自检功能",
        "Chiller 改走 EtherCAT", "机台告警导出至 MES", "PM-B 加热功率曲线重标定",
        "UPS 切电保护联调", "配电柜线号标准化", "传感器滤波窗口可配置",
        "Aurora-2 腔体压力 PID 再整定", "Wafer ID 扫码枪对接", "气路阀位实时监控",
        "配方下发 CRC 校验", "门禁联锁接入安全 PLC", "待机功耗统计看板",
        "伺服报警码中文对照", "Load Port 光栅防误触发", "电气图纸同步至 PLM",
        "接地电阻巡检周期调整", "HMI 中英切换与字体统一", "冷却水流量低延时报警",
        "腔体照明 LED 改造", "远程诊断 VPN 通道开通", "加热带过温双限保护",
        "气体面板 MFC 校准提醒", "RF 匹配器状态上报 SCADA", "机台节能待机模式",
        "备用电源自动切换演练", "温控表通讯双链路", "排风压差联锁",
        "Orion-3 IO 扩展柜接线", "真空泵运行时长统计", "安全光幕旁路钥匙管理",
    ),
    bugs=("TM 抓片偶发超时", "PM-A 温度读数毛刺", "HMI 报警历史翻页空白",
          "急停复位后风扇不转", "真空计零点漂移", "配方另存后丢参",
          "Chiller 掉线无弹窗", "Load Port 门开关抖振"),
    tasks=("整理 2026Q4 电气备件", "更新电气安全培训课件", "核对 Orion-3 IO 点表",
           "编写 PLC 升级回退手册", "现场接线抽检", "电气柜年度点检计划"),
    stories=("作为工艺工程师，我希望在 HMI 查看实时温度曲线",
             "作为维护人员，我希望收到预防性维护推送",
             "作为产线主管，我希望导出班次报警汇总"),
    epics=("Orion 系列安全合规升级", "Aurora 三期电气改造", "智能诊断与远程运维"),
    subtasks=("修改 PLC 程序", "更新电气图纸", "现场上机验证", "编写测试报告", "确认备件到货"),
    gates={
        "方案讨论": {"required_fields": [],
                   "approvals": [{"role": "electrical", "count": 1}]},
        "研发中": {"required_fields": ["update_scope", "verify_method"], "approvals": []},
        "测试中": {"required_fields": ["verify_criteria"], "approvals": []},
    },
    recommend_units=("MET / Electrical Engineering",),
)

MECHANICAL = Department(
    key="MECH", name="机械设计（演示）", profile="mechanical", color="#ff9f0a",
    description="演示项目：机械设计从设计、图纸评审、试装到终检的流程，含材料主数据与终检结果。",
    lead_unit="MET / CVD / Mechanical Engineering", team_role="mechanical",
    team_units=("MET / CVD / Mechanical Engineering", "MET / MDP / BEOL&MOL"),
    verify_role="quality", verify_units=("MET / Quality / Supplier Quality",),
    requester_units=("MET / ETCH / SCE", "MET / CVD / Product", "MET / Manufacture / MFG"),
    projects=("Aurora 三期结构", "Orion 腔体改进", "钣金降本 2.0", "客户 C 定制机架"),
    requirements=(
        "PM 盖板加强筋拓扑优化", "EFEM 前端框架铝合金轻量化", "Load Port 支架隔振",
        "冷却水管路重新布线", "传送腔视窗密封改型", "晶圆舟定位销耐磨升级",
        "机台底座三点调平机构", "气体面板开孔标准化", "顶针升降寿命提升",
        "腔体内衬陶瓷涂层", "外罩钣金减重开孔", "维护门铰链加固", "排气管路隔热层",
        "机械手末端执行器换型", "加热套快拆固定", "真空法兰系列化",
        "线缆拖链路径优化", "冷却板流道仿真改版", "机台吊装点校核", "防护罩快拆卡扣",
        "腔体搬运工装", "波纹管寿命改进", "门板密封条规格统一", "脚轮承重复核",
        "FFU 安装支架", "观察窗加热防雾",
    ),
    bugs=("盖板螺纹孔偏 0.3mm", "支架焊接受热变形", "视窗密封圈压缩不足",
          "定位销尺寸超差", "钣金折弯角偏差"),
    tasks=("整理标准件选型库", "BOM 同步至 PLM", "修订来料检验标准", "图纸归档抽查"),
    epics=("Aurora 三期机械结构", "腔体可维护性改进", "钣金件降本 2.0"),
    subtasks=("出 3D 模型", "出 2D 工程图", "供应商打样", "首件检验"),
    gates={
        "图纸评审": {"required_fields": ["custom_fields.part_no"],
                   "approvals": [{"role": "mechanical", "count": 1}]},
        "试装": {"required_fields": ["custom_fields.material"], "approvals": []},
        "已完成": {"required_fields": ["custom_fields.inspection_result"], "approvals": []},
    },
    recommend_units=("MET / CVD / Mechanical Engineering",),
)

PRODUCTION = Department(
    key="PROD", name="生产执行（演示）", profile="production", color="#30d158",
    description="演示项目：生产工单从分派、排产、在制到质检完工的流程，含产线主数据与质量结果。",
    lead_unit="MET / Manufacture / Planning", team_role="production",
    team_units=("MET / Manufacture / MFG", "MET / Manufacture / Planning"),
    verify_role="quality",
    verify_units=("MET / Manufacture / QC", "MET / Quality / Process Quality"),
    requester_units=("MET / Supply Chain", "MET / Finance / Warehouse",
                     "MET / Account Team / Services"),
    projects=("2026Q4 出货", "客户 C 机台", "精益产线 Wave-2", "新洁净线导入"),
    requirements=(
        "Orion-1 整机总装", "Aurora-3 腔体组装", "EFEM 模块预装", "PM-A 加热盘更换",
        "客户 C 出货前整改", "备件装箱发运", "气路检漏返工", "线束批量制作",
        "Q4 批次 ALD 腔体清洗", "LL 模块老化", "机台出厂 FAT", "洁净室搬入",
        "TM 模块预装", "气体柜就位", "二次配管", "整机通电调试", "射频模块装配",
        "备件安全库存补货", "装配线 5S 改善", "客户现场装机", "Orion-2 返修",
        "腔体抛光外协", "真空泵批量测试", "出货资料归档",
    ),
    bugs=("批次 B2610 密封圈来料不良", "线束标签错印", "总装扭矩记录缺失", "包装箱防潮不达标"),
    tasks=("更新 SOP-ASM-014", "盘点在制品 WIP", "周末加班排程", "工装夹具点检"),
    epics=("2026Q4 出货计划", "精益产线 Wave-2", "新洁净线导入"),
    subtasks=("领料", "装配", "自检", "转质检"),
    gates={
        "已排产": {"required_fields": [],
                 "approvals": [{"role": "production", "count": 1}]},
        "在制": {"required_fields": ["custom_fields.line", "custom_fields.planned_date"],
                 "approvals": []},
        "完工": {"required_fields": ["custom_fields.quality_result",
                                     "custom_fields.actual_quantity"],
                 "approvals": [{"role": "quality", "count": 1}]},
    },
    recommend_units=("MET / Manufacture",),
)

# Previous showcase keys — removed on --reset so staging never keeps stale DEMO* projects.
LEGACY_DEMO_KEYS = ("DEMOE", "DEMOM", "DEMOP")
DEPARTMENTS = (ELECTRICAL, MECHANICAL, PRODUCTION)
MATERIALS = [("AL6061-T6", "铝合金 6061-T6"), ("SUS304", "不锈钢 304"), ("SUS316L", "不锈钢 316L"),
             ("PEEK", "聚醚醚酮 PEEK"), ("TI-6AL-4V", "钛合金 TC4"), ("AL2O3", "氧化铝陶瓷")]
LINES = [("LINE-A1", "总装线 A1"), ("LINE-A2", "总装线 A2"), ("LINE-M1", "模块装配线 M1"),
         ("CLEAN-1", "洁净装配间 1")]
LABELS = ["客户反馈", "安全", "产能", "降本", "质量", "紧急"]
VERIFY_METHODS = ["上机验证 + 72 小时稳定性运行", "模拟信号注入测试", "对比升级前后报警日志",
                  "工艺跑片验证 25 片", "第三方安全检测"]
CRITERIA = ["连续 72 小时无相关报警", "响应时间 < 200ms", "跑片良率 ≥ 99.5%",
            "互锁动作 100% 正确", "数据与上位机一致"]
BACKGROUNDS = ["现场反馈该问题影响产能，近两周出现 {n} 次。",
               "客户审核时提出，需要在下次出货前完成。", "为满足新版安全规范，需要整改。",
               "降本项目的一部分，目标降低 {n}% 成本。",
               "工艺部门希望提升稳定性，减少人工干预。"]


# ---------------------------------------------------------------- safety


def check_database(allow_sqlite_tmp: bool) -> None:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() == "sqlite":
        if allow_sqlite_tmp and str(url.database or "").startswith("/tmp/"):
            return
        sys.exit("Refusing to seed a sqlite database outside /tmp")
    if url.database != "met_jira_staging":
        sys.exit(f"Refusing to seed database {url.database!r}: only met_jira_staging is allowed")


def silence_outbound() -> None:
    from backend.services import alerts, reminders

    alerts.send = lambda *args, **kwargs: False
    alerts.push_wecom = lambda *args, **kwargs: False
    alerts.send_mail = lambda *args, **kwargs: {"configured": False, "sent": 0, "failed": 0}
    reminders.queue = lambda *args, **kwargs: None


# ---------------------------------------------------------------- templates


def _comparable(definition: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in definition.items() if k != "version_id"}


def apply_latest_templates(db: Session, admin: User, say: Callable[[str], None]) -> None:
    """Publish the code's built-in definitions as new versions and migrate projects onto them."""
    from backend.routers import templates as api

    for template in templates.catalog(db, enabled_only=False):
        profile = profiles.PROFILES.get(template.key)
        if profile is None:
            continue
        latest = templates.validate_definition(profiles.builtin_definition(profile))
        current = template.current_version
        if current is None or _comparable(current.definition) != _comparable(latest):
            draft = api.create_draft(template.id, {}, admin, db)
            api.save_draft(template.id, draft["id"],
                           {"revision": draft["revision"], "definition": latest}, admin, db)
            api.publish(template.id, draft["id"], admin, db)
            db.refresh(template)
            say(f"template {template.key}: published v{template.current_version.version}")
        else:
            say(f"template {template.key}: v{current.version} is already the latest")
        target = template.current_version
        version_ids = select(DepartmentTemplateVersion.id).where(
            DepartmentTemplateVersion.template_id == template.id)
        for project in db.scalars(select(Project).where(
                Project.template_version_id.in_(version_ids),
                Project.template_version_id != target.id)):
            migrate_project(db, project, target, admin)
            say(f"project {project.key}: migrated to {template.key} v{target.version}")


def migrate_project(db: Session, project: Project, target: DepartmentTemplateVersion,
                    admin: User) -> None:
    from backend.services import template_migration

    snapshot = template_migration.preview(db, project, target)
    target_names = {s["name"] for s in snapshot["target_statuses"]}
    mapping = {
        "status_map": {str(row["id"]): (row["name"] if row["name"] in target_names
                                        else row["suggested"])
                       for row in snapshot["statuses"]},
        "field_map": {row["key"]: row["suggested"] or "orphan"
                      for row in snapshot["fields"] if row["usage"]},
        "role_map": {role: "member" for role in snapshot["invalid_roles"]},
    }
    template_migration.apply(db, project, target, mapping, admin.id)
    db.commit()


# ---------------------------------------------------------------- people & master data


class People:
    def __init__(self, db: Session, rng: random.Random):
        self.db, self.rng = db, rng
        units = db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False)))
        self.units = {u.path: u for u in units}
        self.used: set[int] = set()

    def pick(self, unit_paths: tuple[str, ...], count: int, tag: str) -> list[User]:
        unit_ids = {u.id for path in unit_paths for p, u in self.units.items()
                    if p == path or p.startswith(path + " / ")}
        pool = list(self.db.scalars(select(User).where(
            User.org_unit_id.in_(unit_ids), User.enabled.is_(True), User.is_admin.is_(False))
            .order_by(User.username))) if unit_ids else []
        pool = [u for u in pool if u.id not in self.used]
        chosen = self.rng.sample(pool, min(count, len(pool)))
        for index in range(len(chosen), count):
            chosen.append(self._local(f"demo.{tag}{index + 1}"))
        self.used.update(u.id for u in chosen)
        return chosen

    def _local(self, username: str) -> User:
        user = self.db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, display_name=username.replace("demo.", "演示 "),
                        auth_source="local", enabled=True)
            self.db.add(user)
            self.db.flush()
        return user


def ensure_assets(db: Session) -> dict[str, list[MasterAsset]]:
    out: dict[str, list[MasterAsset]] = {"material": [], "line": []}
    for kind, rows in (("material", MATERIALS), ("line", LINES)):
        for code, name in rows:
            asset = db.scalar(select(MasterAsset).where(MasterAsset.kind == kind,
                                                        MasterAsset.code == code))
            if asset is None:
                asset = MasterAsset(kind=kind, code=code, name=name, active=True, attributes={})
                db.add(asset)
                db.flush()
            out[kind].append(asset)
    db.commit()
    return out


def ensure_demo_org_units(db: Session) -> None:
    """Create the department paths the showcase references when LDAP sync is empty."""
    needed = sorted({path for dept in DEPARTMENTS for path in (
        dept.lead_unit, *dept.team_units, *dept.verify_units, *dept.requester_units,
        *dept.owner_units, *dept.recommend_units)})
    existing = {u.path: u for u in db.scalars(select(OrgUnit)) if u.path}
    for path in needed:
        parts = path.split(" / ")
        cursor = None
        built: list[str] = []
        for part in parts:
            built.append(part)
            cur_path = " / ".join(built)
            if cur_path in existing:
                cursor = existing[cur_path]
                continue
            unit = OrgUnit(
                dn=f"OU=demo,{cur_path}",
                name=part,
                display_name=part,
                path=cur_path,
                parent_id=cursor.id if cursor else None,
                level=len(built) - 1,
                removed=False,
            )
            db.add(unit)
            db.flush()
            existing[cur_path] = unit
            cursor = unit
    db.commit()


def recommend_templates(db: Session, say: Callable[[str], None]) -> None:
    ensure_demo_org_units(db)
    units = {u.path: u for u in db.scalars(select(OrgUnit))}
    for dept in DEPARTMENTS:
        template = next((t for t in templates.catalog(db) if t.key == dept.profile), None)
        for path in dept.recommend_units:
            unit = units.get(path)
            if unit is None or template is None or db.scalar(
                    select(OrgUnitTemplateRecommendation.id)
                    .where(OrgUnitTemplateRecommendation.org_unit_id == unit.id)):
                continue
            db.add(OrgUnitTemplateRecommendation(org_unit_id=unit.id, template_id=template.id,
                                                 sort_order=0))
            say(f"recommend {dept.profile} template for {path}")
    db.commit()


# ---------------------------------------------------------------- simulation model


@dataclass
class Plan:
    type: str
    title: str
    path: list[str]
    created: datetime
    finish: datetime
    priority: str = "medium"
    points: float | None = None
    due: date | None = None
    assigned: bool = True
    epic: Plan | None = None
    parent: Plan | None = None
    sprint: str = "auto"  # auto | backlog | future
    reject_approval_first: bool = False
    pending_approval: str | None = None
    comments: list[tuple[datetime, str, str]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    assignee: User | None = None
    tester: User | None = None
    reporter: User | None = None
    owner: User | None = None
    issue: Issue | None = None


class Demo:
    """One demo project: people, plans and the time-ordered replay through the services."""

    def __init__(self, db: Session, dept: Department, admin: User, people: People,
                 assets: dict[str, list[MasterAsset]], rng: random.Random,
                 say: Callable[[str], None]):
        self.db, self.dept, self.admin, self.rng, self.say = db, dept, admin, rng, say
        self.people, self.assets = people, assets
        self.events: list[tuple[datetime, int, Callable[[datetime], None]]] = []
        self.plans: list[Plan] = []
        self.sprints: list[Sprint] = []
        self.counter = 0

    # -- setup

    def create_project(self) -> None:
        from backend.routers.projects import create_project
        from backend.schemas import ProjectIn

        dept = self.dept
        self.lead = self.people.pick((dept.lead_unit,), 1, f"{dept.key.lower()}lead")[0]
        self.team = self.people.pick(dept.team_units, 6, f"{dept.key.lower()}team")
        self.verifiers = self.people.pick(dept.verify_units, 3, f"{dept.key.lower()}qa")
        self.requesters = self.people.pick(dept.requester_units, 6, f"{dept.key.lower()}req")
        self.owners = self.people.pick(dept.owner_units, 2, f"{dept.key.lower()}sw") \
            if dept.owner_units else []
        create_project(ProjectIn(
            key=dept.key,
            name=dept.name,
            description=dept.description,
            lead_id=self.lead.id,
            color=dept.color,
            with_machines=True,
            profile=dept.profile,
            plan_start_date=TODAY - timedelta(days=28),
            target_date=TODAY + timedelta(days=28),
        ), self.admin, self.db)
        self.project = self.db.scalar(select(Project).where(Project.key == dept.key))
        # Lead is already project-admin from create_project; also give them the team role so
        # they can satisfy countersign gates that ask for that role.
        members = [(self.admin, "admin"), (self.lead, dept.team_role)] + [
            (u, dept.team_role) for u in self.team] + [
            (u, dept.verify_role) for u in self.verifiers] + [
            (u, "requester") for u in self.requesters] + [(u, "member") for u in self.owners]
        for user, role in members:
            if not self.db.scalar(select(ProjectMember.id).where(
                    ProjectMember.project_id == self.project.id, ProjectMember.user_id == user.id)):
                self.db.add(ProjectMember(project_id=self.project.id, user_id=user.id, role=role))
        settings = dict(self.project.settings or {})
        settings["transition_gates"] = {**(settings.get("transition_gates") or {}), **dept.gates}
        settings["sla_calendar"] = "business"
        settings["sla_business_hours"] = {
            "start": "09:00",
            "end": "18:00",
            "workdays": [1, 2, 3, 4, 5],
        }
        self.project.settings = settings
        for kind in ("material", "line"):
            for asset in self.assets[kind]:
                self.db.add(ProjectAsset(project_id=self.project.id, asset_id=asset.id))
        self.db.commit()
        self.statuses = {s.name: s for s in workflow.project_statuses(self.db, self.project.id)}
        self.machines = list(self.db.scalars(select(Machine.id).join(MachineGroup).where(
            MachineGroup.project_id == self.project.id)))
        self.owners = [u for u in self.owners
                       if software.is_software_member(self.db, self.project, u)]

    def main_line(self, issue_type: str) -> list[str]:
        """Forward path of a type: follow the first allowed target until a done status."""
        rules = workflow.transition_rules(self.project, issue_type) or {}
        start = workflow.initial_status(self.project, issue_type, list(self.statuses.values()))
        path, current = [], start.name
        while rules.get(current):
            current = rules[current][0]
            if current in path or current == start.name:
                break
            path.append(current)
            if self.statuses[current].category == "done":
                break
        return path

    # -- time helpers

    def at(self, day: float, hour: float | None = None) -> datetime:
        base = datetime.combine(TODAY + timedelta(days=int(day)), datetime.min.time(), LOCAL)
        hours = hour if hour is not None else self.rng.uniform(9, 18)
        return base + timedelta(hours=hours)

    def schedule(self, when: datetime, action: Callable[[datetime], None]) -> None:
        self.counter += 1
        self.events.append((when, self.counter, action))

    # -- planning

    def plan(self) -> None:
        dept, rng = self.dept, self.rng
        req_line = self.main_line("requirement")
        done_name = req_line[-1]
        verify = req_line[-2] if len(req_line) > 1 else req_line[-1]
        titles = list(dept.requirements)
        rng.shuffle(titles)

        def take() -> str:
            if not titles:
                titles.extend(f"{name}（二期）" for name in dept.requirements)
                rng.shuffle(titles)
            return titles.pop()

        epics = []
        for index, title in enumerate(dept.epics):
            epic_line = self.main_line("epic")
            created = self.at(-56 + index)
            path = (epic_line if index == 0 else epic_line[:1] if index == 1 else [])
            finish = self.at(-4) if index == 0 else self.at(-40 + index * 3)
            epics.append(self.add(Plan("epic", title, path, created, finish, priority="high",
                                       sprint="backlog", due=TODAY + timedelta(days=30 * index))))

        def requirement(path: list[str], created: datetime, finish: datetime, **extra) -> Plan:
            plan = Plan("requirement", take(), path, created, finish, **extra)
            plan.epic = plan.epic or (epics[rng.randrange(len(epics))] if rng.random() < 0.8
                                      else None)
            if plan.epic is epics[0] and done_name not in path:
                plan.epic = epics[1]
            return self.add(plan)

        # Finished work spread over seven weeks gives the throughput, velocity and forecast.
        for index in range(13):
            finish = self.at(-46 + index * 3.4)
            requirement(req_line, finish - timedelta(days=rng.uniform(5, 16)), finish,
                        epic=epics[0] if index < 4 else None)
        # Work in every intermediate stage, some overdue and some due soon.
        for depth in range(1, len(req_line)):
            for _ in range(3 if depth < len(req_line) - 1 else 2):
                created = self.at(rng.uniform(-30, -4))
                requirement(req_line[:depth], created, self.at(rng.uniform(-3, 0)))
        # Fresh requests, two still unassigned, one urgent.
        for index in range(5):
            created = self.at(-rng.uniform(0, 6))
            requirement([], created, created, assigned=index >= 2,
                        priority="highest" if index == 0 else rng.choice(["high", "medium"]),
                        sprint="future" if index == 4 else "backlog")
        # Rejected requests (one as a duplicate) and one stopped after the first step.
        reject = workflow.REJECTED if workflow.REJECTED in self.statuses else None
        if reject:
            for index in range(2):
                created = self.at(rng.uniform(-40, -10))
                plan = requirement([reject], created, created + timedelta(days=1.5))
                plan.comments.append((plan.finish - timedelta(hours=1), "lead",
                                      "与现有需求重复，合并处理。" if index == 0 else
                                      "收益不足以覆盖改造成本，暂不实施。"))
            created = self.at(-25)
            requirement([req_line[0], reject], created, created + timedelta(days=4))
        # Approval states: waiting, rejected then approved.
        gated = next((name for name, gate in dept.gates.items() if gate.get("approvals")), None)
        if gated and gated in req_line:
            before = req_line[:req_line.index(gated)]
            for _ in range(2):
                created = self.at(rng.uniform(-8, -2))
                requirement(before, created, self.at(-1), pending_approval=gated)
            created = self.at(-20)
            requirement(req_line[:req_line.index(gated) + 1], created, self.at(-12),
                        reject_approval_first=True)

        # Electrical-only rework: failed tests, reopened work.
        if "测试未通过" in self.statuses:
            rework = req_line[:-1] + ["测试未通过", workflow.REOPENED, "研发中", verify, done_name]
            for index in range(3):
                finish = self.at(-30 + index * 9)
                requirement(rework, finish - timedelta(days=14), finish)
            for _ in range(2):
                requirement(req_line[:-1] + ["测试未通过"], self.at(-15), self.at(-1))
            requirement(req_line[:-1] + ["测试未通过", workflow.REOPENED],
                        self.at(-18), self.at(-2))
            requirement(req_line + [workflow.REOPENED], self.at(-24), self.at(-1))

        # Other issue types.
        for kind, pool, counts in (("bug", dept.bugs, (4, 2, 2)), ("task", dept.tasks, (3, 2, 1)),
                                   ("story", dept.stories, (1, 1, 1))):
            line = self.main_line(kind)
            names = list(pool)
            rng.shuffle(names)
            done_count, doing_count, new_count = counts
            for index, title in enumerate(names[:done_count + doing_count + new_count]):
                if index < done_count:
                    finish = self.at(rng.uniform(-40, -2))
                    path, created = line, finish - timedelta(days=rng.uniform(2, 8))
                elif index < done_count + doing_count:
                    created, finish = self.at(rng.uniform(-14, -3)), self.at(rng.uniform(-2, 0))
                    path = line[:max(1, len(line) - 2)]
                else:
                    created = self.at(-rng.uniform(0, 4))
                    path, finish = [], created
                self.add(Plan(kind, title, path, created, finish,
                              priority="high" if kind == "bug" else "medium",
                              sprint="auto" if path else "backlog"))

        # Sub-tasks under a few requirements that are underway or done.
        sub_line = self.main_line("subtask")
        parents = [p for p in self.plans if p.type == "requirement" and len(p.path) >= 2][:5]
        for parent in parents:
            for index, title in enumerate(rng.sample(list(dept.subtasks), 2)):
                start = parent.created + (parent.finish - parent.created) * 0.3
                finish = parent.created + (parent.finish - parent.created) * (0.6 + index * 0.15)
                parent_done = self.statuses[parent.path[-1]].category == "done"
                path = sub_line if parent_done or index == 0 else sub_line[:1]
                self.add(Plan("subtask", title, path, start, finish, parent=parent,
                              sprint="backlog", assigned=True))

        for plan in self.plans:
            self.finalize(plan)

    def add(self, plan: Plan) -> Plan:
        self.plans.append(plan)
        return plan

    def finalize(self, plan: Plan) -> None:
        rng = self.rng
        plan.created = min(plan.created, NOW - timedelta(hours=2))
        plan.finish = min(max(plan.finish, plan.created + timedelta(hours=2)),
                          NOW - timedelta(minutes=30))
        plan.reporter = rng.choice(self.requesters)
        plan.assignee = rng.choice(self.team) if plan.assigned else None
        plan.tester = rng.choice(self.verifiers)
        plan.owner = rng.choice(self.owners) if self.owners and rng.random() < 0.3 else None
        if plan.points is None and plan.type in {"requirement", "story", "task", "bug"}:
            plan.points = rng.choice([1, 2, 3, 3, 5, 5, 8])
        if plan.priority == "medium" and plan.type == "requirement":
            plan.priority = rng.choice(["highest", "high", "high", "medium", "medium", "low",
                                        "lowest"])
        plan.labels = rng.sample(LABELS, rng.choice([0, 1, 1, 2]))
        done = plan.path and self.statuses[plan.path[-1]].category == "done"
        if plan.due is None and plan.type != "subtask":
            if done:
                plan.due = plan.finish.date() + timedelta(days=rng.choice([-4, -1, 0, 2, 5, 9]))
            else:
                plan.due = TODAY + timedelta(days=rng.choice([-9, -4, -2, 0, 1, 3, 6, 12, 20]))
        if plan.comments == [] and plan.path and rng.random() < 0.7:
            mid = plan.created + (plan.finish - plan.created) * rng.uniform(0.2, 0.8)
            plan.comments.append((mid, "assignee", rng.choice([
                "@{lead} 方案初稿已整理，请帮忙看一下。", "已和工艺确认，按方案 B 执行。",
                "物料预计下周三到货，到货后马上处理。",
                "@{reporter} 需要补充一下现场照片和报警截图。",
                "进度正常，预计本周内提交验证。"])))
        if done and rng.random() < 0.6:
            plan.comments.append((plan.finish - timedelta(hours=2), "tester", rng.choice([
                "验证通过，数据符合验收标准。", "已上机验证 3 天，无异常。",
                "首件检验合格，可以关闭。"])))
        if not done and plan.due and plan.due < TODAY and plan.path:
            plan.comments.append((plan.finish, "lead", "@{assignee} 已逾期，请更新预计完成时间。"))

    # -- replay

    def build_events(self) -> None:
        for index, (start, end) in enumerate(SPRINT_WINDOWS):
            sprint = Sprint(project_id=self.project.id, name=f"{self.dept.key} 冲刺 {index + 1}",
                            goal=["完成需求受理与方案评审", "推进研发与上机验证",
                                  "清理逾期与测试问题", "交付本期重点需求", "下期规划"][index],
                            start_date=TODAY + timedelta(days=start),
                            end_date=TODAY + timedelta(days=end), state="future")
            self.db.add(sprint)
            self.sprints.append(sprint)
        self.db.commit()
        for index, (start, end) in enumerate(SPRINT_WINDOWS):
            begin, finish = self.at(start, 9 if index == 0 else 17.75), self.at(end, 17.5)
            if begin < NOW:
                self.schedule(begin, lambda when, i=index: self.start_sprint(i, when))
            if finish < NOW:
                self.schedule(finish, lambda when, i=index: self.complete_sprint(i, when))
        for plan in self.plans:
            self.schedule(plan.created, lambda when, p=plan: self.create(p, when))
            steps = len(plan.path)
            span = plan.finish - plan.created
            for index, status in enumerate(plan.path):
                when = plan.created + span * ((index + 1) / steps)
                jitter = span / (steps * 6) * self.rng.uniform(-1, 1) if index < steps - 1 \
                    else timedelta(0)
                self.schedule(when + jitter, lambda w, p=plan, s=status: self.move(p, s, w))
            if plan.pending_approval:
                self.schedule(plan.finish + timedelta(minutes=20),
                              lambda w, p=plan: self.request_approval(p, w))
            for when, who, text in plan.comments:
                self.schedule(max(when, plan.created + timedelta(minutes=5)),
                              lambda w, p=plan, a=who, t=text: self.comment(p, a, t, w))

    def run(self) -> None:
        for when, _seq, action in sorted(self.events, key=lambda e: (e[0], e[1])):
            if when > NOW:
                continue
            marks = {model: self.db.scalar(select(func.max(model.id))) or 0 for model in STAMPED}
            action(when)
            self.db.flush()
            for model, mark in marks.items():
                self.db.execute(update(model).where(model.id > mark).values(created_at=when))
            self.db.execute(update(ApprovalRequest).where(
                ApprovalRequest.id > marks[ApprovalRequest],
                ApprovalRequest.finished_at.is_not(None)).values(finished_at=when))
            self.db.commit()
        self.links()
        self.finish_sla()

    # -- actions

    def user(self, plan: Plan, who: str) -> User:
        return {"lead": self.lead, "assignee": plan.assignee or self.lead,
                "tester": plan.tester or self.lead, "reporter": plan.reporter or self.lead,
                }.get(who, self.lead)

    def as_user(self, actor: User, action: Callable[[User], Any]) -> Any:
        try:
            return action(actor)
        except IssueError as error:
            if error.status_code != 403 or actor.id == self.admin.id:
                raise
            return action(self.admin)

    def current_sprint(self, when: datetime) -> Sprint | None:
        open_sprints = [s for s in self.sprints if s.state != "closed"]
        active = next((s for s in open_sprints if s.state == "active"), None)
        return active or (open_sprints[0] if open_sprints else None)

    def start_sprint(self, index: int, when: datetime) -> None:
        from backend.routers.sprints import start_sprint
        from backend.schemas import SprintUpdate

        sprint = self.sprints[index]
        start_sprint(sprint.id, SprintUpdate(), self.lead, self.db)
        sprint.started_at = when

    def complete_sprint(self, index: int, when: datetime) -> None:
        from backend.routers.sprints import complete_sprint
        from backend.schemas import SprintComplete

        sprint = self.sprints[index]
        following = self.sprints[index + 1].id if index + 1 < len(self.sprints) else None
        complete_sprint(sprint.id, SprintComplete(move_open_to=following), self.lead, self.db)
        sprint.completed_at = when

    def create(self, plan: Plan, when: datetime) -> None:
        dept, rng = self.dept, self.rng
        data: dict[str, Any] = {
            "type": plan.type, "title": plan.title, "priority": plan.priority,
            "description": self.description(plan), "due_date": plan.due,
            "labels": plan.labels, "story_points": plan.points, "created_at": when,
            "reporter_id": plan.reporter.id,
        }
        if plan.type != "epic":
            data["project_name"] = rng.choice(dept.projects)
            if self.machines and plan.type != "subtask":
                data["machine_ids"] = rng.sample(self.machines, min(len(self.machines),
                                                                    rng.choice([1, 1, 2])))
        # Match create-form required fields for electrical requirements (impact scope).
        if plan.type == "requirement" and dept.profile == "electrical":
            options = (self.project.settings or {}).get("update_scope_options") or list(
                workflow.DEFAULT_UPDATE_SCOPES)
            data["update_scope"] = rng.sample(options, rng.choice([1, 2]))
        if plan.assignee and (plan.path or rng.random() < 0.6):
            data["assignee_id"] = plan.assignee.id
        if plan.epic and plan.epic.issue:
            data["epic_id"] = plan.epic.issue.id
        if plan.parent:
            if plan.parent.issue is None:
                return
            data["parent_id"] = plan.parent.issue.id
            data.pop("due_date", None)
        sprint = None
        if plan.sprint == "auto" and plan.type not in {"epic", "subtask"}:
            sprint = self.current_sprint(when)
        elif plan.sprint == "future":
            sprint = self.sprints[-1]
        if sprint is not None:
            data["sprint_id"] = sprint.id
        custom = self.custom_values(plan, "create")
        if custom:
            data["custom_fields"] = custom
        plan.issue = create_issue(self.db, self.project, plan.reporter, data)
        for uid in {self.lead.id, *(u.id for u in rng.sample(self.team, 1))}:
            watch(self.db, plan.issue.id, uid)
        plan.issue.updated_at = when

    def move(self, plan: Plan, status_name: str, when: datetime) -> None:
        issue = plan.issue
        if issue is None or plan.parent and plan.parent.issue is None:
            return
        self.db.refresh(issue)
        target = self.statuses[status_name]
        if issue.status_id == target.id:
            return
        self.fill(plan, target)
        actor = self.actor_for(plan, issue.status, target)
        changes: dict[str, Any] = {"status_id": target.id}
        if target.name == workflow.REOPENED:
            changes["reopen_reason"] = self.rng.choice([
                "测试未通过，需返工修复",
                "现场复现，方案需调整",
                "验收标准未达标，重新打开",
            ])
        try:
            self.as_user(actor, lambda user: update_issue(
                self.db, issue, user, changes, close_subtasks=True))
        except IssueError as error:
            if error.code != "approval_required":
                self.say(f"  ! {issue.key} {issue.status.name} -> {target.name}: {error.message}")
                return
            self.approve(plan, target, actor, reject_first=plan.reject_approval_first)
        issue.updated_at = when
        issue.resolved_at = when if issue.status.category == "done" else None
        if issue.status.category == "done" and issue.type != "subtask":
            issue.sla_state = "completed"

    def actor_for(self, plan: Plan, current: Status, target: Status) -> User:
        pass_name = (self.project.settings or {}).get("pass_status") or workflow.TEST_PASSED
        if current.name == pass_name and target.name in {"已完成", workflow.REJECTED}:
            return plan.assignee or self.lead
        if target.name == workflow.REJECTED:
            return self.lead
        if current.name in {"测试中", "终检", "质检"}:
            return plan.tester or self.lead
        if target.name == workflow.REOPENED and current.category == "done":
            return plan.reporter or self.lead
        return plan.assignee or self.lead

    def approve(self, plan: Plan, target: Status, requester: User, *, reject_first: bool) -> None:
        issue = plan.issue
        row = approvals.request(self.db, issue, target, requester, "方案已整理，申请评审。")
        approver = self.approver(target)
        if reject_first:
            approvals.decide(self.db, row, issue, approver, False, "请补充风险评估后再提交。")
            self.db.flush()
            row = approvals.request(self.db, issue, target, requester,
                                    "已补充风险评估，请再次审批。")
        if approvals.decide(self.db, row, issue, approver, True, "同意。"):
            update_issue(self.db, issue, approver, {"status_id": target.id}, enforce=False)

    def approver(self, target: Status) -> User:
        roles = {r["role"] for r in (self.dept.gates.get(target.name) or {}).get("approvals", [])}
        if "quality" in roles and self.verifiers:
            return self.verifiers[0]
        if self.dept.verify_role in roles and self.verifiers:
            return self.verifiers[0]
        if self.dept.team_role in roles and self.team:
            return self.team[0]
        return self.lead

    def request_approval(self, plan: Plan, when: datetime) -> None:
        if plan.issue is None or plan.pending_approval is None:
            return
        target = self.statuses[plan.pending_approval]
        self.fill(plan, target)
        try:
            approvals.request(self.db, plan.issue, target, plan.assignee or self.lead,
                              "方案已整理完毕，请审批。")
        except IssueError as error:
            self.say(f"  ! {plan.issue.key} approval request: {error.message}")

    def comment(self, plan: Plan, who: str, text: str, when: datetime) -> None:
        if plan.issue is None:
            return
        names = {"lead": self.lead.username, "assignee": (plan.assignee or self.lead).username,
                 "reporter": (plan.reporter or self.lead).username}
        body = text.format(**names)
        add_comment(self.db, plan.issue, self.user(plan, who), body)

    # -- field values

    def fill(self, plan: Plan, target: Status) -> None:
        """Fill what the target needs (gates, tester) plus the section that owns this stage."""
        issue = plan.issue
        line = self.main_line(issue.type)
        stage = line.index(target.name) if target.name in line else -1
        sections = profiles.profile_of(self.project).sections
        wanted: set[str] = set(approvals.missing_fields(issue, target))
        if stage >= 0 and len(sections) > 1:
            wanted.update(sections[1].fields)
        if stage >= len(line) - 2:
            for section in sections:
                if section.key in {"tester", "software", "inspection", "quality"}:
                    if section.key == "software" and not (issue.involves_software or plan.owner):
                        continue
                    wanted.update(section.fields)
        changes: dict[str, Any] = {}
        custom: dict[str, Any] = {}
        for key in sorted(wanted):
            name = key.removeprefix("custom_fields.")
            if profiles.profile_of(self.project).custom_field(name):
                if (issue.extra or {}).get(name) in (None, "", []):
                    value = self.custom_value(name, plan)
                    if value is not None:
                        custom[name] = value
                continue
            if approvals.value_of(issue, name) not in (None, "", [], False):
                continue
            value = self.builtin_value(name, plan)
            if value is not None:
                changes[name] = value
        test_status = (self.project.settings or {}).get("test_status")
        if target.name == test_status and issue.tester_id is None:
            changes["tester_id"] = plan.tester.id
        if changes.get("software_owner_id") and not changes.get("involves_software"):
            changes["involves_software"] = True
        if custom:
            changes["custom_fields"] = custom
        if not changes:
            return
        actor = plan.tester if stage >= len(line) - 2 else (plan.assignee or self.lead)
        try:
            self.as_user(actor, lambda user: update_issue(self.db, issue, user, changes))
        except IssueError as error:
            update_issue(self.db, issue, self.admin, changes, enforce=False)
            self.say(f"  ~ {issue.key} fields via admin: {error.message}")

    def builtin_value(self, key: str, plan: Plan) -> Any:
        rng = self.rng
        options = (self.project.settings or {}).get("update_scope_options") or ["硬件", "软件"]
        values: dict[str, Callable[[], Any]] = {
            "update_scope": lambda: rng.sample(options, rng.choice([1, 2])),
            "verify_method": lambda: rng.choice(VERIFY_METHODS),
            "verify_criteria": lambda: rng.choice(CRITERIA),
            "tester_id": lambda: plan.tester.id,
            "remark": lambda: rng.choice(["", "已同步更新图纸与程序版本。",
                                          "需要在下次 PM 时复查。"]),
            "involves_software": lambda: bool(plan.owner),
            "software_owner_id": lambda: plan.owner.id if plan.owner else None,
            "software_ready": lambda: True if plan.owner else None,
            "software_note": lambda: "演示：软件已按约定版本交付" if plan.owner else None,
            "assignee_id": lambda: plan.assignee.id if plan.assignee else None,
            "project_name": lambda: rng.choice(self.dept.projects),
        }
        maker = values.get(key)
        return maker() if maker else None

    def custom_values(self, plan: Plan, stage: str) -> dict[str, Any]:
        profile = profiles.profile_of(self.project)
        out = {}
        for item in profile.custom_fields:
            if item.required and plan.type != "epic":
                out[item.key] = self.custom_value(item.key, plan)
        return {k: v for k, v in out.items() if v is not None}

    def custom_value(self, key: str, plan: Plan) -> Any:
        rng = self.rng
        number = (plan.issue.number if plan.issue else rng.randint(1, 99))
        start = plan.created.date()
        values: dict[str, Callable[[], Any]] = {
            "drawing_no": lambda: f"MD-2610-{number:03d}",
            "part_no": lambda: f"P-{rng.randint(10000, 99999)}",
            "material": lambda: rng.choice(self.assets["material"]).id,
            "tolerance": lambda: rng.choice(["IT6", "IT7", "IT8", "±0.05mm", "±0.1mm"]),
            "drawing_url": lambda: f"https://plm.example.local/drawings/MD-2610-{number:03d}",
            "trial_result": lambda: rng.choice(["试装顺利，配合间隙符合要求。",
                                                "需修磨 2 处倒角后装配。",
                                                "与相邻部件轻微干涉，已调整孔位。"]),
            "inspection_result": lambda: rng.choice(["通过", "通过", "通过", "有条件通过"]),
            "inspection_date": lambda: plan.finish.date().isoformat(),
            "work_order": lambda: f"WO-2610{number:02d}",
            "batch_no": lambda: f"B2610-{rng.randint(1, 9):02d}",
            "line": lambda: rng.choice(self.assets["line"]).id,
            "planned_date": lambda: (start + timedelta(days=rng.randint(2, 10))).isoformat(),
            "actual_quantity": lambda: rng.choice([1, 2, 4, 6, 10, 12]),
            "quality_result": lambda: rng.choice(["通过", "通过", "通过", "待复验"]),
            "quality_note": lambda: rng.choice(["外观、功能检验合格。", "扭矩记录完整，抽检合格。",
                                                "需复验密封性能，已安排。"]),
            "quality_date": lambda: plan.finish.date().isoformat(),
        }
        maker = values.get(key)
        return maker() if maker else None

    def description(self, plan: Plan) -> str:
        rng = self.rng
        if plan.type == "epic":
            return (f"## 目标\n{plan.title}，覆盖本季度相关需求与改进。\n\n"
                    "## 范围\n- 方案评审\n- 研发与验证\n- 上线与培训")
        background = rng.choice(BACKGROUNDS).format(n=rng.randint(2, 15))
        return (f"## 背景\n{background}\n\n"
                f"## 期望\n{plan.title}，完成后验证效果并更新相关文档。\n\n"
                f"## 备注\n- 影响范围：{rng.choice(['单机台', '同型号全部机台', '新出货机台'])}\n"
                f"- 联系人：@{plan.reporter.username}")

    # -- finishing touches

    def links(self) -> None:
        from backend.routers.issues import add_link
        from backend.schemas import LinkIn

        issues = [p.issue for p in self.plans if p.issue is not None and p.type != "subtask"]
        rejected = [i for i in issues if i.status.name == workflow.REJECTED]
        open_items = [i for i in issues if i.status.category != "done" and i.type != "epic"]
        pairs: list[tuple[Issue, Issue, str, int]] = []
        if rejected and len(issues) > 3:
            pairs.append((rejected[0], issues[3], "duplicates", 1))
        chains = [
            open_items[index:index + 5]
            for index in range(0, min(10, len(open_items)), 5)
            if len(open_items[index:index + 5]) == 5
        ]
        for chain in chains:
            pairs.extend(
                (source, target, "blocks", (position % 3) + 1)
                for position, (source, target) in enumerate(zip(
                    chain, chain[1:], strict=False
                ))
            )
        if len(chains) > 1:
            # The release Gate needs both the main build and the parallel tooling stream.
            pairs.append((chains[0][2], chains[1][-2], "blocks", 2))
        for left, right in zip(open_items[10:16], issues[:6], strict=False):
            if left.id != right.id:
                pairs.append((left, right, "relates", 1))
        for source, target, kind, lag_days in pairs:
            try:
                add_link(
                    source.key,
                    LinkIn(target_key=target.key, link_type=kind, lag_days=lag_days),
                    self.lead,
                    self.db,
                )
            except Exception as error:  # noqa: BLE001 - a link is decoration, never fatal
                self.say(f"  ! link {source.key} {kind} {target.key}: {error}")
        self.db.commit()
        self.sequence_baseline(chains)

    def sequence_baseline(self, chains: list[list[Issue]]) -> None:
        """Create two readable dependency chains, lock a baseline, then slip their sources."""
        from backend.routers.projects import _baseline_material

        all_issues = list(self.db.scalars(
            select(Issue).where(Issue.project_id == self.project.id)
        ))
        for issue in all_issues:
            if issue.due_date is not None:
                issue.planned_start_date = max(
                    self.project.plan_start_date,
                    issue.due_date - timedelta(days=5),
                )

        offsets = ((-7, 1, 8, 15, 22), (-3, 4, 11, 18, 26))
        titles = (
            (
                "出货范围冻结", "长周期物料齐套", "模块预装完成",
                "整机联调 Gate", "客户验收 Milestone",
            ),
            (
                "工装方案确认", "关键件加工", "首件检验",
                "装配放行 Gate", "包装发运 Milestone",
            ),
        )
        for chain_index, chain in enumerate(chains[:2]):
            for position, issue in enumerate(chain):
                due = TODAY + timedelta(days=offsets[chain_index][position])
                issue.title = titles[chain_index][position]
                issue.planned_start_date = due - timedelta(days=4)
                issue.due_date = due
                issue.labels = list(dict.fromkeys([
                    *(issue.labels or []),
                    *(["Gate"] if position == len(chain) - 2 else []),
                    *(["Milestone"] if position == len(chain) - 1 else []),
                ]))
        self.db.flush()
        snapshot, blockers, _warnings = _baseline_material(self.db, self.project)
        if blockers:
            self.say(f"  ! baseline skipped: {'; '.join(blockers)}")
            self.db.rollback()
            return
        canonical = json.dumps(
            snapshot,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        baseline = ProjectScheduleBaseline(
            project_id=self.project.id,
            version=1,
            active=True,
            target_date=self.project.target_date,
            scope_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            snapshot=snapshot,
            note="演示：已批准的顺序执行基线",
            created_by_id=self.lead.id,
        )
        self.db.add(baseline)
        self.db.flush()
        for chain in chains[:2]:
            source = chain[0]
            old_due = source.due_date
            new_due = old_due + timedelta(days=5)
            source.due_date = new_due
            source.lock_version += 1
            source.updated_at = NOW
            self.db.add(ActivityLog(
                issue_id=source.id,
                actor_id=self.lead.id,
                action="updated",
                field="due_date",
                old_value=old_due.isoformat(),
                new_value=new_due.isoformat(),
                meta={"demo": True, "reason": "关键物料/前置验证延期"},
                created_at=NOW,
            ))
        self.db.commit()
        blocking_edges = sum(len(chain) - 1 for chain in chains[:2])
        if len(chains) > 1:
            blocking_edges += 1
        self.say(
            f"  schedule: baseline v1, {blocking_edges} "
            "blocking edges, 2 delayed sources"
        )

    def finish_sla(self) -> None:
        for plan in self.plans:
            issue = plan.issue
            if issue is None:
                continue
            self.db.refresh(issue)
            restamp_sla(issue, self.project)
        self.db.commit()


def restamp_sla(issue: Issue, project: Project) -> None:
    """Restart the SLA clock at the issue's simulated creation time."""
    issue.sla_started_at = _aware(issue.created_at)
    issue.sla_paused_at, issue.sla_paused_hours = None, 0.0
    if issue.status.category == "done":
        issue.sla_state = "completed"
        return
    issue.sla_state = "ok"
    sla.recompute_due(issue, project)
    issue.sla_state = sla.state_of(issue, project, NOW)


# ---------------------------------------------------------------- main


def reset(db: Session, say: Callable[[str], None]) -> None:
    keys = {*(d.key for d in DEPARTMENTS), *LEGACY_DEMO_KEYS}
    for key in sorted(keys):
        project = db.scalar(select(Project).where(Project.key == key))
        if project is not None:
            db.execute(delete(Issue).where(Issue.project_id == project.id))
            db.execute(delete(Project).where(Project.id == project.id))
            db.commit()
            db.expunge_all()
            say(f"removed old demo project {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reset", action="store_true", help="delete and rebuild the demo projects")
    parser.add_argument("--skip-templates", action="store_true")
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--allow-sqlite-tmp", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    check_database(args.allow_sqlite_tmp)
    silence_outbound()

    def say(message: str) -> None:
        print(message, flush=True)

    if args.allow_sqlite_tmp:
        from backend.db import init_db
        from backend.main import seed

        init_db()
        seed()
    rng = random.Random(args.seed)
    with session_factory()() as db:
        admin = db.scalar(select(User).where(User.username == get_settings().admin_user))
        if admin is None:
            sys.exit("local admin user not found")
        if not args.skip_templates:
            apply_latest_templates(db, admin, say)
            recommend_templates(db, say)
        if args.reset:
            reset(db, say)
        assets = ensure_assets(db)
        people = People(db, rng)
        for dept in DEPARTMENTS:
            if db.scalar(select(Project.id).where(Project.key == dept.key)):
                say(f"{dept.key} already exists; use --reset to rebuild it")
                continue
            demo = Demo(db, dept, admin, people, assets, rng, say)
            demo.create_project()
            demo.plan()
            demo.build_events()
            demo.run()
            count = db.scalar(select(func.count(Issue.id)).where(
                Issue.project_id == demo.project.id))
            say(f"{dept.key} {dept.name}: {count} issues, lead {demo.lead.display_name}")


if __name__ == "__main__":
    main()
