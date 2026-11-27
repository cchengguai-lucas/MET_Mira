from __future__ import annotations

from datetime import date

import pytest

from backend.services.search import Context, JqlError, compile_jql, parse_date, tokenize

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31"}


def _keys(admin, jql: str) -> set[str]:
    response = admin.get("/api/search", params={"jql": jql, "limit": 100})
    assert response.status_code == 200, response.text
    return {i["key"] for i in response.json()["issues"]}


def test_tokenize_and_errors():
    tokens = tokenize('project = ELEC AND text ~ "温度 报警" ORDER BY created DESC')
    assert [t.value for t in tokens][:3] == ["project", "=", "ELEC"]
    with pytest.raises(JqlError):
        compile_jql("nosuchfield = 1", user_id=1)
    with pytest.raises(JqlError):
        compile_jql("created > notadate", user_id=1)


def test_start_of_offsets():
    ctx = Context(user_id=None, today=date(2026, 1, 14))
    assert parse_date("startOfWeek()", ctx).date() == date(2026, 1, 12)
    assert parse_date("startOfWeek(-1)", ctx).date() == date(2026, 1, 5)
    assert parse_date("startOfMonth(-1)", ctx).date() == date(2025, 12, 1)
    assert parse_date("startOfMonth(+2)", ctx).date() == date(2026, 3, 1)
    assert [t.value for t in tokenize("updated >= startOfWeek(-1)")][-1] == "startOfWeek(-1)"


def test_jql_queries(admin):
    project = admin.get("/api/projects/ELEC").json()
    cmb1 = project["machine_groups"][0]
    me = admin.get("/api/auth/me").json()["user"]["id"]
    a = admin.post("/api/issues", json={
        **ELEC_ISSUE, "title": "温度报警阈值", "priority": "high",
        "machine_ids": [cmb1["machines"][0]["id"]], "assignee_id": me,
        "update_scope": ["软件"], "labels": ["急"],
    }).json()["key"]
    b = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "门锁互锁", "type": "bug",
                                        "involves_software": False}).json()["key"]

    assert _keys(admin, "project = ELEC") == {a, b}
    assert _keys(admin, "type = 需求") == {a}
    assert _keys(admin, "type in (bug, task)") == {b}
    assert _keys(admin, "priority >= high") == {a}
    assert _keys(admin, f"group = {cmb1['name']}") == {a}
    assert _keys(admin, "machine ~ CMB") == {a}
    assert _keys(admin, "assignee = currentUser()") == {a}
    assert _keys(admin, "assignee is EMPTY") == {b}
    assert _keys(admin, "scope = 软件 AND label = 急") == {a}
    assert _keys(admin, "software = false") == {b}
    assert _keys(admin, "温度") == {a}
    assert _keys(admin, "text ~ 互锁 OR key = " + a) == {a, b}
    assert _keys(admin, "NOT (status = 待评估)") == set()
    assert _keys(admin, "category = todo AND created >= -1d") == {a, b}
    ordered = admin.get("/api/search", params={"jql": "project = ELEC ORDER BY priority DESC"})
    assert ordered.json()["issues"][0]["key"] == a
    assert admin.get("/api/search", params={"jql": "status = "}).status_code == 400

    saved = admin.post("/api/filters", json={"name": "我的", "jql": "assignee = currentUser()"})
    assert saved.status_code == 200
    assert admin.get("/api/filters").json()["filters"][0]["name"] == "我的"


def test_shared_filter_library(admin, make_user):
    member, member_id = make_user("member")
    outsider, _ = make_user("outsider")
    admin.post("/api/projects/ELEC/members", json={"user_id": member_id, "role": "member"})

    def shared(client, **params) -> dict[str, dict]:
        response = client.get("/api/filters/shared", params=params)
        assert response.status_code == 200, response.text
        return {f["name"]: f for f in response.json()["filters"]}

    assert admin.post("/api/filters", json={"name": "私有", "jql": "project = ELEC"}).is_success
    everyone = admin.post("/api/filters", json={
        "name": "全员", "jql": "type = bug", "description": "所有缺陷", "scope": "everyone",
    }).json()["id"]
    project = admin.post("/api/filters", json={
        "name": "电气", "jql": "project = ELEC", "scope": "project", "project_key": "ELEC",
    }).json()["id"]
    assert admin.post("/api/filters", json={
        "name": "缺项目", "jql": "project = ELEC", "scope": "project"}).status_code == 400
    assert outsider.post("/api/filters", json={
        "name": "越权", "jql": "project = ELEC", "scope": "project", "project_key": "ELEC",
    }).status_code == 403

    assert member.get("/api/filters").json()["filters"] == [], "library holds only my filters"
    assert set(shared(member)) == {"全员", "电气"}
    assert set(shared(outsider)) == {"全员"}
    assert set(shared(member, project="ELEC")) == {"电气"}
    assert set(shared(member, q="缺陷")) == {"全员"}
    assert shared(admin)["电气"]["project"]["key"] == "ELEC"

    assert outsider.post(f"/api/filters/{project}/copy").status_code == 404
    copied = member.post(f"/api/filters/{everyone}/copy")
    assert copied.status_code == 200, copied.text
    again = member.post(f"/api/filters/{everyone}/copy").json()
    assert again["name"] == "全员 (副本)"
    library = {f["name"]: f for f in member.get("/api/filters").json()["filters"]}
    assert library["全员"]["scope"] == "private"
    assert library["全员"]["source"]["id"] == everyone
    listed = shared(member)["全员"]
    assert listed["copies"] == 2 and listed["saved"] is True and listed["mine"] is False

    assert member.patch(f"/api/filters/{everyone}", json={"name": "改名"}).status_code == 403
    assert admin.patch(f"/api/filters/{everyone}", json={"jql": "type = task"}).is_success
    copy = member.get("/api/filters").json()["filters"][0]
    assert copy["jql"] == "type = bug", "a copy is independent of later edits"
    assert member.patch(f"/api/filters/{copy['id']}", json={
        "scope": "project", "project_key": "ELEC", "description": "转给项目"}).is_success
    assert "全员" in shared(member, project="ELEC")
    assert admin.patch(f"/api/filters/{copy['id']}", json={"scope": "private"}).is_success, \
        "admins may take a shared filter down"
    assert admin.patch(f"/api/filters/{copy['id']}", json={"name": "x"}).status_code == 403

    assert admin.delete(f"/api/filters/{everyone}").is_success
    kept = {f["name"]: f for f in member.get("/api/filters").json()["filters"]}
    assert set(kept) == {"全员", "全员 (副本)"} and kept["全员"]["source"] is None


def test_view_filters_and_facets(admin):
    import json

    project = admin.get("/api/projects/ELEC").json()
    group = project["machine_groups"][0]
    machine = group["machines"][0]
    me = admin.get("/api/auth/me").json()["user"]
    a = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "需求A",
                                        "assignee_id": me["id"],
                                        "machine_ids": [machine["id"]]}).json()["key"]
    b = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "缺陷B",
                                        "type": "bug"}).json()["key"]

    def run(view: dict) -> set[str]:
        response = admin.get("/api/search", params={"jql": "project = ELEC",
                                                    "view": json.dumps(view)})
        assert response.status_code == 200, response.text
        return {i["key"] for i in response.json()["issues"]}

    machine_value = f"{group['name']}/{machine['name']}"
    assert run({}) == {a, b}
    assert run({"type": ["bug"]}) == {b}
    assert run({"machine": [machine_value]}) == {a}
    assert run({"machine": ["EMPTY"]}) == {b}
    assert run({"assignee": ["EMPTY"], "type": ["requirement", "bug"]}) == {b}
    assert run({"status": ["待评估"], "assignee": [me["username"]]}) == {a}
    saved = (f'(project = ELEC) AND (machine in ("{machine_value}") OR machine is EMPTY) '
             'AND assignee in (EMPTY) AND type in ("bug") ORDER BY updated DESC')
    assert _keys(admin, saved) == {b}, "the JQL saved from a view (composeJql) must parse"

    facets = admin.get("/api/search/facets", params={
        "jql": "project = ELEC", "view": json.dumps({"type": ["bug"]}),
    }).json()
    assert facets["total"] == 2
    assert {f["value"]: f["count"] for f in facets["type"]} == {"requirement": 1, "bug": 1}, \
        "a field's own selection does not narrow its counts"
    assert facets["machine"] == [{"value": "EMPTY", "count": 1}]
    assert [f["value"] for f in facets["assignee"]] == ["EMPTY"]
    assert admin.get("/api/search", params={"view": "[1]"}).status_code == 400


def test_watcher_and_linked_jql(admin):
    a = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "关注源"}).json()
    b = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "关联目标"}).json()
    # create_issue auto-watches the reporter; clear B so watcher filters differ.
    assert admin.delete(f"/api/issues/{b['key']}/watch").status_code == 200
    assert admin.post(f"/api/issues/{a['key']}/links",
                      json={"target_key": b["key"], "link_type": "blocks"}).status_code == 200
    assert a["key"] in _keys(admin, "watcher = currentUser()")
    assert b["key"] not in _keys(admin, "watcher = currentUser()")
    assert a["key"] in _keys(admin, f"linked = {b['key']}")
    assert b["key"] in _keys(admin, f"linked = {a['key']}")
    assert a["key"] in _keys(admin, "link_type = blocks")
    assert b["key"] in _keys(admin, "link_type = blocks")
    assert a["key"] not in _keys(admin, "watcher is EMPTY")
    assert b["key"] in _keys(admin, "watcher is EMPTY")
