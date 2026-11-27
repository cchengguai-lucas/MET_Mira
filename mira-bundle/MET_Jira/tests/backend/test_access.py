from __future__ import annotations

ISSUE = {"project": "ELEC", "due_date": "2099-12-31", "type": "task"}


def _private(admin) -> None:
    response = admin.put("/api/projects/ELEC", json={"settings": {"visibility": "members"}})
    assert response.status_code == 200, response.text


def test_public_projects_are_open_to_everyone(admin, make_user):
    outsider, _ = make_user("outsider")
    issue = admin.post("/api/issues", json={**ISSUE, "title": "公开"}).json()
    assert outsider.get(f"/api/issues/{issue['key']}").status_code == 200
    assert outsider.get("/api/projects/ELEC/reports/health").status_code == 200
    # Public visibility is read-only for users who are neither members nor involved.
    assert outsider.post(
        f"/api/issues/{issue['key']}/comments", json={"body": "越权评论"}
    ).status_code == 403
    assert outsider.post(
        f"/api/issues/{issue['key']}/attachments",
        files={"file": ("outsider.txt", b"not allowed", "text/plain")},
    ).status_code == 403
    # Public viewers can read; creating still requires project membership.
    created = outsider.post("/api/issues", json={**ISSUE, "title": "外部需求"})
    assert created.status_code == 403
    outsider_id = outsider.get("/api/auth/me").json()["user"]["id"]
    assert admin.post("/api/projects/ELEC/members",
                      json={"user_id": outsider_id, "role": "member"}).status_code == 200
    assert outsider.post("/api/issues", json={**ISSUE, "title": "成员需求"}).status_code == 200


def test_members_only_projects_hide_everything_from_outsiders(admin, make_user):
    outsider, _ = make_user("outsider")
    issue = admin.post("/api/issues", json={**ISSUE, "title": "机密"}).json()
    board = admin.get("/api/projects/ELEC/boards").json()["boards"][0]
    comment = admin.post(f"/api/issues/{issue['key']}/comments", json={"body": "内部"}).json()
    _private(admin)

    for path in ("/api/projects/ELEC", "/api/projects/ELEC/reports/health",
                 "/api/projects/ELEC/reports/cfd", "/api/projects/ELEC/backlog",
                 f"/api/issues/{issue['key']}", f"/api/issues/{issue['key']}/comments",
                 f"/api/boards/{board['id']}"):
        response = outsider.get(path)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"] == "仅项目成员可以查看此项目"
    assert outsider.put(f"/api/comments/{comment['id']}", json={"body": "x"}).status_code == 403
    assert outsider.post("/api/issues", json={**ISSUE, "title": "x"}).status_code == 403

    assert "ELEC" not in {p["key"] for p in outsider.get("/api/projects").json()["projects"]}
    assert outsider.get("/api/search?jql=project%20%3D%20ELEC").json()["total"] == 0
    assert outsider.get("/api/search/quick?q=机密").json()["issues"] == []
    assert outsider.get("/api/search/facets").json()["total"] == 0
    assert outsider.get("/api/dashboard/health").json()["projects"] == []


def test_members_and_people_on_an_issue_keep_access(admin, make_user):
    member, member_id = make_user("member")
    assignee, assignee_id = make_user("assignee")
    added = admin.post("/api/projects/ELEC/members", json={"user_id": member_id, "role": "member"})
    assert added.status_code == 200, added.text
    mine = admin.post("/api/issues", json={**ISSUE, "title": "指派给我",
                                           "assignee_id": assignee_id}).json()
    other = admin.post("/api/issues", json={**ISSUE, "title": "别人的"}).json()
    _private(admin)

    assert member.get("/api/projects/ELEC/reports/health").status_code == 200
    assert member.get(f"/api/issues/{other['key']}").status_code == 200

    assert assignee.get(f"/api/issues/{mine['key']}").status_code == 200
    assert assignee.get(f"/api/issues/{other['key']}").status_code == 403
    assert assignee.get("/api/projects/ELEC/reports/health").status_code == 403
    found = assignee.get("/api/search?jql=project%20%3D%20ELEC").json()["issues"]
    assert [i["key"] for i in found] == [mine["key"]]
    assert [i["key"] for i in assignee.get("/api/dashboard").json()["assigned"]] == [mine["key"]]


def test_involved_outsider_cannot_see_private_related_issues(admin, make_user):
    outsider, outsider_id = make_user("related-outsider")
    hidden_parent = admin.post("/api/issues", json={
        **ISSUE, "title": "隐藏父任务",
    }).json()
    visible_child = admin.post("/api/issues", json={
        **ISSUE, "title": "可见子任务", "assignee_id": outsider_id,
        "parent_id": hidden_parent["id"], "type": "subtask",
    }).json()
    visible_parent = admin.post("/api/issues", json={
        **ISSUE, "title": "可见父任务", "assignee_id": outsider_id,
    }).json()
    hidden_child = admin.post("/api/issues", json={
        **ISSUE, "title": "隐藏子任务", "parent_id": visible_parent["id"], "type": "subtask",
    }).json()
    _private(admin)

    child_detail = outsider.get(f"/api/issues/{visible_child['key']}")
    assert child_detail.status_code == 200, child_detail.text
    assert child_detail.json()["parent_info"] is None
    assert child_detail.json()["parent_progress"] is None

    parent_detail = outsider.get(f"/api/issues/{visible_parent['key']}")
    assert parent_detail.status_code == 200, parent_detail.text
    assert hidden_child["key"] not in {
        row["key"] for row in parent_detail.json()["children"]
    }

    candidates = outsider.get(
        f"/api/issues/{visible_child['key']}/parent-candidates"
    ).json()
    assert hidden_parent["key"] not in {row["key"] for row in candidates["issues"]}


def test_visibility_value_is_checked_and_admin_only(admin, make_user):
    member, member_id = make_user("member")
    admin.post("/api/projects/ELEC/members", json={"user_id": member_id, "role": "member"})
    bad = admin.put("/api/projects/ELEC", json={"settings": {"visibility": "secret"}})
    assert bad.status_code == 400
    denied = member.put("/api/projects/ELEC", json={"settings": {"visibility": "members"}})
    assert denied.status_code == 403 and denied.json()["detail"] == "需要项目管理员权限"


def test_create_project_accepts_visibility(admin):
    created = admin.post("/api/projects", json={
        "key": "PRIV",
        "name": "仅成员项目",
        "profile": "electrical",
        "visibility": "members",
    })
    assert created.status_code == 200, created.text
    detail = admin.get("/api/projects/PRIV").json()
    assert detail["project"]["settings"]["visibility"] == "members"


def test_create_project_accepts_work_mode(admin):
    delivery = admin.post("/api/projects", json={
        "key": "DELV",
        "name": "交付项目",
        "profile": "electrical",
        "work_mode": "delivery",
        "plan_start_date": "2099-01-01",
        "target_date": "2099-06-30",
    })
    assert delivery.status_code == 200, delivery.text
    delivery_detail = admin.get("/api/projects/DELV").json()["project"]
    assert delivery_detail["settings"]["work_mode"] == "delivery"
    assert delivery_detail["plan_start_date"] == "2099-01-01"
    assert delivery_detail["target_date"] == "2099-06-30"

    ongoing = admin.post("/api/projects", json={
        "key": "ONGN",
        "name": "持续项目",
        "profile": "electrical",
        "work_mode": "ongoing",
        "plan_start_date": "2099-01-01",
        "target_date": "2099-06-30",
    })
    assert ongoing.status_code == 200, ongoing.text
    ongoing_detail = admin.get("/api/projects/ONGN").json()["project"]
    assert ongoing_detail["settings"]["work_mode"] == "ongoing"
    assert ongoing_detail["plan_start_date"] is None
    assert ongoing_detail["target_date"] is None

    bad = admin.put("/api/projects/ONGN", json={"settings": {"work_mode": "memo"}})
    assert bad.status_code == 400
