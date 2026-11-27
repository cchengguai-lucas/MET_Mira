from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from backend.db import session_factory
from backend.models import Notification
from backend.services import pm_risk_alerts

ISSUE = {"project": "ELEC", "due_date": "2099-12-31", "type": "task"}


def _status(admin, name: str) -> int:
    project = admin.get("/api/projects/ELEC").json()
    return next(s["id"] for s in project["statuses"] if s["name"] == name)


def _link(admin, source: str, target: str) -> None:
    resp = admin.post(
        f"/api/issues/{source}/links",
        json={"target_key": target, "link_type": "blocks"},
    )
    assert resp.status_code == 200, resp.text


def test_cascade_suggests_and_applies(admin):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=3)).isoformat()
    blocker = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "阻塞源", "due_date": yesterday, "priority": "high"},
    ).json()
    successor = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "后继", "due_date": later, "priority": "low"},
    ).json()
    _link(admin, blocker["key"], successor["key"])

    body = admin.get("/api/projects/ELEC/schedule/cascade").json()
    assert body["suggestion_count"] >= 1
    keys = {s["key"] for s in body["suggestions"]}
    assert successor["key"] in keys
    sug = next(s for s in body["suggestions"] if s["key"] == successor["key"])
    assert sug["suggested_due"] or sug["suggested_priority"]
    assert sug["suggested_priority"] == "medium"
    assert blocker["key"] in (sug.get("reason_brief") or "")
    assert "延期" in (sug.get("reason_brief") or "")

    payload = {
        "key": successor["key"],
        "expected_lock_version": sug["expected_lock_version"],
    }
    if sug["suggested_due"]:
        payload["due_date"] = sug["suggested_due"]
    if sug["suggested_priority"]:
        payload["priority"] = sug["suggested_priority"]
    apply = admin.post(
        "/api/projects/ELEC/schedule/cascade/apply",
        json={
            "analysis_token": body["analysis_token"],
            "note": "进度链应对",
            "sync_priority": True,
            "items": [payload],
        },
    )
    assert apply.status_code == 200, apply.text
    assert apply.json()["count"] == 1
    updated = admin.get(f"/api/issues/{successor['key']}").json()
    assert updated["priority"] == "medium"
    if sug["suggested_due"]:
        assert updated["due_date"] == sug["suggested_due"]


def test_admin_default_portfolio_view_prefers_larger_tower(admin):
    me = admin.get("/api/auth/me").json()
    assert me["portfolio_access"] is True
    assert me["default_portfolio_view"] in ("delivery", "ongoing")
    delivery_n = len(admin.get("/api/portfolio/overview").json()["projects"])
    ongoing_n = len(admin.get("/api/portfolio/ongoing").json()["projects"])
    need = max(1, delivery_n - ongoing_n + 1)
    for index in range(need):
        created = admin.post("/api/projects", json={
            "key": f"DPOV{index}",
            "name": f"默认持续 {index}",
            "profile": "electrical",
            "work_mode": "ongoing",
        })
        assert created.status_code == 200, created.text
    assert admin.get("/api/auth/me").json()["default_portfolio_view"] == "ongoing"


def test_portfolio_overview_for_admin(admin):
    me = admin.get("/api/auth/me").json()
    assert me["portfolio_access"] is True
    assert me.get("default_portfolio_view") in ("delivery", "ongoing")
    body = admin.get("/api/portfolio/overview").json()
    assert body["portfolio_access"] is True
    assert body.get("scope") == "all"
    assert "totals" in body and "projects" in body
    assert "recent_activity" in body
    assert isinstance(body["recent_activity"], list)
    elec = next(p for p in body["projects"] if p["key"] == "ELEC")
    assert "goal" in elec
    assert "target" in elec
    assert "risk_events" in elec
    assert "risk_items" in elec
    assert len(elec["risk_items"]) == elec["risk_events"]["decisions"]
    assert "risk_rank" in elec
    assert "last_activity" in elec
    assert elec["goal"]["pace"] in {"ahead", "on_track", "behind"}
    assert "points" in elec["completion"]


def test_portfolio_overview_skips_ongoing_projects(admin):
    created = admin.post("/api/projects", json={
        "key": "PORTO",
        "name": "组合外持续项目",
        "profile": "electrical",
        "work_mode": "ongoing",
    })
    assert created.status_code == 200, created.text
    body = admin.get("/api/portfolio/overview").json()
    assert all(project["key"] != "PORTO" for project in body["projects"])
    apply = admin.post(
        "/api/projects/PORTO/schedule/cascade/apply",
        json={
            "analysis_token": "a" * 64,
            "items": [{
                "key": "PORTO-1",
                "due_date": "2099-12-30",
                "expected_lock_version": 1,
            }],
        },
    )
    assert apply.status_code == 400
    assert apply.json()["detail"] == "持续项目不支持项目级排期顺延"


def test_ongoing_portfolio_overview(admin):
    created = admin.post("/api/projects", json={
        "key": "ONGPF",
        "name": "持续运维组合",
        "profile": "electrical",
        "work_mode": "ongoing",
        "description": "持续项目吞吐面板示例",
    })
    assert created.status_code == 200, created.text
    issue = admin.post("/api/issues", json={
        "project": "ONGPF",
        "title": "运维积压项",
        "type": "task",
        "due_date": "2099-12-01",
    })
    assert issue.status_code == 200, issue.text

    body = admin.get("/api/portfolio/ongoing").json()
    assert body["portfolio_access"] is True
    assert body["scope"] == "all"
    assert "totals" in body
    card = next(project for project in body["projects"] if project["key"] == "ONGPF")
    assert card["work_mode"] == "ongoing"
    assert card["goal"]["open"] >= 1
    assert "pressure_rank" in card
    assert "throughput" in card and len(card["throughput"]) >= 2
    assert set(card["queues"]) == {"urgent", "stuck", "unassigned"}
    assert set(card["backlog"]) >= {"overdue", "blocked", "stale", "unassigned"}
    assert "recent_activity" in body
    assert any(item["project_key"] == "ONGPF" for item in body["recent_activity"])
    assert card.get("last_activity") is not None
    delivery = admin.get("/api/portfolio/overview").json()
    assert all(project["key"] != "ONGPF" for project in delivery["projects"])


def test_portfolio_recent_activity_spans_projects(admin):
    """Busy projects must not monopolize the ongoing tower activity feed."""
    busy = admin.post("/api/projects", json={
        "key": "BUSY1",
        "name": "忙碌运维",
        "profile": "electrical",
        "work_mode": "ongoing",
    })
    quiet = admin.post("/api/projects", json={
        "key": "QUIET",
        "name": "安静运维",
        "profile": "electrical",
        "work_mode": "ongoing",
    })
    assert busy.status_code == 200, busy.text
    assert quiet.status_code == 200, quiet.text

    for index in range(12):
        created = admin.post("/api/issues", json={
            "project": "BUSY1",
            "title": f"忙碌项{index}",
            "type": "task",
            "due_date": "2099-12-01",
        })
        assert created.status_code == 200, created.text
    quiet_issue = admin.post("/api/issues", json={
        "project": "QUIET",
        "title": "安静项",
        "type": "task",
        "due_date": "2099-12-01",
    })
    assert quiet_issue.status_code == 200, quiet_issue.text

    body = admin.get("/api/portfolio/ongoing").json()
    keys = {item["project_key"] for item in body["recent_activity"]}
    assert "BUSY1" in keys
    assert "QUIET" in keys
    busy_count = sum(1 for item in body["recent_activity"] if item["project_key"] == "BUSY1")
    assert busy_count <= 10


def test_completion_curve_monotone_denom(admin):
    admin.post("/api/issues", json={**ISSUE, "title": "曲线A"})
    done = admin.post("/api/issues", json={**ISSUE, "title": "曲线B"}).json()
    assert admin.patch(
        f"/api/issues/{done['key']}",
        json={"status_id": _status(admin, "研发中")},
    ).status_code == 200
    assert admin.patch(
        f"/api/issues/{done['key']}",
        json={"status_id": _status(admin, "已完成")},
    ).status_code == 200
    curve = admin.get("/api/projects/ELEC/reports/completion-curve?days=30").json()
    assert curve["days"] >= 1
    assert 0 <= curve["actual_pct"] <= 100
    assert len(curve["points"]) == curve["days"]
    # Done counts never decrease day over day for cumulative finished work in scope.
    dones = [p["done"] for p in curve["points"]]
    assert dones == sorted(dones)


def test_cascade_is_cycle_safe(admin):
    first = admin.post(
        "/api/issues", json={**ISSUE, "title": "循环 A", "due_date": "2099-01-10"}
    ).json()
    second = admin.post(
        "/api/issues", json={**ISSUE, "title": "循环 B", "due_date": "2099-01-11"}
    ).json()
    _link(admin, first["key"], second["key"])
    _link(admin, second["key"], first["key"])

    analysis = admin.get("/api/projects/ELEC/schedule/cascade")
    assert analysis.status_code == 200
    data = analysis.json()
    assert sorted([first["key"], second["key"]]) in data["data_quality"]["cycles"]
    assert any("循环" in warning for warning in data["data_quality"]["warnings"])


def test_cascade_uses_locked_baseline_and_latest_predecessor(admin):
    assert admin.put(
        "/api/projects/ELEC",
        json={
            "plan_start_date": "2099-01-01",
            "target_date": "2099-03-31",
            "settings": {
                "sla_calendar": "business",
                "sla_business_hours": {
                    "start": "09:00",
                    "end": "18:00",
                    "workdays": [1, 2, 3, 4, 5],
                },
            },
        },
    ).status_code == 200
    first = admin.post(
        "/api/issues",
        json={
            **ISSUE,
            "title": "前置 A",
            "planned_start_date": "2099-01-01",
            "due_date": "2099-01-05",
        },
    ).json()
    second = admin.post(
        "/api/issues",
        json={
            **ISSUE,
            "title": "前置 B",
            "planned_start_date": "2099-01-01",
            "due_date": "2099-01-06",
        },
    ).json()
    successor = admin.post(
        "/api/issues",
        json={
            **ISSUE,
            "title": "多前置后继",
            "planned_start_date": "2099-01-07",
            "due_date": "2099-01-12",
        },
    ).json()
    _link(admin, first["key"], successor["key"])
    _link(admin, second["key"], successor["key"])
    published = admin.post("/api/projects/ELEC/schedule-baseline", json={})
    assert published.status_code == 200, published.text

    for issue, due in ((first, "2099-01-10"), (second, "2099-01-08")):
        updated = admin.patch(
            f"/api/issues/{issue['key']}",
            json={"due_date": due, "expected_version": issue["lock_version"]},
        )
        assert updated.status_code == 200, updated.text

    one = admin.get("/api/projects/ELEC/schedule/cascade").json()
    two = admin.get("/api/projects/ELEC/schedule/cascade").json()
    suggestion = next(row for row in one["suggestions"] if row["key"] == successor["key"])
    repeated = next(row for row in two["suggestions"] if row["key"] == successor["key"])
    assert one["baseline_mode"] == "locked"
    assert one["baseline_version"] == 1
    assert suggestion["forecast_due"] == repeated["forecast_due"]
    assert one["analysis_token"] == two["analysis_token"]
    assert suggestion["baseline_due"] == "2099-01-12"
    assert suggestion["forecast_due"] > suggestion["current_due"]
    assert suggestion["constraining_predecessors"]
    assert suggestion["confidence"] == "high"


def test_cascade_rejects_stale_or_arbitrary_changes_and_records_decision(admin):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=5)).isoformat()
    blocker = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "安全应用前置", "due_date": yesterday},
    ).json()
    successor = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "安全应用后继", "due_date": later},
    ).json()
    _link(admin, blocker["key"], successor["key"])
    analysis = admin.post("/api/projects/ELEC/schedule/analysis", json={}).json()
    suggestion = next(
        row for row in analysis["suggestions"] if row["key"] == successor["key"]
    )

    preview = admin.post(
        "/api/projects/ELEC/schedule/cascade/preview",
        json={
            "analysis_token": analysis["analysis_token"],
            "keys": [successor["key"]],
        },
    )
    assert preview.status_code == 200
    assert preview.json()["stale"] is False

    arbitrary = admin.post(
        "/api/projects/ELEC/schedule/cascade/apply",
        json={
            "analysis_token": analysis["analysis_token"],
            "items": [
                {
                    "key": successor["key"],
                    "due_date": "2099-12-30",
                    "expected_lock_version": suggestion["expected_lock_version"],
                }
            ],
        },
    )
    assert arbitrary.status_code == 200
    assert arbitrary.json()["skipped"][0]["reason"] == "not_suggested"

    decision = admin.post(
        f"/api/projects/ELEC/schedule/risks/{successor['key']}/decision",
        json={
            "action": "reject",
            "expected_version": suggestion["risk_lock_version"],
            "note": "保留当前承诺",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "rejected"

    changed = admin.patch(
        f"/api/issues/{blocker['key']}",
        json={
            "due_date": date.today().isoformat(),
            "expected_version": blocker["lock_version"],
        },
    )
    assert changed.status_code == 200
    stale = admin.post(
        "/api/projects/ELEC/schedule/cascade/apply",
        json={
            "analysis_token": analysis["analysis_token"],
            "items": [
                {
                    "key": successor["key"],
                    "due_date": suggestion["suggested_due"],
                    "expected_lock_version": suggestion["expected_lock_version"],
                }
            ],
        },
    )
    assert stale.status_code == 200
    assert stale.json()["stale"] is True
    assert stale.json()["skipped"][0]["reason"] == "stale_analysis"


def test_pm_risk_alerts_shadow_and_dedupe(admin):
    overdue = (date.today() - timedelta(days=12)).isoformat()
    later = (date.today() + timedelta(days=2)).isoformat()
    blocker = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "告警前置", "due_date": overdue},
    ).json()
    successor = admin.post(
        "/api/issues",
        json={**ISSUE, "title": "告警后继", "due_date": later},
    ).json()
    _link(admin, blocker["key"], successor["key"])
    with session_factory()() as db:
        shadow = pm_risk_alerts.scan(db, shadow=True)
        assert shadow["candidates"] >= 1
        assert db.scalar(select(func.count(Notification.id))) == 0
    with session_factory()() as db:
        live = pm_risk_alerts.scan(db, shadow=False)
        assert live["notified"] >= 1
        first_count = db.scalar(select(func.count(Notification.id)))
        assert first_count >= 1
    with session_factory()() as db:
        repeated = pm_risk_alerts.scan(db, shadow=False)
        assert repeated["notified"] == 0
        assert db.scalar(select(func.count(Notification.id))) == first_count
