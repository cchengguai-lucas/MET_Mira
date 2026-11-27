from __future__ import annotations

from fastapi.testclient import TestClient

ISSUE = {"project": "ELEC", "due_date": "2099-12-31", "type": "task"}


def _status(admin, name: str) -> int:
    project = admin.get("/api/projects/ELEC").json()
    return next(s["id"] for s in project["statuses"] if s["name"] == name)


def _move(admin, key: str, *names: str) -> dict:
    issue = None
    for name in names:
        response = admin.patch(f"/api/issues/{key}", json={"status_id": _status(admin, name)})
        assert response.status_code == 200, (name, response.text)
        issue = response.json()
    assert issue is not None
    return issue


def _private(admin) -> None:
    response = admin.put("/api/projects/ELEC", json={"settings": {"visibility": "members"}})
    assert response.status_code == 200, response.text


def test_assigned_open_lists_unfinished_with_full_payload(admin, make_user, monkeypatch):
    from backend import config

    monkeypatch.setenv("METJIRA_PUBLIC_URL", "http://mira.test")
    config.get_settings.cache_clear()
    try:
        owner, owner_id = make_user("owner", "负责人")
        open_issue = admin.post(
            "/api/issues",
            json={**ISSUE, "title": "进行中", "assignee_id": owner_id,
                  "description": "详细说明"},
        ).json()
        done = admin.post(
            "/api/issues",
            json={**ISSUE, "title": "已完成的", "assignee_id": owner_id},
        ).json()
        _move(admin, done["key"], "研发中", "已完成")
        _other, other_id = make_user("other", "其他人")
        admin.post("/api/issues", json={**ISSUE, "title": "别人的", "assignee_id": other_id})

        missing = admin.get("/api/users/nobody/assigned-open")
        assert missing.status_code == 404

        body = admin.get("/api/users/owner/assigned-open").json()
        assert body["user"]["username"] == "owner"
        assert body["total"] == 1
        assert body["limit"] == 100
        assert body["offset"] == 0
        assert [i["key"] for i in body["issues"]] == [open_issue["key"]]
        row = body["issues"][0]
        assert row["title"] == "进行中"
        assert row["description"] == "详细说明"
        assert row["status"]["category"] != "done"
        assert row["project_key"] == "ELEC"
        assert row["project_title"]  # Mira board name (project_name is the business field)
        assert row["assignee"]["username"] == "owner"
        assert row["reporter"] is not None
        assert "tester" in row
        assert "machines" in row
        assert "labels" in row
        assert "custom_fields" in row
        assert "custom_field_users" in row
        assert row["url"] == f"http://mira.test/issues/{open_issue['key']}"
        # Created with assignee and never reassigned → assigned_at == created_at
        assert row["assigned_at"] == row["created_at"]

        self_view = owner.get("/api/users/owner/assigned-open").json()
        assert self_view["total"] == 1
    finally:
        monkeypatch.delenv("METJIRA_PUBLIC_URL", raising=False)
        config.get_settings.cache_clear()


def test_assigned_open_assigned_at_follows_reassign(admin, make_user, monkeypatch):
    from backend import config

    monkeypatch.setenv("METJIRA_PUBLIC_URL", "http://mira.test")
    config.get_settings.cache_clear()
    try:
        first, first_id = make_user("first-owner", "先任")
        second, second_id = make_user("second-owner", "后任")
        created = admin.post(
            "/api/issues",
            json={**ISSUE, "title": "改派", "assignee_id": first_id},
        ).json()
        before = first.get("/api/users/first-owner/assigned-open").json()["issues"][0]
        assert before["assigned_at"] == before["created_at"]

        patched = admin.patch(
            f"/api/issues/{created['key']}", json={"assignee_id": second_id}
        ).json()
        assert patched["assignee"]["id"] == second_id

        after = second.get("/api/users/second-owner/assigned-open").json()["issues"][0]
        assert after["key"] == created["key"]
        assert after["assigned_at"] is not None
        assert after["assigned_at"] >= after["created_at"]
        assert after["assigned_at"] != after["created_at"] or after["updated_at"] >= after["created_at"]

        # First owner no longer sees it as open assigned
        assert first.get("/api/users/first-owner/assigned-open").json()["total"] == 0
    finally:
        monkeypatch.delenv("METJIRA_PUBLIC_URL", raising=False)
        config.get_settings.cache_clear()


def test_assigned_open_respects_private_visibility(admin, make_user):
    outsider, outsider_id = make_user("open-outsider")
    assignee, assignee_id = make_user("open-assignee")
    mine = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "指派可见", "assignee_id": assignee_id},
    ).json()
    hidden = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "私有隐藏", "assignee_id": outsider_id},
    ).json()
    _private(admin)

    as_assignee = assignee.get("/api/users/open-assignee/assigned-open").json()
    assert [i["key"] for i in as_assignee["issues"]] == [mine["key"]]

    as_outsider = outsider.get("/api/users/open-outsider/assigned-open").json()
    assert [i["key"] for i in as_outsider["issues"]] == [hidden["key"]]

    as_admin = admin.get("/api/users/open-outsider/assigned-open").json()
    assert [i["key"] for i in as_admin["issues"]] == [hidden["key"]]

    peek = outsider.get("/api/users/open-assignee/assigned-open").json()
    assert peek["total"] == 0
    assert peek["issues"] == []


def test_assigned_open_external_token_bypasses_cookie(app, admin, make_user, monkeypatch):
    from backend import config

    token = "ext-token-assigned-open-test"
    monkeypatch.setenv("METJIRA_EXTERNAL_API_TOKEN", token)
    monkeypatch.setenv("METJIRA_PUBLIC_URL", "http://mira.test")
    config.get_settings.cache_clear()
    try:
        _owner, owner_id = make_user("tok-owner", "令牌用户")
        created = admin.post(
            "/api/issues",
            json={**ISSUE, "title": "外部可读", "assignee_id": owner_id,
                  "description": "给门户"},
        ).json()
        _private(admin)

        # No cookie, wrong token → 401
        bare = TestClient(app)
        denied = bare.get(
            "/api/users/tok-owner/assigned-open",
            headers={"Authorization": "Bearer wrong-token-xxxxxxxx"},
        )
        assert denied.status_code == 401

        # Bearer token sees full private-project list (no visibility filter)
        ok = bare.get(
            "/api/users/tok-owner/assigned-open",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["total"] == 1
        row = body["issues"][0]
        assert row["key"] == created["key"]
        assert row["description"] == "给门户"
        assert row["url"] == f"http://mira.test/issues/{created['key']}"

        # X-Api-Key also accepted
        via_key = bare.get(
            "/api/users/tok-owner/assigned-open",
            headers={"X-Api-Key": token},
        )
        assert via_key.status_code == 200
        assert via_key.json()["total"] == 1
    finally:
        monkeypatch.delenv("METJIRA_EXTERNAL_API_TOKEN", raising=False)
        monkeypatch.delenv("METJIRA_PUBLIC_URL", raising=False)
        config.get_settings.cache_clear()
