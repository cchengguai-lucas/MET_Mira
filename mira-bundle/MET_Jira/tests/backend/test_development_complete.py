"""开发完成 sits between development and testing on the electrical flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.services import profiles, workflow
from backend.services.software_jira import WAIT_PENDING, snapshot_is_complete

VERIFY = {"verify_method": "上机", "verify_criteria": "稳定"}


def _ids(admin, key: str = "ELEC") -> dict[str, int]:
    project = admin.get(f"/api/projects/{key}").json()
    return {status["name"]: status["id"] for status in project["statuses"]}


def _requirement(admin, **fields) -> dict:
    created = admin.post("/api/issues", json={
        "project": "ELEC", "type": "requirement", "title": "开发完成",
        "due_date": "2099-12-31", **fields,
    })
    assert created.status_code == 200, created.text
    return created.json()


def _to_dev(admin, key: str, ids: dict[str, int]) -> dict:
    assert admin.patch(f"/api/issues/{key}", json={"status_id": ids["方案讨论"]}).status_code == 200
    assert admin.patch(f"/api/issues/{key}", json={"scheme_note": "三方方案"}).status_code == 200
    assert admin.patch(f"/api/issues/{key}", json={"scheme_action": "submit"}).status_code == 200
    assert admin.patch(f"/api/issues/{key}", json={
        "electrical_scheme_ok": True, "process_scheme_ok": True,
    }).status_code == 200
    moved = admin.patch(f"/api/issues/{key}", json={"status_id": ids["研发中"]})
    assert moved.status_code == 200, moved.text
    return moved.json()


def _stamp(key: str, status: str) -> None:
    _stamp_people(key, status, assignee="Cavin", tester=None)


def _stamp_people(key: str, status: str, *, assignee: str | None, tester: str | None) -> None:
    from backend.db import session_factory
    from backend.models import Issue

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == key))
        row.software_jira_snapshot = {
            "assignee": assignee, "tester": tester, "status": status,
            "due_date": None, "fetched_at": "2026-11-08T00:00:00+00:00", "error": None,
        }
        db.commit()


def test_builtin_flows_insert_development_complete_only_on_the_test_path():
    electrical = profiles.builtin_definition(profiles.ELECTRICAL)
    names = [row["name"] for row in electrical["statuses"]]
    assert names.index("开发完成") == names.index("研发中") + 1
    for kind in ("requirement", "bug", "story"):
        rules = electrical["workflows"][kind]
        assert rules["研发中"][0] == "开发完成"
        assert rules["开发完成"] == ["测试中", "研发中"]
    for kind in ("task", "subtask", "epic"):
        rules = electrical["workflows"][kind]
        assert "开发完成" not in rules
        assert "测试中" not in {name for targets in rules.values() for name in targets}
    for profile in (profiles.MECHANICAL, profiles.PRODUCTION):
        definition = profiles.builtin_definition(profile)
        assert all(row["name"] != "开发完成" for row in definition["statuses"])


def test_splice_keeps_a_flow_that_no_longer_goes_to_testing():
    assert workflow.splice_development_complete({"研发中": ["已完成"]}) is None
    spliced = workflow.splice_development_complete({
        "研发中": ["测试中", "方案讨论"],
    })
    assert spliced == {
        "研发中": ["开发完成", "方案讨论"],
        "开发完成": ["测试中", "研发中"],
    }
    assert workflow.splice_development_complete(spliced) is None


def test_only_awaiting_machine_verification_counts_as_software_done():
    assert snapshot_is_complete({"status": "待上机验证"})
    assert snapshot_is_complete({"status": " 待上机验证 "})
    assert not snapshot_is_complete({"status": "开发完成"})
    assert not snapshot_is_complete({"status": "已完成"})
    assert not snapshot_is_complete({"status": "Done"})
    assert not snapshot_is_complete({"status": "待开发"})
    assert not snapshot_is_complete({"status": "未待上机验证"})
    assert not snapshot_is_complete(None)


def test_software_jira_key_is_required_before_the_next_status(admin):
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]})
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body["code"] == "transition_gate"
    assert body["fields"] == ["software_jira_key"]
    stayed = admin.get(f"/api/issues/{issue['key']}").json()
    assert stayed["status"]["name"] == "研发中"
    assert stayed["extra"].get("_software_test_wait") is not True
    spaces = admin.patch(f"/api/issues/{issue['key']}", json={
        "status_id": ids["开发完成"], "software_jira_key": "  ",
    })
    assert spaces.status_code == 409
    assert spaces.json()["code"] == "transition_gate"
    back = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["方案讨论"]})
    assert back.status_code == 200, back.text
    assert back.json()["status"]["name"] == "方案讨论"


def test_without_software_the_owner_can_enter_development_complete(admin):
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "开发完成"
    testing = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["测试中"]})
    assert testing.status_code == 200, testing.text
    assert testing.json()["status"]["name"] == "测试中"


def test_early_next_waits_and_software_completion_enters_testing(admin, monkeypatch):
    from backend.db import session_factory
    from backend.models import Issue
    from backend.services import software_jira

    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, assignee_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me, "software_jira_key": "CVD-1",
    }).status_code == 200
    _stamp(issue["key"], "待开发")
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "software_pending"
    assert blocked.json()["detail"] == WAIT_PENDING
    stayed = admin.get(f"/api/issues/{issue['key']}").json()
    assert stayed["status"]["name"] == "研发中"
    assert stayed["extra"]["_software_test_wait"] is True
    assert stayed["software_test_wait"] == WAIT_PENDING
    assert stayed["title"] == "开发完成"

    _stamp(issue["key"], "未完成")

    class Fake:
        def fetch(self, key: str) -> dict:
            assert key == "CVD-1"
            return {
                "assignee": "Cavin", "tester": None, "status": "待上机验证",
                "due_date": None, "fetched_at": "2026-11-08T03:00:00+00:00", "error": None,
            }

    monkeypatch.setattr(software_jira, "client_from_settings", lambda: Fake())
    now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {
            **(row.software_jira_snapshot or {}),
            "fetched_at": (now - timedelta(minutes=6)).isoformat(),
        }
        title = row.title
        method = row.verify_method
        db.commit()
        result = software_jira.refresh_open_snapshots(db, now=now)
    assert result["moved"] == 1
    body = admin.get(f"/api/issues/{issue['key']}").json()
    assert body["status"]["name"] == "测试中"
    assert body["title"] == title
    assert body["verify_method"] == method
    assert body["software_ready"] is True
    assert body["software_test_wait"] is None
    notes = admin.get("/api/notifications").json()["notifications"]
    assert any(note["kind"] == "to_test" and issue["key"] in note["message"] for note in notes)
    activity = admin.get(f"/api/issues/{issue['key']}/activity").json()["activity"]
    assert any(
        row["field"] == "status_id" and row["meta"].get("reason") == "software_jira_done"
        for row in activity
    )


def test_auto_move_waits_for_the_tester(admin, monkeypatch):
    from backend.db import session_factory
    from backend.models import Issue
    from backend.services import software_jira

    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me, "software_jira_key": "CVD-2",
    }).status_code == 200
    _stamp(issue["key"], "待开发")
    blocked = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]})
    assert blocked.status_code == 409
    assert "测试人" in blocked.json()["detail"]

    class Fake:
        def fetch(self, key: str) -> dict:
            return {
                "assignee": "Cavin", "tester": None, "status": "待上机验证",
                "due_date": None, "fetched_at": "2026-11-08T03:00:00+00:00", "error": None,
            }

    monkeypatch.setattr(software_jira, "client_from_settings", lambda: Fake())
    now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == issue["key"]))
        row.software_jira_snapshot = {
            **dict(row.software_jira_snapshot or {}),
            "fetched_at": (now - timedelta(minutes=6)).isoformat(),
        }
        db.commit()
        software_jira.refresh_open_snapshots(db, now=now)
    body = admin.get(f"/api/issues/{issue['key']}").json()
    assert body["status"]["name"] == "研发中"
    assert "测试人" in body["software_test_wait"]
    assert body["software_ready"] is True


def test_turning_software_off_clears_the_wait(admin):
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    _stamp(issue["key"], "待开发")
    assert admin.patch(
        f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]},
    ).status_code == 409
    cleared = admin.patch(f"/api/issues/{issue['key']}", json={"involves_software": False})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["software_test_wait"] is None
    assert "_software_test_wait" not in (cleared.json()["extra"] or {})
    moved = admin.patch(f"/api/issues/{issue['key']}", json={"status_id": ids["开发完成"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"]["name"] == "开发完成"


def test_task_still_skips_development_complete(admin):
    ids = _ids(admin)
    task = admin.post("/api/issues", json={
        "project": "ELEC", "type": "task", "title": "任务", "due_date": "2099-12-31",
    }).json()
    moved = admin.patch(f"/api/issues/{task['key']}", json={"status_id": ids["研发中"]})
    assert moved.status_code == 200, moved.text
    names = {item["name"] for item in moved.json()["transitions"]}
    assert "开发完成" not in names
    assert "已完成" in names


def test_directory_people_are_watched_and_ready_follows_the_jira_status(admin, make_user):
    _, tao = make_user("tao.zhang", "张涛")
    _, nan = make_user("nan.xing", "南星")
    _, _one = make_user("same-a", "同名")
    _, _two = make_user("same-b", "同名")
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, assignee_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    _stamp_people(issue["key"], "待开发", assignee="张涛", tester="南星")
    waiting = admin.get(f"/api/issues/{issue['key']}").json()
    assert waiting["software_ready"] is None
    ids_waiting = {row["id"] for row in waiting["watchers"]}
    assert tao in ids_waiting and nan in ids_waiting
    assert waiting["software_jira_people"]["assignee"]["display_name"] == "张涛"
    assert waiting["software_jira_people"]["tester"]["username"] == "nan.xing"

    _stamp_people(issue["key"], "待上机验证", assignee="张涛", tester="南星")
    ready = admin.get(f"/api/issues/{issue['key']}").json()
    assert ready["software_ready"] is True
    notes = admin.get("/api/notifications").json()["notifications"]
    assert any(
        "待上机验证" in note["message"] and note["kind"] == "software_ready" for note in notes
    )

    other = _requirement(admin, title="同名不关注", tester_id=me, **VERIFY)
    _to_dev(admin, other["key"], ids)
    assert admin.patch(f"/api/issues/{other['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    from backend.db import session_factory
    from backend.models import Issue

    with session_factory()() as db:
        row = db.scalar(select(Issue).where(Issue.key == other["key"]))
        row.software_jira_snapshot = {
            "assignee": "同名", "tester": "没有这个人", "status": "待上机验证",
            "due_date": None, "fetched_at": "2026-11-08T00:00:00+00:00", "error": None,
        }
        db.commit()
    body = admin.get(f"/api/issues/{other['key']}").json()
    assert body["software_ready"] is True
    assert body["software_jira_people"]["assignee"] is None
    assert body["software_jira_people"]["tester"] is None
    watched = {row["display_name"] for row in body["watchers"]}
    assert "同名" not in watched
    assert "没有这个人" not in watched


def test_chinese_directory_name_matches_the_account(admin, make_user):
    from backend.db import session_factory
    from backend.models import User
    from backend.services.ldap_auth import directory_native_name

    assert directory_native_name("张涛") == "张涛"
    assert directory_native_name(" tao.zhang ") is None
    _, tao = make_user("tao.zhang", "tao.zhang")
    _, nan = make_user("xing.nan", "xing.nan")
    _, _one = make_user("shuai.wang", "shuai.wang")
    _, _two = make_user("shuai2.wang", "shuai2.wang")
    with session_factory()() as db:
        for username, native in (
            ("tao.zhang", "张涛"), ("xing.nan", "南星"),
            ("shuai.wang", "王帅"), ("shuai2.wang", "王帅"),
        ):
            user = db.scalar(select(User).where(User.username == username))
            user.native_name = native
        db.commit()
    ids = _ids(admin)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    issue = _requirement(admin, tester_id=me, assignee_id=me, **VERIFY)
    _to_dev(admin, issue["key"], ids)
    assert admin.patch(f"/api/issues/{issue['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    _stamp_people(issue["key"], "待上机验证", assignee="张涛", tester="南星")
    body = admin.get(f"/api/issues/{issue['key']}").json()
    assert body["software_jira_people"]["assignee"]["username"] == "tao.zhang"
    assert body["software_jira_people"]["tester"]["username"] == "xing.nan"
    watched = {row["id"] for row in body["watchers"]}
    assert tao in watched and nan in watched

    tied = _requirement(admin, title="重名不关注", tester_id=me, **VERIFY)
    _to_dev(admin, tied["key"], ids)
    assert admin.patch(f"/api/issues/{tied['key']}", json={
        "involves_software": True, "software_owner_id": me,
    }).status_code == 200
    _stamp_people(tied["key"], "待开发", assignee="王帅", tester=None)
    tied_body = admin.get(f"/api/issues/{tied['key']}").json()
    assert tied_body["software_jira_people"]["assignee"] is None
    assert tied_body["software_ready"] is None
    tied_names = {row["username"] for row in tied_body["watchers"]}
    assert "shuai.wang" not in tied_names and "shuai2.wang" not in tied_names


def _migration():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/0031_development_complete.py"
    )
    spec = spec_from_file_location("migration_0031", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_splices_only_the_old_electrical_edge(admin, monkeypatch):
    from backend.db import engine, session_factory
    from backend.models import Board, DepartmentTemplateVersion, Project, Status

    for key, name, profile in (
        ("OLD", "旧电气", "electrical"),
        ("CUSTOM", "已改电气", "electrical"),
        ("MECH", "机械", "mechanical"),
    ):
        created = admin.post("/api/projects", json={"key": key, "name": name, "profile": profile})
        assert created.status_code == 200, created.text

    with session_factory()() as db:
        def rewind(key: str, rules: dict[str, list[str]]) -> None:
            project = db.scalar(select(Project).where(Project.key == key))
            assert project is not None
            settings = dict(project.settings or {})
            transitions = {**dict(settings.get("transitions") or {}), "requirement": rules}
            project.settings = {**settings, "transitions": transitions}
            status = db.scalar(
                select(Status).where(Status.project_id == project.id, Status.name == "开发完成")
            )
            if status is None:
                return
            for board in db.scalars(select(Board).where(Board.project_id == project.id)):
                board.columns = []
            db.delete(status)

        rewind("OLD", {"研发中": ["测试中", "方案讨论"], "测试中": ["测试通过"]})
        rewind("CUSTOM", {"研发中": ["已完成"]})
        mech = db.scalar(select(Project).where(Project.key == "MECH"))
        assert mech is not None
        before = [
            row.name for row in db.scalars(
                select(Status).where(Status.project_id == mech.id).order_by(Status.sort_order)
            )
        ]
        version = db.scalar(
            select(DepartmentTemplateVersion)
            .join(DepartmentTemplateVersion.template)
            .where(DepartmentTemplateVersion.template.has(key="electrical"))
            .order_by(DepartmentTemplateVersion.id.desc())
        )
        assert version is not None
        definition = dict(version.definition)
        workflows = dict(definition.get("workflows") or {})
        workflows["bug"] = {"研发中": ["测试中", "待评估"]}
        definition["workflows"] = workflows
        definition["statuses"] = [
            row for row in definition.get("statuses") or []
            if not (isinstance(row, dict) and row.get("name") == "开发完成")
        ]
        version.definition = definition
        db.commit()

    module = _migration()
    with engine().begin() as connection:
        monkeypatch.setattr(module.op, "get_bind", lambda: connection)
        module.upgrade()

    with session_factory()() as db:
        old = db.scalar(select(Project).where(Project.key == "OLD"))
        assert old is not None
        rules = (old.settings or {})["transitions"]["requirement"]
        assert rules["研发中"][0] == "开发完成"
        assert rules["开发完成"] == ["测试中", "研发中"]
        names = [
            row.name for row in db.scalars(
                select(Status).where(Status.project_id == old.id).order_by(Status.sort_order)
            )
        ]
        assert names.index("开发完成") == names.index("研发中") + 1
        board = db.scalar(select(Board).where(Board.project_id == old.id))
        assert board is not None
        assert any(column.get("name") == "开发完成" for column in board.columns or [])

        custom = db.scalar(select(Project).where(Project.key == "CUSTOM"))
        assert custom is not None
        custom_names = [
            row.name for row in db.scalars(select(Status).where(Status.project_id == custom.id))
        ]
        assert "开发完成" not in custom_names
        assert (custom.settings or {})["transitions"]["requirement"]["研发中"] == ["已完成"]

        mech = db.scalar(select(Project).where(Project.key == "MECH"))
        assert mech is not None
        assert [
            row.name for row in db.scalars(
                select(Status).where(Status.project_id == mech.id).order_by(Status.sort_order)
            )
        ] == before
        stored = db.get(DepartmentTemplateVersion, version.id)
        assert stored is not None
        stored_bug = stored.definition["workflows"]["bug"]
        assert stored_bug["研发中"][0] == "开发完成"
        assert any(row.get("name") == "开发完成" for row in stored.definition["statuses"])


def test_sections_place_scheme_under_requester(admin):
    sections = admin.get("/api/projects/ELEC").json()["profile"]["sections"]
    keys = [section["key"] for section in sections]
    assert keys.index("scheme") == keys.index("requester") + 1
    scheme = next(section for section in sections if section["key"] == "scheme")
    assert scheme["issue_types"] == ["requirement"]
