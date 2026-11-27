from __future__ import annotations

from sqlalchemy import select

from backend.db import session_factory
from backend.models import OrgSyncRun, OrgUnit, User
from backend.services.ldap_auth import LdapProfile, org_from_dn, split_dn
from backend.services.org_sync import Directory, run_org_sync

BASE = "OU=MET,DC=met,DC=com"


def _profile(sam: str, name: str, ou: str, manager: str | None = None,
             enabled: bool = True, native_name: str | None = None) -> LdapProfile:
    return LdapProfile(
        username=sam,
        display_name=name,
        email=f"{sam}@met.com",
        dn=f"CN={name},{ou}",
        title="工程师",
        department=None,
        manager_dn=manager,
        enabled=enabled,
        native_name=native_name,
    )


def test_dn_helpers():
    assert split_dn("CN=Li\\, Lei,OU=A,DC=x") == ["CN=Li\\, Lei", "OU=A", "DC=x"]
    team, unit = org_from_dn("CN=x,OU=电气组,OU=研发部,OU=MET,DC=met,DC=com", company_ou="MET")
    assert team == "电气组" and unit == "研发部"


def test_apply_directory_builds_tree_and_diff(app, admin):
    rd = f"OU=研发部,{BASE}"
    elec = f"OU=电气组,{rd}"
    soft = f"OU=软件组,{rd}"
    boss_dn = f"CN=王总,{rd}"
    first = Directory(
        ou_dns=[BASE, rd, elec, soft],
        profiles=[
            _profile("wang", "王总", rd),
            _profile("li", "李工", elec, manager=boss_dn, native_name="李工"),
            _profile("zhao", "赵工", soft, manager=boss_dn),
        ],
    )
    with session_factory()() as db:
        run = run_org_sync(db, directory=first)
        assert run.status == "success", run.error
        assert run.stats["users"] == 3
        li = db.scalar(select(User).where(User.username == "li"))
        assert li.manager.username == "wang"
        assert li.native_name == "李工"
        assert li.org_unit.path == "MET / 研发部 / 电气组"

    second = Directory(
        ou_dns=[BASE, rd, elec],
        profiles=[
            _profile("wang", "王总", rd),
            _profile("li", "李工", elec, manager=boss_dn),
            _profile("zhao", "赵工", elec, manager=boss_dn),
            _profile("old", "离职", elec, enabled=False),
        ],
    )
    with session_factory()() as db:
        run = run_org_sync(db, directory=second)
        assert run.diff["users_moved"][0]["username"] == "zhao"
        assert run.diff["ou_removed"] == ["MET / 研发部 / 软件组"]
        assert db.scalar(select(OrgUnit).where(OrgUnit.name == "软件组")).removed is True
        assert db.scalar(select(User).where(User.username == "old")).enabled is False
        assert db.scalar(select(OrgSyncRun).order_by(OrgSyncRun.id.desc())).status == "success"

    tree = admin.get("/api/org/tree").json()
    rd_unit = next(u for u in tree["units"] if u["name"] == "研发部")
    assert rd_unit["total_count"] == 3
    detail = admin.get(f"/api/org/units/{rd_unit['id']}").json()
    assert [c["name"] for c in detail["children"]] == ["电气组"]
    profile = admin.get("/api/users/li").json()
    assert profile["manager_chain"][0]["username"] == "wang"
    reporting = admin.get("/api/org/reporting").json()["nodes"]
    assert any(n["username"] == "zhao" and n["manager_id"] for n in reporting)
    status = admin.get("/api/admin/sync").json()
    assert status["ldap"]["can_sync"] is False
    assert len(status["runs"]) == 2
