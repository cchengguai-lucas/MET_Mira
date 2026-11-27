from __future__ import annotations

from backend.db import session_factory
from backend.models import OrgUnit, User


def _org(placement: dict[int, str]) -> dict[str, int]:
    """MET / Electrical with a Power sub-unit; users are placed by unit name."""
    with session_factory()() as db:
        met = OrgUnit(dn="OU=MET", name="MET", path="MET", level=0)
        db.add(met)
        db.flush()
        electrical = OrgUnit(dn="OU=Electrical,OU=MET", name="Electrical", parent_id=met.id,
                             path="MET / Electrical", level=1)
        db.add(electrical)
        db.flush()
        power = OrgUnit(dn="OU=Power,OU=Electrical,OU=MET", name="Power",
                        parent_id=electrical.id, path="MET / Electrical / Power", level=2)
        db.add(power)
        db.flush()
        units = {"Electrical": electrical.id, "Power": power.id}
        for user_id, unit in placement.items():
            db.get(User, user_id).org_unit_id = units[unit]
        db.commit()
        return units


def test_add_members_by_org_unit(admin, make_user):
    a, a_id = make_user("lead", "电气主管")
    _b, b_id = make_user("power1", "电源工程师")
    _c, c_id = make_user("power2", "电源工程师二")
    units = _org({a_id: "Electrical", b_id: "Power", c_id: "Power"})
    with session_factory()() as db:
        db.get(User, c_id).enabled = False
        db.commit()

    only_direct = admin.post("/api/projects/ELEC/members/bulk", json={
        "org_unit_id": units["Electrical"], "role": "electrical", "include_children": False})
    assert only_direct.json() == {"added": 1, "skipped": 0, "total": 1}

    subtree = admin.post("/api/projects/ELEC/members/bulk", json={
        "org_unit_id": units["Electrical"], "role": "electrical"})
    assert subtree.json() == {"added": 1, "skipped": 1, "total": 2}, "disabled users are left out"

    members = admin.get("/api/projects/ELEC").json()["members"]
    electrical = {m["user"]["id"] for m in members if m["role"] == "electrical"}
    assert {a_id, b_id} <= electrical and c_id not in electrical
    lead = next(m for m in members if m["user"]["id"] == a_id)
    assert lead["user"]["org_path"] == "MET / Electrical"
    direct = admin.get(
        f"/api/users?org_unit_id={units['Electrical']}&descendants=false"
    ).json()
    assert direct["total"] == 1
    assert direct["users"][0]["username"] == "lead"

    assert a.post("/api/projects/ELEC/members/bulk", json={
        "org_unit_id": units["Power"], "role": "admin"}).status_code == 403
    assert admin.post("/api/projects/ELEC/members/bulk", json={
        "org_unit_id": 99999, "role": "member"}).status_code == 404


def test_bulk_update_member_roles(admin, make_user):
    member, member_id = make_user("role-bulk-member", "批量权限成员")
    _other, other_id = make_user("role-bulk-other", "另一成员")
    _outsider, outsider_id = make_user("role-bulk-outsider", "项目外成员")
    for user_id in (member_id, other_id):
        response = admin.post("/api/projects/ELEC/members", json={
            "user_id": user_id, "role": "member",
        })
        assert response.status_code == 200, response.text

    replaced = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id, other_id],
        "set_roles": ["electrical", "tester"],
    })
    assert replaced.json() == {"users": 2, "added": 4, "removed": 2}

    changed = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id, other_id],
        "add_roles": ["process"],
        "remove_roles": ["electrical"],
    })
    assert changed.json() == {"users": 2, "added": 2, "removed": 2}
    again = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id, other_id],
        "add_roles": ["process"],
        "remove_roles": ["electrical"],
    })
    assert again.json() == {"users": 2, "added": 0, "removed": 0}

    rows = admin.get("/api/projects/ELEC").json()["members"]
    roles = {
        row["role"] for row in rows if row["user"]["id"] == member_id
    }
    assert roles == {"process", "tester"}
    denied = member.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id], "set_roles": ["admin"],
    })
    assert denied.status_code == 403
    unsupported = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id], "add_roles": ["unknown"],
    })
    assert unsupported.status_code == 400
    missing = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id, outsider_id], "add_roles": ["member"],
    })
    assert missing.status_code == 404
    conflict = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id], "add_roles": ["tester"], "remove_roles": ["tester"],
    })
    assert conflict.status_code == 400
    mixed = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id],
        "set_roles": ["member"],
        "add_roles": ["tester"],
    })
    assert mixed.status_code == 400
    empty = admin.patch("/api/projects/ELEC/members/roles", json={"user_ids": [member_id]})
    assert empty.status_code == 400

    removed = admin.patch("/api/projects/ELEC/members/roles", json={
        "user_ids": [member_id, other_id], "set_roles": [],
    })
    assert removed.json() == {"users": 2, "added": 0, "removed": 4}
    user_ids = {
        row["user"]["id"] for row in admin.get("/api/projects/ELEC").json()["members"]
    }
    assert member_id not in user_ids and other_id not in user_ids


def test_same_named_org_units_keep_full_paths(admin, make_user):
    _left, left_id = make_user("left-power", "左侧同名组")
    _right, right_id = make_user("right-power", "右侧同名组")
    with session_factory()() as db:
        root = OrgUnit(dn="OU=SameRoot", name="SameRoot", path="SameRoot", level=0)
        db.add(root)
        db.flush()
        left = OrgUnit(
            dn="OU=Left,OU=SameRoot",
            name="Left",
            parent_id=root.id,
            path="SameRoot / Left",
            level=1,
        )
        right = OrgUnit(
            dn="OU=Right,OU=SameRoot",
            name="Right",
            parent_id=root.id,
            path="SameRoot / Right",
            level=1,
        )
        db.add_all([left, right])
        db.flush()
        left_power = OrgUnit(
            dn="OU=Power,OU=Left,OU=SameRoot",
            name="Power",
            parent_id=left.id,
            path="SameRoot / Left / Power",
            level=2,
        )
        right_power = OrgUnit(
            dn="OU=Power,OU=Right,OU=SameRoot",
            name="Power",
            parent_id=right.id,
            path="SameRoot / Right / Power",
            level=2,
        )
        db.add_all([left_power, right_power])
        db.flush()
        db.get(User, left_id).org_unit_id = left_power.id
        db.get(User, right_id).org_unit_id = right_power.id
        db.commit()
        unit_ids = (left_power.id, right_power.id)

    tree = admin.get("/api/org/tree").json()["units"]
    power_paths = {row["path"] for row in tree if row["label"] == "Power"}
    assert {"SameRoot / Left / Power", "SameRoot / Right / Power"} <= power_paths
    left_users = admin.get(
        f"/api/users?org_unit_id={unit_ids[0]}&descendants=true"
    ).json()["users"]
    right_users = admin.get(
        f"/api/users?org_unit_id={unit_ids[1]}&descendants=true"
    ).json()["users"]
    assert [row["username"] for row in left_users] == ["left-power"]
    assert [row["username"] for row in right_users] == ["right-power"]
    added = admin.post("/api/projects/ELEC/members/bulk", json={
        "org_unit_id": unit_ids[0], "role": "member", "include_children": True,
    })
    assert added.status_code == 200, added.text
    member_ids = {
        row["user"]["id"] for row in admin.get("/api/projects/ELEC").json()["members"]
    }
    assert left_id in member_ids and right_id not in member_ids


def test_report_units_and_scoped_report(admin, make_user):
    from backend.models import Project
    from backend.services.agent import _report_data

    _a, a_id = make_user("lead", "电气主管")
    _b, b_id = make_user("power1", "电源工程师")
    _c, c_id = make_user("power2x", "相似部门")
    units = _org({a_id: "Electrical", b_id: "Power"})
    with session_factory()() as db:
        twin = OrgUnit(dn="OU=Power2,OU=Electrical,OU=MET", name="Power2",
                       parent_id=units["Electrical"], path="MET / Electrical / Power2", level=2)
        db.add(twin)
        db.flush()
        db.get(User, c_id).org_unit_id = twin.id
        db.commit()
    def create(title: str, **people: int) -> str:
        body = {"project": "ELEC", "title": title, "due_date": "2099-12-31", **people}
        return admin.post("/api/issues", json=body).json()["key"]

    lead_key = create("主管的需求", assignee_id=a_id)
    power_key = create("电源问题", tester_id=b_id)
    twin_key = create("相似部门", assignee_id=c_id)
    create("无人负责")

    data = admin.get("/api/projects/ELEC/reports/units").json()
    assert data["total"] == 4
    rows = [(u["label"], u["depth"], u["issues"]) for u in data["units"]]
    assert rows == [("MET", 0, 3), ("Electrical", 1, 3), ("Power", 2, 1), ("Power2", 2, 1)]

    with session_factory()() as db:
        project = db.query(Project).filter_by(key="ELEC").one()
        power = _report_data(db, project, "weekly", db.get(OrgUnit, units["Power"]))
        electrical = _report_data(db, project, "weekly", db.get(OrgUnit, units["Electrical"]))
    assert [i["key"] for i in power["created"]] == [power_key], "Power2 is not part of Power"
    assert {i["key"] for i in electrical["created"]} == {lead_key, power_key, twin_key}
    assert all(m.split(":")[0] in {power_key} for m in power["status_moves"])
