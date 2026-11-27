"""Project profiles: which form sections an issue has, the fields in each and who may fill them.

A project picks its profile in ``settings.profile``; projects without one use ``electrical``, the
original 需求提出人 / 电气组 / 测试人员 layout. Fields not claimed by a section belong to "general".
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..models import Project

GENERAL = "general"

GENERAL_FIELDS: tuple[str, ...] = (
    "type",
    "status_id",
    "priority",
    "assignee_id",
    "parent_id",
    "epic_id",
    "sprint_id",
    "labels",
    "due_date",
    "story_points",
    "rank",
)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"  # text | textarea | date | boolean | number | select
    required: bool = False
    options: tuple[str, ...] = ()
    storage: str = "extra"  # column | extra | master_ref
    master_kind: str | None = None
    multiple: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    fields: tuple[str, ...]
    # Project role that may fill this section on any requirement.
    role: str | None = None
    # Issue person fields (``reporter_id``…) whose user may fill this section on that requirement.
    issue_users: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ("requirement",)


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    sections: tuple[Section, ...]
    roles: tuple[str, ...]
    custom_fields: tuple[Field, ...] = ()
    default_statuses: tuple[str, ...] = ()
    labels: dict[str, str] | None = None
    definition: dict[str, Any] | None = None

    @property
    def section_keys(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.sections)

    def field_section(self, field: str) -> str:
        if field.startswith("custom_fields."):
            field = field.removeprefix("custom_fields.")
        for section in self.sections:
            if field in section.fields:
                return section.key
        return GENERAL

    def field_applies_to_type(self, field: str, issue_type: str) -> bool:
        """Whether ``field`` appears on the form for ``issue_type``.

        Profile sections default to ``requirement`` only. Gates that name those
        fields must not block task/story/bug/… where the UI never shows them.
        Fields that are not owned by any section (e.g. story_points) always apply.
        """
        key = field.removeprefix("custom_fields.")
        owners = [section for section in self.sections if key in section.fields]
        if not owners:
            return True
        return any(
            not section.issue_types or issue_type in section.issue_types
            for section in owners
        )

    def custom_field(self, key: str) -> Field | None:
        return next((field for field in self.custom_fields if field.key == key), None)

    @property
    def version_id(self) -> int | None:
        return (self.definition or {}).get("version_id")

    @property
    def required_builtins(self) -> tuple[str, ...]:
        """System fields the template marks required (custom fields carry ``Field.required``)."""
        return tuple(
            str(raw["key"]) for raw in (self.definition or {}).get("fields") or []
            if isinstance(raw, dict) and raw.get("required") and raw.get("kind") == "builtin"
            and raw.get("key") in _NATIVE_FIELDS
        )


def person_roles(profile: Profile, field: str) -> set[str]:
    """Project roles that may be chosen for an issue person field."""
    roles = {
        section.role
        for section in profile.sections
        if section.role and field in section.issue_users
    }
    if field == "assignee_id":
        roles.add("admin")
    return roles


ELECTRICAL = Profile(
    key="electrical",
    label="电气",
    sections=(
        Section(
            key="requester",
            label="需求方填写",
            fields=("title", "description", "project_name", "machine_ids"),
            issue_users=("reporter_id",),
        ),
        Section(
            key="scheme",
            label="方案确认",
            fields=("scheme_note", "electrical_scheme_ok", "process_scheme_ok",
                    "software_scheme_ok"),
            issue_types=("requirement",),
        ),
        Section(
            key="electrical",
            label="电气组填写",
            fields=("update_scope", "involves_software", "software_owner_id", "verify_method",
                    "verify_criteria"),
            role="electrical",
            issue_users=("assignee_id", "software_owner_id"),
        ),
        Section(
            key="software",
            label="软件组填写",
            fields=("software_ready", "software_note", "software_jira_key"),
            issue_users=("software_owner_id",),
        ),
        Section(
            key="tester",
            label="测试方填写",
            fields=("update_date", "tester_id", "test_passed", "remark"),
            role="tester",
            issue_users=("tester_id",),
        ),
    ),
    roles=("admin", "requester", "electrical", "process", "tester", "member"),
    default_statuses=(
        "待评估", "方案讨论", "重新打开", "研发中", "开发完成",
        "测试中", "测试通过", "测试未通过", "已完成", "已驳回",
    ),
)

MECHANICAL = Profile(
    key="mechanical",
    label="机械",
    sections=(
        Section(
            key="requester", label="需求方填写",
            fields=("title", "description", "project_name", "machine_ids"),
            issue_users=("reporter_id",),
        ),
        Section(
            key="mechanical", label="机械设计组填写",
            fields=("drawing_no", "part_no", "material", "tolerance", "drawing_url"),
            role="mechanical", issue_users=("assignee_id",),
        ),
        Section(
            key="inspection", label="工艺/装配确认",
            fields=("trial_result", "inspection_result", "inspection_date"),
            role="quality", issue_users=("tester_id",),
        ),
    ),
    roles=("admin", "requester", "mechanical", "quality", "member"),
    custom_fields=(
        Field("drawing_no", "图号", required=True),
        Field("part_no", "零件号"),
        Field("material", "材料", kind="master_ref", storage="master_ref",
              master_kind="material"),
        Field("tolerance", "公差等级"),
        Field("drawing_url", "图纸 / PLM 链接"),
        Field("trial_result", "试装结果", "textarea"),
        Field("inspection_result", "终检结果", "select", options=("通过", "不通过", "有条件通过")),
        Field("inspection_date", "终检日期", "date"),
    ),
    default_statuses=(
        "待评估", "设计中", "重新打开", "图纸评审", "试装", "终检", "已完成", "已驳回",
    ),
)

PRODUCTION = Profile(
    key="production",
    label="生产",
    sections=(
        Section(
            key="requester", label="问题提出人填写",
            fields=("title", "description", "project_name"), issue_users=("reporter_id",),
        ),
        Section(
            key="production", label="生产执行组填写",
            fields=("work_order", "batch_no", "line", "planned_date", "actual_quantity"),
            role="production", issue_users=("assignee_id",),
        ),
        Section(
            key="quality", label="质量确认",
            fields=("quality_result", "quality_note", "quality_date"),
            role="quality", issue_users=("tester_id",),
        ),
    ),
    roles=("admin", "requester", "production", "quality", "member"),
    custom_fields=(
        Field("work_order", "工单号", required=True),
        Field("batch_no", "批次"),
        Field("line", "产线", kind="master_ref", storage="master_ref",
              master_kind="line"),
        Field("planned_date", "计划日期", "date"),
        Field("actual_quantity", "实际数量", "number"),
        Field("quality_result", "质量结果", "select", options=("通过", "不通过", "待复验")),
        Field("quality_note", "质量说明", "textarea"),
        Field("quality_date", "质量确认日期", "date"),
    ),
    default_statuses=(
        "待分派", "已排产", "重新打开", "在制", "质检", "完工", "已驳回",
    ),
)

PROFILES: dict[str, Profile] = {p.key: p for p in (ELECTRICAL, MECHANICAL, PRODUCTION)}
DEFAULT_PROFILE = ELECTRICAL.key

_NATIVE_FIELDS = {
    "title", "description", "project_name", "machine_ids", "update_scope",
    "involves_software", "software_owner_id", "verify_method", "verify_criteria",
    "software_ready", "software_note", "software_jira_key",
    "scheme_note", "electrical_scheme_ok", "process_scheme_ok", "software_scheme_ok",
    "update_date", "tester_id", "test_passed", "remark", *GENERAL_FIELDS,
}
_PALETTE = ("#8e8e93", "#af52de", "#0a84ff", "#ff9f0a", "#5e5ce6", "#64d2ff")
# Statuses whose meaning is fixed get the same category and colour in every template (matching
# workflow.DEFAULT_STATUSES), so "done" is never red and rework stays recognisable.
STATUS_STYLES: dict[str, tuple[str, str]] = {
    "待评估": ("todo", "#8e8e93"),
    "待分派": ("todo", "#8e8e93"),
    "方案讨论": ("todo", "#af52de"),
    "重新打开": ("todo", "#32ade6"),
    "研发中": ("doing", "#0a84ff"),
    "开发完成": ("doing", "#5ac8fa"),
    "设计中": ("doing", "#0a84ff"),
    "图纸评审": ("doing", "#5e5ce6"),
    "试装": ("doing", "#ff9f0a"),
    "终检": ("doing", "#64d2ff"),
    "已排产": ("doing", "#5e5ce6"),
    "在制": ("doing", "#0a84ff"),
    "质检": ("doing", "#ff9f0a"),
    "测试中": ("doing", "#ff9f0a"),
    "测试通过": ("doing", "#34c759"),
    "测试未通过": ("doing", "#ff375f"),
    "已完成": ("done", "#30d158"),
    "完工": ("done", "#30d158"),
    "已驳回": ("done", "#ff453a"),
}

# Display names for keys the built-in templates share; 0015 backfills seeded versions with them.
ROLE_LABELS: dict[str, dict[str, str]] = {
    "admin": {"zh": "项目管理员", "en": "Project admin"},
    "requester": {"zh": "需求提出人", "en": "Requester"},
    "electrical": {"zh": "电气组", "en": "Electrical team"},
    "process": {"zh": "工艺工程师", "en": "Process engineer"},
    "tester": {"zh": "测试人员", "en": "Tester"},
    "mechanical": {"zh": "机械设计组", "en": "Mechanical design"},
    "production": {"zh": "生产执行组", "en": "Production team"},
    "quality": {"zh": "质量确认", "en": "Quality"},
    "member": {"zh": "成员", "en": "Member"},
}
BUILTIN_FIELD_LABELS: dict[str, dict[str, str]] = {
    "title": {"zh": "标题", "en": "Title"},
    "description": {"zh": "描述", "en": "Description"},
    "project_name": {"zh": "项目名称", "en": "Project name"},
    "machine_ids": {"zh": "机台", "en": "Machines"},
    "update_scope": {"zh": "影响范围", "en": "Impact scope"},
    "involves_software": {"zh": "是否涉及软件", "en": "Involves software"},
    "software_owner_id": {"zh": "软件负责人", "en": "Software owner"},
    "software_ready": {"zh": "软件是否准备好", "en": "Software ready"},
    "software_note": {"zh": "软件交付说明", "en": "Software delivery note"},
    "software_jira_key": {"zh": "软件 Jira 单号", "en": "Software Jira key"},
    "scheme_note": {"zh": "方案说明", "en": "Scheme note"},
    "electrical_scheme_ok": {"zh": "电气确认", "en": "Electrical confirmation"},
    "process_scheme_ok": {"zh": "工艺确认", "en": "Process confirmation"},
    "software_scheme_ok": {"zh": "软件确认", "en": "Software confirmation"},
    "scheme_locked": {"zh": "发起方案确认", "en": "Scheme submitted"},
    "scheme_review": {"zh": "方案比对", "en": "Scheme review"},
    "verify_method": {"zh": "验证方法", "en": "Verification method"},
    "verify_criteria": {"zh": "验证标准", "en": "Acceptance criteria"},
    "update_date": {"zh": "更新日期", "en": "Update date"},
    "tester_id": {"zh": "测试人", "en": "Tester"},
    "test_passed": {"zh": "是否通过", "en": "Passed"},
    "remark": {"zh": "备注", "en": "Remarks"},
}
SECTION_EN_LABELS: dict[str, str] = {
    "需求方填写": "Requester",
    "电气组填写": "Electrical team",
    "方案确认": "Scheme confirmation",
    "软件组填写": "Software",
    "测试方填写": "Tester",
    "机械设计组填写": "Mechanical design",
    "工艺/装配确认": "Process / assembly check",
    "问题提出人填写": "Reporter",
    "生产执行组填写": "Production team",
    "质量确认": "Quality check",
}
PROFILE_EN_LABELS = {"electrical": "Electrical", "mechanical": "Mechanical",
                     "production": "Production"}


def builtin_definition(profile: Profile) -> dict[str, Any]:
    """Stable JSON representation used to seed the versioned template catalog."""
    statuses = []
    for index, name in enumerate(profile.default_statuses):
        category, color = STATUS_STYLES.get(name) or (
            "todo" if index == 0 else "doing",
            _PALETTE[min(index, len(_PALETTE) - 1)],
        )
        statuses.append({"name": name, "category": category, "color": color})
    fields: list[dict[str, Any]] = []
    custom = {f.key: f for f in profile.custom_fields}
    for section in profile.sections:
        for key in section.fields:
            field = custom.get(key)
            fields.append({
                "key": key,
                "labels": ({"zh": field.label, "en": ""} if field else
                           dict(BUILTIN_FIELD_LABELS.get(key, {"zh": key, "en": key}))),
                "kind": field.kind if field else "builtin",
                "required": field.required if field else False,
                "options": list(field.options) if field else [],
                "storage": (
                    field.storage if field else
                    ("column" if key in _NATIVE_FIELDS else "extra")
                ),
                "master_kind": field.master_kind if field else None,
                "multiple": field.multiple if field else False,
                "aliases": list(field.aliases) if field else [],
                "section": section.key,
                "report": bool(field and field.kind in {"select", "boolean", "master_ref"}),
                "excel": True,
            })
    names = list(profile.default_statuses)
    linear = {name: ([names[i + 1]] if i + 1 < len(names) else [])
              for i, name in enumerate(names)}
    if "已驳回" in names:
        for name in names[:-2]:
            linear[name] = [*linear[name], "已驳回"]
        linear["已驳回"] = [names[0]]
    workflows = {kind: dict(linear) for kind in
                 ("requirement", "bug", "story", "task", "subtask", "epic")}
    if profile.key == "electrical":
        reopened, passed, failed = "重新打开", "测试通过", "测试未通过"
        workflows = {
            "requirement": {
                "待评估": ["方案讨论", "已驳回"],
                "方案讨论": ["研发中", "待评估", "已驳回"],
                reopened: ["研发中", "方案讨论"],
                "研发中": ["开发完成", "方案讨论"],
                "开发完成": ["测试中", "研发中"],
                "测试中": [passed, failed],
                passed: ["已完成", "已驳回"],
                failed: [reopened], "已完成": [reopened], "已驳回": ["待评估"],
            },
            "bug": {
                "待评估": ["研发中", "已驳回"], reopened: ["研发中"],
                "研发中": ["开发完成", "待评估"],
                "开发完成": ["测试中", "研发中"],
                "测试中": [passed, failed],
                passed: ["已完成", "已驳回"],
                failed: [reopened], "已完成": [reopened], "已驳回": ["待评估"],
            },
            "story": {
                "待评估": ["研发中"], reopened: ["研发中", "待评估"],
                "研发中": ["开发完成", "待评估"],
                "开发完成": ["测试中", "研发中"],
                "测试中": [passed, failed],
                passed: ["已完成"],
                failed: [reopened], "已完成": [reopened],
            },
            "task": {
                "待评估": ["研发中"], reopened: ["研发中"],
                "研发中": ["已完成", "待评估"], "已完成": [reopened],
            },
            "subtask": {
                "待评估": ["研发中", "已完成"], reopened: ["研发中", "已完成"],
                "研发中": ["已完成", "待评估"], "已完成": [reopened],
            },
            "epic": {
                "待评估": ["研发中", "已驳回"], reopened: ["研发中"],
                "研发中": ["已完成", "待评估"], "已完成": [reopened],
                "已驳回": ["待评估"],
            },
        }
    elif profile.key == "mechanical":
        # Design → review → trial → inspect → done; reopen after completion for rework.
        reopened = "重新打开"
        full = {
            "待评估": ["设计中", "已驳回"],
            "设计中": ["图纸评审", "待评估", "已驳回"],
            reopened: ["设计中", "图纸评审"],
            "图纸评审": ["试装", "设计中", "已驳回"],
            "试装": ["终检", "图纸评审", "已驳回"],
            "终检": ["已完成", "试装", "已驳回"],
            "已完成": [reopened],
            "已驳回": ["待评估"],
        }
        workflows = {
            "requirement": dict(full),
            "bug": dict(full),
            "story": {
                "待评估": ["设计中"],
                "设计中": ["图纸评审", "待评估"],
                reopened: ["设计中", "待评估"],
                "图纸评审": ["试装", "设计中"],
                "试装": ["终检", "图纸评审"],
                "终检": ["已完成", "试装"],
                "已完成": [reopened],
            },
            "task": {
                "待评估": ["设计中"], reopened: ["设计中"],
                "设计中": ["已完成", "待评估"], "已完成": [reopened],
            },
            "subtask": {
                "待评估": ["设计中", "已完成"], reopened: ["设计中", "已完成"],
                "设计中": ["已完成", "待评估"], "已完成": [reopened],
            },
            "epic": {
                "待评估": ["设计中", "已驳回"], reopened: ["设计中"],
                "设计中": ["已完成", "待评估"], "已完成": [reopened],
                "已驳回": ["待评估"],
            },
        }
    elif profile.key == "production":
        # Dispatch → schedule → WIP → QC → done; reopen returns work to the floor.
        reopened = "重新打开"
        full = {
            "待分派": ["已排产", "已驳回"],
            "已排产": ["在制", "待分派", "已驳回"],
            reopened: ["在制", "已排产"],
            "在制": ["质检", "已排产", "已驳回"],
            "质检": ["完工", "在制", "已驳回"],
            "完工": [reopened],
            "已驳回": ["待分派"],
        }
        workflows = {
            "requirement": dict(full),
            "bug": dict(full),
            "story": {
                "待分派": ["已排产"],
                "已排产": ["在制", "待分派"],
                reopened: ["在制", "已排产"],
                "在制": ["质检", "已排产"],
                "质检": ["完工", "在制"],
                "完工": [reopened],
            },
            "task": {
                "待分派": ["在制"], reopened: ["在制"],
                "在制": ["完工", "待分派"], "完工": [reopened],
            },
            "subtask": {
                "待分派": ["在制", "完工"], reopened: ["在制", "完工"],
                "在制": ["完工", "待分派"], "完工": [reopened],
            },
            "epic": {
                "待分派": ["在制", "已驳回"], reopened: ["在制"],
                "在制": ["完工", "待分派"], "完工": [reopened],
                "已驳回": ["待分派"],
            },
        }
    return {
        "schema_version": 1,
        "key": profile.key,
        "labels": {"zh": profile.label,
                   "en": PROFILE_EN_LABELS.get(profile.key, profile.label)},
        "roles": [{"key": role, "labels": dict(ROLE_LABELS.get(role, {"zh": role, "en": role}))}
                  for role in profile.roles],
        "sections": [
            {
                "key": s.key,
                "labels": {"zh": s.label, "en": SECTION_EN_LABELS.get(s.label, s.label)},
                "fields": list(s.fields), "role": s.role,
                "issue_users": list(s.issue_users), "issue_types": list(s.issue_types),
            }
            for s in profile.sections
        ],
        "fields": fields,
        "general_fields": list(GENERAL_FIELDS),
        "statuses": statuses,
        "workflows": workflows,
        "defaults": {
            "update_scope_options": [
                "硬件", "软件", "工作流程", "程序/参数", "图纸/文档", "其他",
            ],
            "test_status": "测试中" if "测试中" in names else None,
            "pass_status": (
                "测试通过" if "测试通过" in names else
                next((s["name"] for s in statuses
                      if s["category"] == "done" and s["name"] != "已驳回"), None)
            ) if "测试中" in names else None,
            "fail_return_status": "测试未通过" if "测试未通过" in names else None,
            "sla_hours": {
                "highest": 24, "high": 48, "medium": 120, "low": 240, "lowest": 480,
            },
            "sla_warning_hours": 4,
        },
        "default_gates": {},
        "list_layout": {"columns": _builtin_columns(profile, fields)},
    }


def _builtin_columns(profile: Profile, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reproduce the pre-template sheet so seeding v1 changes nothing visible."""
    if profile.key == "electrical":
        return [
            {"field": "key", "width": 160, "section": "requester"},
            {"field": "reporter_id", "width": 136, "section": "requester"},
            {"field": "project_name", "width": 120, "grow": 1, "section": "requester"},
            {"field": "machine_group", "width": 90, "section": "requester"},
            {"field": "machine_ids", "width": 180, "section": "requester"},
            {"field": "title", "width": 180, "grow": 2, "section": "requester"},
            {"field": "description", "width": 260, "grow": 3, "section": "requester"},
            {"field": "update_scope", "width": 190, "section": "electrical"},
            {"field": "involves_software", "width": 84, "section": "electrical"},
            {"field": "software_owner_id", "width": 136, "section": "electrical"},
            {"field": "software_ready", "width": 120, "section": "software"},
            {"field": "software_jira_key", "width": 130, "section": "software"},
            {"field": "scheme_note", "width": 220, "grow": 2, "section": "scheme"},
            {"field": "electrical_scheme_ok", "width": 96, "section": "scheme"},
            {"field": "process_scheme_ok", "width": 96, "section": "scheme"},
            {"field": "software_scheme_ok", "width": 96, "section": "scheme"},
            {"field": "verify_method", "width": 170, "grow": 2, "section": "electrical"},
            {"field": "verify_criteria", "width": 190, "grow": 2, "section": "electrical"},
            {"field": "status_id", "width": 156, "section": "electrical"},
            {"field": "update_date", "width": 104, "section": "tester"},
            {"field": "tester_id", "width": 136, "section": "tester"},
            {"field": "test_passed", "width": 84, "section": "tester"},
            {"field": "remark", "width": 150, "grow": 1.5, "section": "tester"},
            {"field": "assignee_id", "width": 136, "section": GENERAL},
            {"field": "priority", "width": 88, "section": GENERAL},
            {"field": "due_date", "width": 108, "section": GENERAL},
        ]
    requester = profile.sections[0].key if profile.sections else GENERAL
    base = [
        {"field": "key", "width": 160, "section": requester},
        {"field": "reporter_id", "width": 136, "section": requester},
        {"field": "project_name", "width": 120, "grow": 1, "section": requester},
        {"field": "machine_group", "width": 90, "section": requester},
        {"field": "machine_ids", "width": 180, "section": requester},
        {"field": "title", "width": 180, "grow": 2, "section": requester},
        {"field": "description", "width": 260, "grow": 3, "section": requester},
    ]
    custom = [
        {"field": f["key"], "width": 220 if f["kind"] in {"textarea", "markdown"} else 140,
         **({"grow": 2} if f["kind"] in {"textarea", "markdown"} else {}),
         "section": f["section"]}
        for f in fields if f["storage"] != "column"
    ]
    tracking = [
        {"field": "status_id", "width": 156, "section": GENERAL},
        {"field": "assignee_id", "width": 136, "section": GENERAL},
        {"field": "priority", "width": 88, "section": GENERAL},
        {"field": "due_date", "width": 108, "section": GENERAL},
    ]
    return [*base, *custom, *tracking]


def from_definition(definition: dict[str, Any]) -> Profile:
    fields = tuple(
        Field(
            key=str(raw["key"]),
            label=str((raw.get("labels") or {}).get("zh") or raw["key"]),
            kind=str(raw.get("kind") or "text"),
            required=bool(raw.get("required")),
            options=tuple(str(v) for v in raw.get("options") or []),
            storage=str(raw.get("storage") or "extra"),
            master_kind=raw.get("master_kind"),
            multiple=bool(raw.get("multiple")),
            aliases=tuple(str(v) for v in raw.get("aliases") or []),
        )
        for raw in definition.get("fields") or []
        if str(raw.get("storage") or "extra") != "column"
    )
    sections = tuple(
        Section(
            key=str(raw["key"]),
            label=str((raw.get("labels") or {}).get("zh") or raw["key"]),
            fields=tuple(str(v) for v in raw.get("fields") or []),
            role=raw.get("role"),
            issue_users=tuple(str(v) for v in raw.get("issue_users") or []),
            issue_types=tuple(str(v) for v in raw.get("issue_types") or ["requirement"]),
        )
        for raw in definition.get("sections") or []
    )
    return Profile(
        key=str(definition["key"]),
        label=str((definition.get("labels") or {}).get("zh") or definition["key"]),
        sections=sections,
        roles=tuple(str(r["key"] if isinstance(r, dict) else r)
                    for r in definition.get("roles") or []),
        custom_fields=fields,
        default_statuses=tuple(str(s["name"] if isinstance(s, dict) else s)
                               for s in definition.get("statuses") or []),
        labels=dict(definition.get("labels") or {}),
        definition=definition,
    )


def form_has_field(profile: Profile, field: str, issue_type: str) -> bool:
    """True only when a section both owns ``field`` and shows it for ``issue_type``.

    Unlike ``field_applies_to_type``, a field with no section does not apply.
    """
    owners = [section for section in profile.sections if field in section.fields]
    if not owners:
        return False
    return any(
        not section.issue_types or issue_type in section.issue_types for section in owners
    )


_SOFTWARE_SECTION = {
    "key": "software",
    "labels": {"zh": "软件组填写", "en": "Software"},
    "fields": ["software_ready", "software_note", "software_jira_key"],
    "role": None,
    "issue_users": ["software_owner_id"],
    "issue_types": ["requirement"],
}
_SOFTWARE_FIELD_DEFS = (
    ("software_ready", "软件是否准备好", "Software ready", True),
    ("software_note", "软件交付说明", "Software delivery note", False),
    ("software_jira_key", "软件 Jira 单号", "Software Jira key", False),
)


def ensure_software_section(definition: dict[str, Any]) -> dict[str, Any]:
    """Add the software section to a stored template that already tracks involves_software.

    Existing projects keep their saved workflows. The section is added in memory only.
    """
    fields = [field for field in (definition.get("fields") or []) if isinstance(field, dict)]
    if not any(field.get("key") == "involves_software" for field in fields):
        return definition
    definition = deepcopy(definition)
    sections = [
        section for section in (definition.get("sections") or []) if isinstance(section, dict)
    ]
    if not any(section.get("key") == "software" for section in sections):
        after = next(
            (index + 1 for index, section in enumerate(sections)
             if "involves_software" in (section.get("fields") or [])),
            None,
        )
        if after is None:
            after = next(
                (index for index, section in enumerate(sections) if section.get("key") == "tester"),
                len(sections),
            )
        sections.insert(after, dict(_SOFTWARE_SECTION))
    definition["sections"] = sections
    known = {str(field.get("key")) for field in fields}
    extra_fields = []
    for key, zh, en, report in _SOFTWARE_FIELD_DEFS:
        if key in known:
            continue
        extra_fields.append({
            "key": key,
            "labels": {"zh": zh, "en": en},
            "kind": "builtin",
            "required": False,
            "options": [],
            "storage": "column",
            "master_kind": None,
            "multiple": False,
            "aliases": [],
            "section": "software",
            "report": report,
            "excel": True,
        })
    definition["fields"] = [*fields, *extra_fields]
    layout = dict(definition.get("list_layout") or {})
    columns = [column for column in (layout.get("columns") or []) if isinstance(column, dict)]
    if columns:
        have = {column.get("field") for column in columns}
        insert = [
            {"field": "software_ready", "width": 120, "section": "software"},
            {"field": "software_jira_key", "width": 130, "section": "software"},
        ]
        insert = [column for column in insert if column["field"] not in have]
        if insert:
            index = next(
                (i + 1 for i, column in enumerate(columns)
                 if column.get("field") == "software_owner_id"),
                len(columns),
            )
            columns[index:index] = insert
            definition["list_layout"] = {**layout, "columns": columns}
    return definition


_SCHEME_SECTION = {
    "key": "scheme",
    "labels": {"zh": "方案确认", "en": "Scheme confirmation"},
    "fields": [
        "scheme_note", "electrical_scheme_ok", "process_scheme_ok", "software_scheme_ok",
    ],
    "role": None,
    "issue_users": [],
    "issue_types": ["requirement"],
}
_SCHEME_FIELD_DEFS = (
    ("scheme_note", "方案说明", "Scheme note", "textarea"),
    ("electrical_scheme_ok", "电气确认", "Electrical confirmation", "boolean"),
    ("process_scheme_ok", "工艺确认", "Process confirmation", "boolean"),
    ("software_scheme_ok", "软件确认", "Software confirmation", "boolean"),
)
_PROCESS_ROLE = {"key": "process", "labels": {"zh": "工艺工程师", "en": "Process engineer"}}


def ensure_scheme_section(definition: dict[str, Any]) -> dict[str, Any]:
    """Add scheme confirmation to a stored electrical template. Workflows stay saved."""
    fields = [field for field in (definition.get("fields") or []) if isinstance(field, dict)]
    if not any(field.get("key") == "involves_software" for field in fields):
        return definition
    definition = deepcopy(definition)
    roles = [role for role in (definition.get("roles") or []) if isinstance(role, dict) or role]
    role_keys = {str(role.get("key") if isinstance(role, dict) else role) for role in roles}
    if "process" not in role_keys:
        roles.append(dict(_PROCESS_ROLE))
    definition["roles"] = roles
    sections = [
        section for section in (definition.get("sections") or []) if isinstance(section, dict)
    ]
    scheme = next((section for section in sections if section.get("key") == "scheme"), None)
    if scheme is None:
        scheme = dict(_SCHEME_SECTION)
    else:
        sections = [section for section in sections if section.get("key") != "scheme"]
    after = next(
        (index + 1 for index, section in enumerate(sections) if section.get("key") == "requester"),
        None,
    )
    if after is None:
        after = 0
    sections.insert(after, scheme)
    definition["sections"] = sections
    known = {str(field.get("key")) for field in fields}
    extra_fields = []
    for key, zh, en, kind in _SCHEME_FIELD_DEFS:
        if key in known:
            continue
        extra_fields.append({
            "key": key,
            "labels": {"zh": zh, "en": en},
            "kind": "builtin",
            "required": False,
            "options": [],
            "storage": "column",
            "master_kind": None,
            "multiple": False,
            "aliases": [],
            "section": "scheme",
            "report": kind == "boolean",
            "excel": True,
        })
    definition["fields"] = [*fields, *extra_fields]
    layout = dict(definition.get("list_layout") or {})
    columns = [column for column in (layout.get("columns") or []) if isinstance(column, dict)]
    if columns:
        have = {column.get("field") for column in columns}
        insert = [
            {"field": "scheme_note", "width": 220, "grow": 2, "section": "scheme"},
            {"field": "electrical_scheme_ok", "width": 96, "section": "scheme"},
            {"field": "process_scheme_ok", "width": 96, "section": "scheme"},
            {"field": "software_scheme_ok", "width": 96, "section": "scheme"},
        ]
        insert = [column for column in insert if column["field"] not in have]
        if insert:
            software_fields = {"software_jira_key", "software_ready", "software_note"}
            index = next(
                (i + 1 for i, column in reversed(list(enumerate(columns)))
                 if column.get("field") in software_fields),
                None,
            )
            if index is None:
                index = next(
                    (i + 1 for i, column in enumerate(columns)
                     if column.get("field") == "software_owner_id"),
                    len(columns),
                )
            columns[index:index] = insert
            definition["list_layout"] = {**layout, "columns": columns}
    return definition


def profile_of(project: Project | None) -> Profile:
    if project is not None and project.template_version is not None:
        definition = ensure_scheme_section(
            ensure_software_section(dict(project.template_version.definition))
        )
        definition["version_id"] = project.template_version.id
        return from_definition(definition)
    key = ((project.settings or {}) if project is not None else {}).get("profile")
    return PROFILES.get(key or DEFAULT_PROFILE, ELECTRICAL)


def layout(profile: Profile) -> dict[str, Any]:
    """The profile as sent to the web client."""
    definition = profile.definition or {}

    def labels_of(kind: str, key: str, label: str) -> dict[str, str]:
        for raw in definition.get(kind) or []:
            if isinstance(raw, dict) and raw.get("key") == key and raw.get("labels"):
                return {**{"zh": label}, **raw["labels"]}
        return {"zh": label}

    return {
        "key": profile.key,
        "label": profile.label,
        "sections": [
            {"key": s.key, "label": s.label, "labels": labels_of("sections", s.key, s.label),
             "fields": list(s.fields), "role": s.role, "issue_users": list(s.issue_users),
             "issue_types": list(s.issue_types)}
            for s in profile.sections
        ],
        "general_fields": list(GENERAL_FIELDS),
        "roles": list(profile.roles),
        "custom_fields": [
            {
                "key": field.key,
                "label": field.label,
                "labels": labels_of("fields", field.key, field.label),
                "kind": field.kind,
                "required": field.required,
                "options": list(field.options),
                "storage": field.storage,
                "master_kind": field.master_kind,
                "multiple": field.multiple,
                "aliases": list(field.aliases),
                "section": profile.field_section(field.key),
            }
            for field in profile.custom_fields
        ],
        "default_statuses": list(profile.default_statuses),
        "labels": profile.labels or {"zh": profile.label, "en": profile.label},
        "version_id": profile.version_id,
        "definition": profile.definition,
    }


def validate_custom_fields(profile: Profile, values: dict[str, Any]) -> dict[str, Any]:
    """Validate the schema-controlled JSON field bag and discard no data silently."""
    unknown = sorted(set(values) - {field.key for field in profile.custom_fields})
    if unknown:
        raise ValueError(f"模板不支持字段：{', '.join(unknown)}")
    out: dict[str, Any] = {}
    for key, value in values.items():
        field = profile.custom_field(key)
        assert field is not None
        if value in (None, ""):
            out[key] = value
        elif field.kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{field.label} 必须是布尔值")
        elif field.kind == "number" and not isinstance(value, (int, float)):
            raise ValueError(f"{field.label} 必须是数字")
        elif field.kind == "master_ref" and (
            (field.multiple and not isinstance(value, list))
            or (not field.multiple and not isinstance(value, int))
            or (field.multiple and any(
                not isinstance(item, int) or isinstance(item, bool) for item in value
            ))
            or (not field.multiple and isinstance(value, bool))
        ):
            raise ValueError(f"{field.label} 必须引用有效主数据")
        elif field.kind == "user" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise ValueError(f"{field.label} 必须引用有效用户")
        elif field.kind == "machine" and (
            not isinstance(value, list)
            or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
        ):
            raise ValueError(f"{field.label} 必须引用有效机台")
        elif field.kind == "select" and str(value) not in field.options:
            raise ValueError(f"{field.label} 选项无效")
        elif field.kind not in {"boolean", "number", "master_ref", "user", "machine"} and (
            not isinstance(value, str)
        ):
            raise ValueError(f"{field.label} 必须是文本")
        else:
            out[key] = value
    return out


def validate_gates(profile: Profile, gates: Any) -> dict[str, Any]:
    if not isinstance(gates, dict):
        raise ValueError("状态门禁配置无效")
    allowed_fields = set(GENERAL_FIELDS) | {
        field for section in profile.sections for field in section.fields
    }
    out: dict[str, Any] = {}
    for status, raw in gates.items():
        if not isinstance(raw, dict):
            raise ValueError("状态门禁配置无效")
        required = [str(field) for field in (raw.get("required_fields") or [])]
        for field in required:
            plain = field.removeprefix("custom_fields.")
            if plain not in allowed_fields:
                raise ValueError(f"模板不支持字段：{plain}")
        approvals = []
        for item in raw.get("approvals") or []:
            role = str(item.get("role") or "")
            if role not in profile.roles:
                raise ValueError("项目模板不支持该角色")
            approvals.append({"role": role, "count": min(20, max(1, int(item.get("count", 1))))})
        out[str(status)] = {"required_fields": required, "approvals": approvals}
    return out
