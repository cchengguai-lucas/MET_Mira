"""AI template: development-complete + tester mail path."""

from __future__ import annotations

from backend.services import workflow
from backend.services.ai_template import build_ai_definition


def test_ai_definition_has_test_loop_and_tester_role():
    definition = build_ai_definition()
    names = [row["name"] for row in definition["statuses"]]
    assert "Development Complete" in names
    assert "Testing" in names
    assert {role["key"] for role in definition["roles"]} >= {"requester", "tester"}
    sections = {row["key"]: row for row in definition["sections"]}
    assert "delivery" in sections
    assert sections["delivery"]["fields"] == ["delivery_note"]
    assert sections["delivery"]["issue_users"] == ["assignee_id"]
    delivery_field = next(field for field in definition["fields"] if field["key"] == "delivery_note")
    assert delivery_field["kind"] == "markdown"
    assert delivery_field["storage"] == "extra"
    assert definition["defaults"]["dev_complete_status"] == "Development Complete"
    assert definition["defaults"]["test_status"] == "Testing"
    for kind in ("requirement", "bug", "story"):
        rules = definition["workflows"][kind]
        assert rules["In Progress"][0] == "Development Complete"
        assert rules["Development Complete"] == ["Testing", "In Progress"]
        assert rules["Testing"] == ["Test Passed", "Test Failed"]
    for kind in ("task", "subtask", "epic"):
        assert "Testing" not in definition["workflows"][kind]


def test_dev_complete_name_respects_project_setting():
    chinese = type("P", (), {"settings": {}})()
    assert workflow.dev_complete_name(chinese) == "开发完成"
    english = type("P", (), {"settings": {
        "dev_complete_status": "Development Complete",
        "test_status": "Testing",
    }})()
    assert workflow.dev_complete_name(english) == "Development Complete"
