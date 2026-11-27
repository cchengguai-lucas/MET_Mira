from __future__ import annotations

import importlib.util
from pathlib import Path

from backend.db import session_factory
from backend.models import OrgUnit


def _template(admin, key: str) -> dict:
    return next(row for row in admin.get("/api/admin/templates").json()["templates"]
                if row["key"] == key)


def test_builtin_templates_have_readable_role_and_field_names(admin):
    detail = admin.get(f"/api/admin/templates/{_template(admin, 'electrical')['id']}").json()
    definition = detail["current_version"]["definition"]
    roles = {row["key"]: row["labels"] for row in definition["roles"]}
    assert roles["requester"] == {"zh": "需求提出人", "en": "Requester"}
    assert roles["admin"]["zh"] == "项目管理员"
    fields = {row["key"]: row["labels"] for row in definition["fields"]}
    assert fields["title"] == {"zh": "标题", "en": "Title"}
    assert all(row["labels"]["en"] and row["labels"]["en"] != row["labels"]["zh"]
               for row in definition["sections"])


def test_label_migration_only_replaces_raw_key_labels():
    path = next(Path("backend/migrations/versions").glob("0015_*.py"))
    spec = importlib.util.spec_from_file_location("migration_0015", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    roles = [
        {"key": "requester", "labels": {"zh": "requester", "en": "requester"}},
        {"key": "tester", "labels": {"zh": "验证工程师", "en": "QA"}},
        {"key": "custom", "labels": {"zh": "custom"}},
    ]
    assert module._relabel(roles, module.ROLE_LABELS) is True
    assert roles[0]["labels"] == {"zh": "需求提出人", "en": "Requester"}
    assert roles[1]["labels"] == {"zh": "验证工程师", "en": "QA"}
    assert roles[2]["labels"] == {"zh": "custom"}
    template = {"key": "electrical", "labels": {"zh": "电气", "en": "电气"}}
    assert module._relabel([template], module.TEMPLATE_LABELS) is True
    assert template["labels"] == {"zh": "电气", "en": "Electrical"}


def test_template_list_reports_draft_and_usage(admin):
    electrical = _template(admin, "electrical")
    assert electrical["project_count"] >= 1
    assert electrical["draft_version"] is None
    draft = admin.post(f"/api/admin/templates/{electrical['id']}/versions", json={}).json()
    assert draft["status"] == "draft"
    assert _template(admin, "electrical")["draft_version"] == draft["version"]


def test_stale_draft_save_conflicts_and_draft_can_be_discarded(admin):
    template = _template(admin, "mechanical")
    url = f"/api/admin/templates/{template['id']}/versions"
    draft = admin.post(url, json={}).json()
    again = admin.post(url, json={}).json()
    assert again["id"] == draft["id"] and again["revision"] == draft["revision"]
    definition = draft["definition"]
    definition["labels"]["zh"] = "机械设计（草稿）"
    first = admin.put(f"{url}/{draft['id']}",
                      json={"revision": draft["revision"], "definition": definition})
    assert first.status_code == 200, first.text
    stale = admin.put(f"{url}/{draft['id']}",
                      json={"revision": draft["revision"], "definition": definition})
    assert stale.status_code == 409
    assert admin.post(url, json={}).json()["revision"] == first.json()["revision"]

    discarded = admin.delete(f"{url}/{draft['id']}")
    assert discarded.status_code == 200, discarded.text
    body = discarded.json()
    assert all(row["status"] == "published" for row in body["versions"])
    assert body["audits"][0]["action"] == "discard"
    assert body["audits"][0]["detail"]["version_number"] == draft["version"]
    assert _template(admin, "mechanical")["draft_version"] is None
    published = admin.get(f"/api/admin/templates/{template['id']}").json()
    assert admin.delete(f"{url}/{published['current_version_id']}").status_code == 409


def test_unpublished_template_cannot_discard_its_only_draft(admin):
    created = admin.post("/api/admin/templates", json={"key": "fresh", "label": "新模板"})
    assert created.status_code == 200, created.text
    template = created.json()
    draft = next(row for row in template["versions"] if row["status"] == "draft")
    response = admin.delete(f"/api/admin/templates/{template['id']}/versions/{draft['id']}")
    assert response.status_code == 409


def test_template_copy_edit_publish_and_disable(admin):
    catalog = admin.get("/api/projects/profile-catalog").json()["profiles"]
    electrical = next(row for row in catalog if row["key"] == "electrical")
    created = admin.post("/api/admin/templates", json={
        "key": "service", "label": "服务", "source_version_id": electrical["version_id"],
    })
    assert created.status_code == 200, created.text
    template = created.json()
    draft = next(row for row in template["versions"] if row["status"] == "draft")
    definition = draft["definition"]
    definition["labels"] = {"zh": "服务部门", "en": "Service"}
    saved = admin.put(
        f"/api/admin/templates/{template['id']}/versions/{draft['id']}",
        json={"revision": draft["revision"], "definition": definition},
    )
    assert saved.status_code == 200, saved.text
    published = admin.post(
        f"/api/admin/templates/{template['id']}/versions/{draft['id']}/publish"
    )
    assert published.status_code == 200, published.text
    assert published.json()["current_version"]["definition"]["labels"]["zh"] == "服务部门"
    assert any(row["key"] == "service"
               for row in admin.get("/api/projects/profile-catalog").json()["profiles"])
    disabled = admin.put(f"/api/admin/templates/{template['id']}", json={"enabled": False})
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    actions = [row["action"] for row in disabled.json()["audits"]]
    assert actions[:3] == ["disable", "publish", "copy"]
    copied = disabled.json()["audits"][2]
    assert copied["actor_name"] and copied["detail"]["source_version_id"] == electrical[
        "version_id"
    ]
    assert all(row["key"] != "service"
               for row in admin.get("/api/projects/profile-catalog").json()["profiles"])


def test_org_recommendation_is_inherited_and_never_changes_projects(admin):
    templates = admin.get("/api/admin/templates").json()["templates"]
    mechanical = next(row for row in templates if row["key"] == "mechanical")
    with session_factory()() as db:
        parent = OrgUnit(dn="OU=Recommend", name="Recommend", path="Recommend", level=0)
        db.add(parent)
        db.flush()
        child = OrgUnit(
            dn="OU=Child,OU=Recommend", name="Child", path="Recommend / Child",
            parent_id=parent.id, level=1,
        )
        db.add(child)
        db.commit()
        parent_id, child_id = parent.id, child.id
    saved = admin.put(
        f"/api/admin/templates/recommendations/{parent_id}",
        json={"template_ids": [mechanical["id"]]},
    )
    assert saved.status_code == 200, saved.text
    inherited = admin.get(f"/api/admin/templates/recommendations/{child_id}").json()
    assert inherited["templates"][0]["key"] == "mechanical"
    assert inherited["templates"][0]["direct"] is False
    before = admin.get("/api/projects/ELEC").json()["profile"]["key"]
    assert before == "electrical"


def test_template_migration_preview_and_apply(admin):
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "模板迁移", "description": "保留描述",
        "due_date": "2099-01-01",
    }).json()
    production = next(
        row for row in admin.get("/api/projects/profile-catalog").json()["profiles"]
        if row["key"] == "production"
    )
    preview = admin.post("/api/projects/ELEC/template-migration/preview", json={
        "target_version_id": production["version_id"],
    })
    assert preview.status_code == 200, preview.text
    diff = preview.json()
    assert diff["target"]["key"] == "production"
    status_map = {str(row["id"]): row["suggested"] for row in diff["statuses"] if row["issues"]}
    field_map = {
        row["key"]: row["suggested"] or "orphan"
        for row in diff["fields"] if row["usage"]
    }
    role_map = {
        role: ("member" if "member" in diff["target_roles"] else diff["target_roles"][0])
        for role in diff["invalid_roles"]
    }
    applied = admin.post("/api/projects/ELEC/template-migration/apply", json={
        "target_version_id": production["version_id"],
        "mapping": {
            "status_map": status_map, "field_map": field_map, "role_map": role_map,
        },
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["result"]["issues"] >= 1
    detail = admin.get("/api/projects/ELEC").json()
    assert detail["profile"]["key"] == "production"
    migrated = admin.get(f"/api/issues/{issue['key']}").json()
    assert migrated["title"] == "模板迁移"
    history = admin.get("/api/projects/ELEC/template-migrations").json()["migrations"]
    assert history[0]["status"] == "applied"


def test_required_system_field_gates_leaving_the_first_status(admin):
    electrical = next(row for row in admin.get("/api/projects/profile-catalog").json()["profiles"]
                      if row["key"] == "electrical")
    template = admin.post("/api/admin/templates", json={
        "key": "strict", "label": "严格电气", "source_version_id": electrical["version_id"],
    }).json()
    draft = next(row for row in template["versions"] if row["status"] == "draft")
    definition = draft["definition"]
    for field in definition["fields"]:
        if field["key"] == "verify_method":
            field["required"] = True
    url = f"/api/admin/templates/{template['id']}/versions/{draft['id']}"
    saved = admin.put(url, json={"revision": draft["revision"], "definition": definition})
    assert saved.status_code == 200, saved.text
    assert admin.post(f"{url}/publish").status_code == 200

    project = admin.post("/api/projects", json={
        "key": "STRICT", "name": "严格", "profile": "strict",
    })
    assert project.status_code == 200, project.text
    second = admin.get("/api/projects/STRICT").json()["statuses"][1]
    issue = admin.post("/api/issues", json={
        "project": "STRICT", "title": "必填系统字段", "due_date": "2099-01-01",
    }).json()
    issue_url = f"/api/issues/{issue['key']}"
    blocked = admin.patch(issue_url, json={"status_id": second["id"]})
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "transition_gate"
    detail = blocked.json()["detail"]
    assert "验证方法" in detail and "verify_method" not in detail
    assert admin.patch(issue_url, json={"verify_method": "示波器"}).status_code == 200
    moved = admin.patch(issue_url, json={"status_id": second["id"]})
    assert moved.status_code == 200, moved.text
