from __future__ import annotations


def _preview(admin, target_key: str) -> tuple[dict, int]:
    target = next(
        row for row in admin.get("/api/projects/profile-catalog").json()["profiles"]
        if row["key"] == target_key
    )
    response = admin.post("/api/projects/ELEC/template-migration/preview", json={
        "target_version_id": target["version_id"],
    })
    assert response.status_code == 200, response.text
    return response.json(), target["version_id"]


def _mapping(preview: dict) -> dict:
    return {
        "status_map": {
            str(row["id"]): row["suggested"]
            for row in preview["statuses"] if row["issues"]
        },
        "field_map": {
            row["key"]: row["suggested"] or "orphan"
            for row in preview["fields"] if row["usage"]
        },
        "role_map": {
            role: ("member" if "member" in preview["target_roles"]
                   else preview["target_roles"][0])
            for role in preview["invalid_roles"]
        },
    }


def test_migration_orphans_unmapped_fields_and_downloads_report(admin):
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "迁移保留", "verify_method": "示波器截图",
        "due_date": "2099-01-01",
    }).json()
    preview, target_id = _preview(admin, "production")
    mapping = _mapping(preview)
    mapping["field_map"]["verify_method"] = "orphan"
    applied = admin.post("/api/projects/ELEC/template-migration/apply", json={
        "target_version_id": target_id, "mapping": mapping,
    })
    assert applied.status_code == 200, applied.text
    migrated = admin.get(f"/api/issues/{issue['key']}").json()
    orphans = migrated["custom_fields"]["_template_orphans"]
    assert any(key.endswith(":verify_method") for key in orphans)
    report = admin.get(
        f"/api/projects/ELEC/template-migrations/{applied.json()['id']}/report.json"
    )
    assert report.status_code == 200
    assert report.headers["content-disposition"].startswith("attachment;")
    assert report.json()["result"]["values_orphaned"] >= 1


def test_migration_to_newer_version_keeps_machine_links(admin):
    project = admin.get("/api/projects/ELEC").json()
    machine = project["machine_groups"][0]["machines"][0]["id"]
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "带机台迁移", "machine_ids": [machine],
        "due_date": "2099-01-01",
    }).json()
    template = next(row for row in admin.get("/api/admin/templates").json()["templates"]
                    if row["key"] == "electrical")
    url = f"/api/admin/templates/{template['id']}/versions"
    draft = admin.post(url, json={}).json()
    assert admin.post(f"{url}/{draft['id']}/publish").status_code == 200
    preview, target_id = _preview(admin, "electrical")
    assert target_id == draft["id"]
    applied = admin.post("/api/projects/ELEC/template-migration/apply", json={
        "target_version_id": target_id, "mapping": _mapping(preview),
    })
    assert applied.status_code == 200, applied.text
    migrated = admin.get(f"/api/issues/{issue['key']}").json()
    assert [row["id"] for row in migrated["machines"]] == [machine]


def test_invalid_mapping_rolls_back_project_and_issue(admin):
    issue = admin.post("/api/issues", json={
        "project": "ELEC", "title": "不应迁移", "verify_method": "保留原值",
        "due_date": "2099-01-01",
    }).json()
    preview, target_id = _preview(admin, "production")
    mapping = _mapping(preview)
    mapping["field_map"]["verify_method"] = "missing_target"
    response = admin.post("/api/projects/ELEC/template-migration/apply", json={
        "target_version_id": target_id, "mapping": mapping,
    })
    assert response.status_code == 400
    assert admin.get("/api/projects/ELEC").json()["profile"]["key"] == "electrical"
    assert admin.get(f"/api/issues/{issue['key']}").json()["verify_method"] == "保留原值"
