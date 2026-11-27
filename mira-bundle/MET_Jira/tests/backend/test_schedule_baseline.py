from __future__ import annotations


def test_schedule_baseline_readiness_versioning_and_permissions(admin, make_user):
    project_update = admin.put(
        "/api/projects/ELEC",
        json={"plan_start_date": "2099-01-01", "target_date": "2099-06-30"},
    )
    assert project_update.status_code == 200, project_update.text
    issue = admin.post(
        "/api/issues",
        json={
            "project": "ELEC",
            "title": "Gate readiness",
            "type": "task",
            "planned_start_date": "2099-01-02",
            "due_date": "2099-02-01",
        },
    )
    assert issue.status_code == 200, issue.text

    readiness = admin.get("/api/projects/ELEC/schedule-baseline")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["baseline"] is None

    first = admin.post(
        "/api/projects/ELEC/schedule-baseline", json={"note": "approved plan"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["baseline"]["version"] == 1

    unchanged = admin.post(
        "/api/projects/ELEC/schedule-baseline", json={"note": "duplicate"}
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["baseline"]["version"] == 1

    changed = admin.patch(
        f"/api/issues/{issue.json()['key']}",
        json={"due_date": "2099-02-05", "expected_version": issue.json()["lock_version"]},
    )
    assert changed.status_code == 200, changed.text
    second = admin.post(
        "/api/projects/ELEC/schedule-baseline", json={"note": "replanned"}
    )
    assert second.status_code == 200, second.text
    assert second.json()["baseline"]["version"] == 2

    member, _user_id = make_user("baseline-reader")
    visible = member.get("/api/projects/ELEC/schedule-baseline")
    assert visible.status_code == 200
    forbidden = member.post("/api/projects/ELEC/schedule-baseline", json={})
    assert forbidden.status_code == 403


def test_schedule_baseline_reports_missing_foundation(admin):
    readiness = admin.get("/api/projects/ELEC/schedule-baseline")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert any("计划开始日期" in item for item in readiness.json()["blockers"])
    assert any("目标日期" in item for item in readiness.json()["blockers"])


def test_ongoing_project_rejects_schedule_baseline(admin):
    created = admin.post("/api/projects", json={
        "key": "BASEO",
        "name": "持续无基线",
        "profile": "electrical",
        "work_mode": "ongoing",
    })
    assert created.status_code == 200, created.text
    readiness = admin.get("/api/projects/BASEO/schedule-baseline")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert any("持续项目" in item for item in readiness.json()["blockers"])
    publish = admin.post("/api/projects/BASEO/schedule-baseline", json={"note": "no"})
    assert publish.status_code == 400
    assert publish.json()["detail"] == "持续项目不发布排期基线"
