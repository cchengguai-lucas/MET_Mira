from __future__ import annotations

from backend.db import session_factory
from backend.models import OrgUnit, User

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31"}


def _org(user_ids: dict[str, int]) -> None:
    """MET / Software / Platform holds `dev`; MET / Electrical holds `elec`."""
    with session_factory()() as db:
        met = OrgUnit(dn="OU=MET", name="MET", path="MET", level=0)
        db.add(met)
        db.flush()
        software = OrgUnit(dn="OU=Software,OU=MET", name="Software", parent_id=met.id,
                           path="MET / Software", level=1)
        electrical = OrgUnit(dn="OU=Electrical,OU=MET", name="Electrical", parent_id=met.id,
                             path="MET / Electrical", level=1)
        db.add_all([software, electrical])
        db.flush()
        platform = OrgUnit(dn="OU=Platform,OU=Software,OU=MET", name="Platform",
                           parent_id=software.id, path="MET / Software / Platform", level=2)
        db.add(platform)
        db.flush()
        db.get(User, user_ids["dev"]).org_unit_id = platform.id
        db.get(User, user_ids["elec"]).org_unit_id = electrical.id
        db.commit()


def test_software_owner_scope_and_lifecycle(admin, make_user):
    dev, dev_id = make_user("dev", "软件工程师")
    _elec, elec_id = make_user("elec", "电气工程师")
    _org({"dev": dev_id, "elec": elec_id})

    project = admin.get("/api/projects/ELEC").json()
    assert [u["path"] for u in project["software_units"]] == ["MET / Software"]
    found = admin.get("/api/users", params={"software_of": "ELEC"}).json()["users"]
    assert [u["username"] for u in found] == ["dev"]

    body = {**ELEC_ISSUE, "title": "上位机配方界面调整"}
    key = admin.post("/api/issues", json=body).json()["key"]
    assert admin.patch(f"/api/issues/{key}", json={"software_owner_id": dev_id}).status_code == 400

    resp = admin.patch(f"/api/issues/{key}",
                       json={"involves_software": True, "software_owner_id": elec_id})
    assert resp.status_code == 400
    assert "软件部门" in resp.json()["detail"]

    resp = admin.patch(f"/api/issues/{key}",
                       json={"involves_software": True, "software_owner_id": dev_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["software_owner"]["username"] == "dev"

    assert dev.patch(f"/api/issues/{key}", json={"verify_method": "HMI 回归"}).status_code == 200
    notes = dev.get("/api/notifications").json()
    assert any("软件负责人" in n["message"] for n in notes["notifications"])

    hits = admin.get("/api/search", params={"jql": "软件负责人 = dev"}).json()
    assert [i["key"] for i in hits["issues"]] == [key]

    resp = admin.patch(f"/api/issues/{key}", json={"involves_software": False})
    assert resp.status_code == 200
    assert resp.json()["software_owner"] is None


def test_issue_person_lists_follow_project_roles(admin, make_user):
    elec, elec_id = make_user("elec-a", "电气甲")
    tester, tester_id = make_user("qa-a", "测试甲")
    _other, other_id = make_user("outsider", "外人")
    added = admin.post(
        "/api/projects/ELEC/members", json={"user_id": elec_id, "role": "electrical"},
    )
    assert added.status_code == 200, added.text
    added = admin.post(
        "/api/projects/ELEC/members", json={"user_id": tester_id, "role": "tester"},
    )
    assert added.status_code == 200, added.text

    assignees = admin.get(
        "/api/users", params={"project": "ELEC", "field": "assignee_id", "limit": 100},
    )
    assert assignees.status_code == 200, assignees.text
    assignee_names = {user["username"] for user in assignees.json()["users"]}
    testers = admin.get(
        "/api/users", params={"project": "ELEC", "field": "tester_id", "limit": 100},
    ).json()
    tester_names = {user["username"] for user in testers["users"]}
    assert "elec-a" in assignee_names
    assert "qa-a" not in assignee_names
    assert "outsider" not in assignee_names
    missed = tester.get(
        "/api/users", params={"project": "ELEC", "field": "assignee_id", "q": "外人"},
    )
    assert missed.json()["users"] == []
    assert "qa-a" in tester_names
    assert "elec-a" not in tester_names
    assert "outsider" not in tester_names
    assert other_id not in {user["id"] for user in testers["users"]}
    _dev, dev_id = make_user("dev", "软件工程师")
    _elec, elec_id = make_user("elec", "电气工程师")
    _org({"dev": dev_id, "elec": elec_id})
    settings = {"software_units": ["MET / Electrical"]}
    resp = admin.put("/api/projects/ELEC", json={"settings": settings})
    assert resp.status_code == 200, resp.text
    found = admin.get("/api/users", params={"software_of": "ELEC"}).json()["users"]
    assert [u["username"] for u in found] == ["elec"]
