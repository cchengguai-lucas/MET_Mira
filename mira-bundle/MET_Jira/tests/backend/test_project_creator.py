"""Users with can_create_projects may create projects without being system admins."""


def test_project_creator_flag_allows_create(admin, make_user):
    client, user_id = make_user("creator")
    denied = client.post(
        "/api/projects",
        json={"key": "NOP", "name": "No", "profile": "electrical"},
    )
    assert denied.status_code == 403

    granted = admin.put(f"/api/admin/users/{user_id}", json={"can_create_projects": True})
    assert granted.status_code == 200
    assert granted.json()["can_create_projects"] is True
    assert granted.json()["is_admin"] is False

    me = client.get("/api/auth/me").json()["user"]
    assert me["can_create_projects"] is True
    assert me["is_admin"] is False

    created = client.post(
        "/api/projects",
        json={"key": "CRT", "name": "Creator Project", "profile": "electrical"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["key"] == "CRT"


def test_plain_user_cannot_create(make_user):
    client, _ = make_user("plain")
    response = client.post(
        "/api/projects",
        json={"key": "NOPE", "name": "Nope", "profile": "electrical"},
    )
    assert response.status_code == 403


def test_system_admin_still_creates(admin):
    response = admin.post(
        "/api/projects",
        json={"key": "ADM", "name": "Admin Project", "profile": "electrical"},
    )
    assert response.status_code == 200, response.text
