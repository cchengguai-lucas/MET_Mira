"""System admins see every project and act as project admins without membership."""

from __future__ import annotations

ISSUE = {"project": "PRIV", "due_date": "2099-12-31", "type": "task"}


def _make_private_project(admin, key: str = "PRIV") -> None:
    created = admin.post(
        "/api/projects",
        json={"key": key, "name": f"Private {key}", "profile": "electrical"},
    )
    assert created.status_code == 200, created.text
    saved = admin.put(
        f"/api/projects/{key}",
        json={"settings": {"visibility": "members"}},
    )
    assert saved.status_code == 200, saved.text


def test_system_admin_sees_and_admins_projects_without_membership(admin, make_user, app):
    from tests.backend.conftest import login

    _make_private_project(admin)
    # Fresh system admin who is not a project member or lead.
    other, other_id = make_user("sysadmin2")
    granted = admin.put(f"/api/admin/users/{other_id}", json={"is_admin": True})
    assert granted.status_code == 200
    sysadmin = login(app, "sysadmin2", "secret-123")

    listed = sysadmin.get("/api/projects").json()["projects"]
    priv = next(p for p in listed if p["key"] == "PRIV")
    assert priv["is_member"] is True
    assert priv["can_admin"] is True

    detail = sysadmin.get("/api/projects/PRIV")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["is_admin"] is True
    assert "admin" in body["my_roles"]

    issue = sysadmin.post("/api/issues", json={**ISSUE, "title": "管理员创建"}).json()
    assert issue["key"].startswith("PRIV-")
    patched = sysadmin.patch(
        f"/api/issues/{issue['key']}", json={"priority": "high"},
    )
    assert patched.status_code == 200, patched.text

    settings = sysadmin.put(
        "/api/projects/PRIV",
        json={"description": "sysadmin can edit"},
    )
    assert settings.status_code == 200, settings.text

    health = sysadmin.get("/api/dashboard/health").json()["projects"]
    assert any(p["key"] == "PRIV" and p["is_member"] for p in health)

    sysadmin.__exit__(None, None, None)
