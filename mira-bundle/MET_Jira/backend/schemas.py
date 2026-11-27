from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

IssueType = Literal["requirement", "bug", "task", "story", "epic", "subtask"]
Priority = Literal["highest", "high", "medium", "low", "lowest"]


class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = False


class IssueFields(BaseModel):
    type: IssueType | None = None
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    status_id: int | None = None
    priority: Priority | None = None
    assignee_id: int | None = None
    tester_id: int | None = None
    parent_id: int | None = None
    epic_id: int | None = None
    sprint_id: int | None = None
    labels: list[str] | None = None
    planned_start_date: date | None = None
    due_date: date | None = None
    story_points: float | None = None
    rank: float | None = None
    project_name: str | None = None
    machine_ids: list[int] | None = None
    update_scope: list[str] | None = None
    involves_software: bool | None = None
    software_owner_id: int | None = None
    verify_method: str | None = None
    verify_criteria: str | None = None
    software_ready: bool | None = None
    software_note: str | None = None
    software_jira_key: str | None = Field(default=None, max_length=64)
    scheme_note: str | None = None
    electrical_scheme_ok: bool | None = None
    process_scheme_ok: bool | None = None
    software_scheme_ok: bool | None = None
    # Read-only cache. Any client value is rejected by the service.
    software_jira_snapshot: Any | None = None
    update_date: date | None = None
    test_passed: bool | None = None
    remark: str | None = None
    custom_fields: dict[str, Any] | None = None


class IssueCreate(IssueFields):
    project: str
    title: str = Field(min_length=1, max_length=500)
    due_date: date | None = Field(default=None, validate_default=True)

    @field_validator("due_date")
    @classmethod
    def _due_date_required(cls, value: date | None, info: ValidationInfo) -> date | None:
        """Sub-tasks may inherit the parent's due date; everything else needs one."""
        if value is None and info.data.get("parent_id") is None:
            raise ValueError("请填写截止日期")
        return value


class IssuePatch(IssueFields):
    expected_version: int | None = Field(default=None, ge=1)
    close_subtasks: bool = False
    # Required for「重新打开」; stored on transition activity + notify copy.
    reopen_reason: str | None = Field(default=None, max_length=2000)
    scheme_action: Literal["submit", "unlock"] | None = None


class BulkUpdate(BaseModel):
    keys: list[str]
    changes: dict[str, Any]


class RankIn(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    before_key: str | None = None
    after_key: str | None = None
    status_id: int | None = None
    sprint_id: int | None = None
    move_to_backlog: bool = False
    close_subtasks: bool = False
    reopen_reason: str | None = Field(default=None, max_length=2000)


class TransitionRequestIn(BaseModel):
    status_id: int
    comment: str = Field(default="", max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


class ApprovalDecisionIn(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


class CommentIn(BaseModel):
    body: str = Field(min_length=1)


class LinkIn(BaseModel):
    target_key: str
    link_type: Literal["blocks", "duplicates", "relates"] = "relates"
    lag_days: int = Field(default=1, ge=0, le=365)
    reparent: bool = False


class CloneIn(BaseModel):
    include_assignee: bool = False
    include_sprint: bool = False
    relate: bool = True


class ProjectIn(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{1,9}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    lead_id: int | None = None
    color: str = "#0a84ff"
    icon: str | None = None
    plan_start_date: date | None = None
    target_date: date | None = None
    with_machines: bool = False
    profile: str = Field(default="electrical", pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
    visibility: Literal["public", "members"] = "public"
    work_mode: Literal["delivery", "ongoing"] = "delivery"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    lead_id: int | None = None
    color: str | None = None
    icon: str | None = None
    plan_start_date: date | None = None
    target_date: date | None = None
    archived: bool | None = None
    settings: dict[str, Any] | None = None


class BaselinePublishIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class ScheduleRiskDecisionIn(BaseModel):
    action: Literal["accept", "reject", "snooze", "acknowledge"]
    expected_version: int = Field(ge=1)
    note: str = Field(default="", max_length=2000)
    snoozed_until: datetime | None = None


class MemberIn(BaseModel):
    user_id: int
    role: str = Field(min_length=1, max_length=32)


class MemberBulkIn(BaseModel):
    org_unit_id: int
    role: str = Field(min_length=1, max_length=32)
    include_children: bool = True


class MemberRolesBulkIn(BaseModel):
    user_ids: list[int] = Field(min_length=1, max_length=500)
    set_roles: list[str] | None = None
    add_roles: list[str] = Field(default_factory=list)
    remove_roles: list[str] = Field(default_factory=list)


class StatusIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    category: Literal["todo", "doing", "done"] = "todo"
    color: str = "#8e8e93"
    sort_order: int | None = None


class MachineGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sort_order: int | None = None


class MachineIn(BaseModel):
    group_id: int
    name: str = Field(min_length=1, max_length=120)
    sort_order: int | None = None
    active: bool = True


class AssetIn(BaseModel):
    kind: Literal["equipment", "line", "material", "service"]
    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)


class AssetBulkItem(BaseModel):
    code: str = Field(max_length=120)
    name: str = Field(max_length=200)


class AssetBulkIn(BaseModel):
    kind: Literal["equipment", "line", "material", "service"]
    items: list[AssetBulkItem] = Field(min_length=1, max_length=2000)


class ProjectAssetIn(BaseModel):
    asset_id: int
    local_machine_id: int | None = None
    alias: str = Field(default="", max_length=200)


class SprintIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal: str = ""
    start_date: date | None = None
    end_date: date | None = None


class SprintUpdate(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    name: str | None = None
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class SprintComplete(BaseModel):
    move_open_to: int | None = None  # sprint id, or None for backlog
    expected_version: int | None = Field(default=None, ge=1)


class BoardUpdate(BaseModel):
    name: str | None = None
    columns: list[dict[str, Any]] | None = None
    swimlane: Literal["none", "assignee", "group", "epic", "priority"] | None = None
    quick_filters: list[dict[str, Any]] | None = None
    filter_jql: str | None = None


FilterScope = Literal["private", "everyone", "project"]


class SavedFilterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    jql: str
    description: str = Field(default="", max_length=2000)
    scope: FilterScope = "private"
    project_key: str | None = None


class SavedFilterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    jql: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    scope: FilterScope | None = None
    project_key: str | None = None


class FilterCopyIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class OrgUnitUpdate(BaseModel):
    display_name: str | None = None
    sort_order: int | None = None
    hidden: bool | None = None


class UserAdminUpdate(BaseModel):
    is_admin: bool | None = None
    can_create_projects: bool | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=6)


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)


class AvatarColorIn(BaseModel):
    color: str | None = None


class CoverPresetIn(BaseModel):
    preset: str = Field(min_length=1, max_length=32)


class AlertTestIn(BaseModel):
    email: str | None = Field(default=None, max_length=254)


class LocalUserIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{2,64}$")
    display_name: str
    password: str = Field(min_length=6)
    email: str | None = None
    is_admin: bool = False
    can_create_projects: bool = False


class CascadeApplyItem(BaseModel):
    key: str
    due_date: date | None = None
    priority: Priority | None = None
    expected_lock_version: int = Field(ge=1)


class CascadeApplyIn(BaseModel):
    analysis_token: str = Field(min_length=64, max_length=64)
    items: list[CascadeApplyItem] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    sync_priority: bool = False


class CascadePreviewIn(BaseModel):
    analysis_token: str = Field(min_length=64, max_length=64)
    keys: list[str] = Field(min_length=1, max_length=200)
    sync_priority: bool = False
