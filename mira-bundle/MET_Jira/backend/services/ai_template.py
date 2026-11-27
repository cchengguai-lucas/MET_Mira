"""Canonical English AI department template definition."""

from __future__ import annotations

from typing import Any


def build_ai_definition() -> dict[str, Any]:
    """English AI template: intake + development-complete + self-test loop."""
    statuses = [
        {"name": "Backlog", "category": "todo", "color": "#8e8e93"},
        {"name": "In Progress", "category": "doing", "color": "#0a84ff"},
        {"name": "Development Complete", "category": "doing", "color": "#5ac8fa"},
        {"name": "Testing", "category": "doing", "color": "#ff9f0a"},
        {"name": "Test Passed", "category": "doing", "color": "#34c759"},
        {"name": "Test Failed", "category": "doing", "color": "#ff375f"},
        {"name": "Deferred", "category": "doing", "color": "#ff9f0a"},
        {"name": "Paused", "category": "doing", "color": "#af52de"},
        {"name": "Reopened", "category": "todo", "color": "#32ade6"},
        {"name": "Done", "category": "done", "color": "#30d158"},
        {"name": "Cancelled", "category": "done", "color": "#ff453a"},
    ]

    def simple_flow() -> dict[str, list[str]]:
        return {
            "Backlog": ["In Progress", "Paused", "Deferred", "Done", "Cancelled"],
            "In Progress": ["Done", "Deferred", "Paused", "Backlog", "Cancelled"],
            "Deferred": ["In Progress", "Paused", "Cancelled"],
            "Paused": ["In Progress", "Deferred", "Cancelled"],
            "Reopened": ["In Progress", "Paused", "Cancelled"],
            "Done": ["Reopened"],
            "Cancelled": ["Reopened", "Backlog"],
        }

    def tested_flow() -> dict[str, list[str]]:
        return {
            "Backlog": ["In Progress", "Paused", "Deferred", "Cancelled"],
            "In Progress": [
                "Development Complete", "Deferred", "Paused", "Backlog", "Cancelled",
            ],
            "Development Complete": ["Testing", "In Progress"],
            "Testing": ["Test Passed", "Test Failed"],
            "Test Passed": ["Done", "Cancelled"],
            "Test Failed": ["Reopened", "In Progress"],
            "Deferred": ["In Progress", "Paused", "Cancelled"],
            "Paused": ["In Progress", "Deferred", "Cancelled"],
            "Reopened": ["In Progress", "Paused", "Cancelled"],
            "Done": ["Reopened"],
            "Cancelled": ["Reopened", "Backlog"],
        }

    types = ("requirement", "bug", "story", "task", "subtask", "epic")
    tested_types = ("requirement", "bug", "story")
    return {
        "schema_version": 1,
        "key": "ai",
        "labels": {"zh": "AI", "en": "AI"},
        "roles": [
            {"key": "admin", "labels": {"zh": "Admin", "en": "Admin"}},
            {"key": "requester", "labels": {"zh": "Requester", "en": "Requester"}},
            {"key": "owner", "labels": {"zh": "Owner", "en": "Owner"}},
            {"key": "tester", "labels": {"zh": "测试人员", "en": "Tester"}},
            {"key": "member", "labels": {"zh": "Member", "en": "Member"}},
        ],
        "sections": [
            {
                "key": "basic",
                "labels": {"zh": "Requirement", "en": "Requirement"},
                "fields": ["title", "description", "assignee_id"],
                "role": None,
                "issue_users": ["reporter_id", "assignee_id"],
                "issue_types": list(types),
            },
            {
                "key": "delivery",
                "labels": {"zh": "交付说明", "en": "Delivery notes"},
                "fields": ["delivery_note"],
                "role": "owner",
                "issue_users": ["assignee_id"],
                "issue_types": list(types),
            },
            {
                "key": "verification",
                "labels": {"zh": "测试", "en": "Testing"},
                "fields": ["tester_id", "test_passed", "update_date"],
                "role": "tester",
                "issue_users": ["tester_id"],
                # Create form + self-test for every type that can carry a tester.
                "issue_types": list(types),
            },
            {
                "key": "links",
                "labels": {"zh": "Links", "en": "Links"},
                "fields": ["related_services", "ref_url"],
                "role": None,
                "issue_users": [],
                "issue_types": list(types),
            },
        ],
        "fields": [
            {
                "key": "title",
                "labels": {"zh": "Title", "en": "Title"},
                "kind": "builtin", "required": True, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": [], "section": "basic", "report": False, "excel": True,
            },
            {
                "key": "description",
                "labels": {"zh": "Description", "en": "Description"},
                "kind": "builtin", "required": False, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": ["Requirement", "需求描述"], "section": "basic",
                "report": False, "excel": True,
            },
            {
                "key": "assignee_id",
                "labels": {"zh": "负责人", "en": "Assignee"},
                "kind": "builtin", "required": True, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": ["Owner"], "section": "basic",
                "report": True, "excel": True,
            },
            {
                "key": "delivery_note",
                "labels": {"zh": "交付说明", "en": "Delivery notes"},
                "kind": "markdown", "required": False, "options": [],
                "storage": "extra", "master_kind": None, "multiple": False,
                "aliases": ["测试方法", "Test method", "Handoff"],
                "section": "delivery",
                "report": False, "excel": True,
            },
            {
                "key": "tester_id",
                "labels": {"zh": "测试人", "en": "Tester"},
                "kind": "builtin", "required": False, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": ["Tester", "测试人员"], "section": "verification",
                "report": True, "excel": True,
            },
            {
                "key": "test_passed",
                "labels": {"zh": "是否通过", "en": "Passed"},
                "kind": "builtin", "required": False, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": [], "section": "verification",
                "report": True, "excel": True,
            },
            {
                "key": "update_date",
                "labels": {"zh": "更新日期", "en": "Update date"},
                "kind": "builtin", "required": False, "options": [],
                "storage": "column", "master_kind": None, "multiple": False,
                "aliases": [], "section": "verification",
                "report": False, "excel": True,
            },
            {
                "key": "related_services",
                "labels": {"zh": "Services", "en": "Services"},
                "kind": "master_ref", "required": False, "options": [],
                "storage": "master_ref", "master_kind": "service", "multiple": True,
                "aliases": ["服务", "Apps"], "section": "links",
                "report": True, "excel": True,
            },
            {
                "key": "ref_url",
                "labels": {"zh": "Reference URL", "en": "Reference URL"},
                "kind": "text", "required": False, "options": [],
                "storage": "extra", "master_kind": None, "multiple": False,
                "aliases": ["Files", "Link"], "section": "links",
                "report": False, "excel": True,
            },
        ],
        "general_fields": [
            "type", "status_id", "priority", "assignee_id", "tester_id", "parent_id",
            "epic_id", "sprint_id", "labels", "due_date", "story_points", "rank",
        ],
        "statuses": statuses,
        "workflows": {
            **{t: tested_flow() for t in tested_types},
            **{t: simple_flow() for t in ("task", "subtask", "epic")},
        },
        "defaults": {
            "update_scope_options": [],
            "dev_complete_status": "Development Complete",
            "test_status": "Testing",
            "pass_status": "Test Passed",
            "fail_return_status": "Test Failed",
            "sla_hours": {
                "highest": 24, "high": 48, "medium": 120, "low": 240, "lowest": 480,
            },
            "sla_warning_hours": 4,
        },
        "default_gates": {},
        "list_layout": {
            "columns": [
                {"field": "key", "width": 110, "section": "basic"},
                {"field": "title", "width": 260, "grow": 2, "section": "basic"},
                {"field": "status_id", "width": 120, "section": "basic"},
                {"field": "priority", "width": 90, "section": "basic"},
                {"field": "assignee_id", "width": 140, "section": "basic"},
                {"field": "tester_id", "width": 140, "section": "verification"},
                {"field": "related_services", "width": 160, "section": "links"},
                {"field": "description", "width": 280, "grow": 2, "section": "basic"},
            ]
        },
    }
