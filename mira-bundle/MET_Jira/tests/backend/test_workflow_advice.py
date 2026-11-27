from backend.services import workflow_audit

STATUSES = [
    ("待评估", "todo"),
    ("重新打开", "todo"),
    ("研发中", "doing"),
    ("已完成", "done"),
    ("已驳回", "done"),
]


def _ids(level: str, suggestions: list[dict]) -> set[str]:
    return {item["id"] for item in suggestions if item["level"] == level}


def test_lint_codes_cover_structural_problems():
    findings = workflow_audit.lint_flow(STATUSES, {
        "研发中": ["研发中"],
        "已完成": [],
    }, "task")
    codes = {item.code for item in findings}
    assert "initial_not_todo" in codes
    assert "self_loop" in codes
    assert "no_reopen" in codes
    assert findings[0].statuses


def test_repairs_fix_errors_without_forcing_reopen():
    current = {"待评估": ["研发中"], "研发中": ["待评估"]}
    suggestions = workflow_audit.suggest_repairs(
        STATUSES, current, issue_type="task",
        template={"待评估": ["研发中"], "研发中": ["已完成"], "已完成": ["重新打开"]},
        issue_counts={"研发中": 4, "已驳回": 2},
    )
    required = _ids("required", suggestions)
    assert required
    assert "no_reopen" not in required
    repaired = workflow_audit.apply_repairs(current, suggestions, required)
    errors = [
        item for item in workflow_audit.lint_flow(STATUSES, repaired, "task")
        if item.level == "error"
    ]
    assert errors == []
    assert "重新打开" not in repaired.get("已完成", [])


def test_issue_count_recommends_connecting_an_unused_status():
    current = {"待评估": ["研发中"], "研发中": ["已完成"], "已完成": ["重新打开"],
               "重新打开": ["研发中"]}
    suggestions = workflow_audit.suggest_repairs(
        STATUSES, current, issue_type="task", template={}, issue_counts={"已驳回": 3},
    )
    rejected = next(item for item in suggestions if item["statuses"] == ["已驳回"])
    assert rejected["level"] == "recommended"
    assert rejected["issue_count"] == 3
    assert rejected["add"]


def test_empty_flow_offers_a_valid_template():
    template = {"待评估": ["研发中"], "研发中": ["已完成"]}
    suggestions = workflow_audit.suggest_repairs(
        STATUSES, {}, issue_type="task", template=template, issue_counts={},
    )
    assert [item["id"] for item in suggestions] == ["empty"]
    assert suggestions[0]["level"] == "required"
    repaired = workflow_audit.apply_repairs({}, suggestions, {"empty"})
    assert workflow_audit.lint_flow(STATUSES, repaired, "task")
    assert not any(
        item.level == "error" for item in workflow_audit.lint_flow(STATUSES, repaired, "task")
    )


SUPPORT = "Support工艺和产品提供相关的报告，并根据需要和每个客户开会"
INTAKE = "测试来料不少于30PCS的6287 SHD的流阻系数R值"
COMPARE = "收集并对比所测试SHD的R值与工艺GPC的对应关系，每周五上午汇总，下午汇报"
TRACK = "跟踪博芯微2nd Source交期，每周更新交期和进度"
CUSTOM = [
    (SUPPORT, "doing"),
    (INTAKE, "doing"),
    (COMPARE, "doing"),
    (TRACK, "doing"),
]


def _errors(statuses, rules, issue_type):
    return [
        item for item in workflow_audit.lint_flow(statuses, rules, issue_type)
        if item.level == "error"
    ]


def test_restore_referenced_template_statuses_makes_the_flow_valid():
    rules = {
        INTAKE: [COMPARE, "已驳回"],
        COMPARE: [SUPPORT, INTAKE, "已驳回"],
        TRACK: [COMPARE, SUPPORT],
        SUPPORT: ["试装", COMPARE, "已驳回"],
        "试装": ["终检", SUPPORT, "已驳回"],
        "终检": ["已完成", "试装", "已驳回"],
        "已完成": [TRACK],
        "已驳回": [INTAKE],
    }
    suggestions = workflow_audit.suggest_repairs(CUSTOM, rules, issue_type="bug")
    required = [item for item in suggestions if item["level"] == "required"]
    assert [item["id"] for item in required] == ["restore_status"]
    created = {item["name"]: item["category"] for item in required[0]["create"]}
    assert created == {"试装": "doing", "终检": "doing", "已完成": "done", "已驳回": "done"}
    pairs, repaired, _creates = workflow_audit.materialize_repairs(
        CUSTOM, rules, suggestions, {item["id"] for item in required},
    )
    assert _errors(pairs, repaired, "bug") == []
    assert "重新打开" not in repaired.get("已完成", [])


def test_doing_cycle_gains_a_completion_status():
    statuses = [("设计", "doing"), ("复核", "doing")]
    rules = {"设计": ["复核"], "复核": ["设计"]}
    pairs, repaired, creates = workflow_audit.heal_flow(statuses, rules, issue_type="task")
    assert _errors(pairs, repaired, "task") == []
    assert creates == [{"name": "已完成", "category": "done", "color": "#30d158"}]
    assert "重新打开" not in repaired.get("已完成", [])
    assert "已完成" in repaired["设计"] or "已完成" in repaired["复核"]


def test_a_stray_name_is_dropped_and_template_statuses_stay():
    rules = {
        SUPPORT: ["试装", "临时名字"],
        "试装": ["已完成"],
        "已完成": [SUPPORT],
        "临时名字": [SUPPORT],
    }
    pairs, repaired, creates = workflow_audit.heal_flow(CUSTOM, rules, issue_type="bug")
    assert _errors(pairs, repaired, "bug") == []
    assert {item["name"] for item in creates} >= {"试装", "已完成"}
    assert "临时名字" not in {item["name"] for item in creates}
    mentioned = set(repaired) | {target for targets in repaired.values() for target in targets}
    assert "临时名字" not in mentioned


def test_an_empty_doing_flow_receives_a_finish_path():
    statuses = [("设计", "doing"), ("复核", "doing")]
    pairs, repaired, creates = workflow_audit.heal_flow(statuses, {}, issue_type="task")
    assert _errors(pairs, repaired, "task") == []
    assert creates == [{"name": "已完成", "category": "done", "color": "#30d158"}]
    assert "已完成" in {target for targets in repaired.values() for target in targets}


def test_unknown_custom_name_is_removed_and_the_flow_stays_valid():
    rules = {"待评估": ["不存在", "研发中"], "研发中": ["已完成"]}
    suggestions = workflow_audit.suggest_repairs(STATUSES, rules, issue_type="task")
    assert any(item["id"] == "unknown_status" for item in suggestions)
    assert all(item["id"] != "restore_status" for item in suggestions)
    required = {item["id"] for item in suggestions if item["level"] == "required"}
    pairs, repaired, creates = workflow_audit.materialize_repairs(
        STATUSES, rules, suggestions, required,
    )
    assert creates == []
    assert _errors(pairs, repaired, "task") == []
    assert "不存在" not in {target for targets in repaired.values() for target in targets}


def test_optimize_restores_missing_template_statuses_without_saving(admin):
    detail = admin.get("/api/projects/ELEC").json()
    before_status = [item["name"] for item in detail["statuses"]]
    before_flow = detail["workflows"]
    todo = next(item["name"] for item in detail["statuses"] if item["category"] == "todo")
    current = {todo: ["试装"], "试装": ["终检"], "终检": ["已完成"]}
    advice = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current,
    })
    assert advice.status_code == 200, advice.text
    required = [
        item["id"] for item in advice.json()["suggestions"]
        if item["level"] == "required"
    ]
    assert "restore_status" in required
    preview = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current, "suggestion_ids": required,
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid"] is True
    assert {item["name"] for item in body["new_statuses"]} == {"试装", "终检"}
    after = admin.get("/api/projects/ELEC").json()
    assert [item["name"] for item in after["statuses"]] == before_status
    assert after["workflows"] == before_flow


def test_skipping_the_required_repair_does_not_pretend_the_draft_is_valid(admin):
    detail = admin.get("/api/projects/ELEC").json()
    todo = next(item["name"] for item in detail["statuses"] if item["category"] == "todo")
    current = {todo: ["试装"], "试装": ["终检"], "终检": ["已完成"]}
    advice = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current,
    })
    assert advice.status_code == 200, advice.text
    optional = [
        item["id"] for item in advice.json()["suggestions"] if item["level"] != "required"
    ]
    assert optional
    preview = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current, "suggestion_ids": optional[:1],
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["valid"] is False
    assert "试装" not in {item["name"] for item in body["new_statuses"]}


def test_optimize_endpoint_previews_without_saving(admin, make_user):
    detail = admin.get("/api/projects/ELEC").json()
    before = detail["workflows"]
    statuses = [(item["name"], item["category"]) for item in detail["statuses"]]
    todo = next(name for name, category in statuses if category == "todo")
    doing = next(name for name, category in statuses if category == "doing")
    current = {todo: [doing], doing: [todo]}
    advice = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current,
    })
    assert advice.status_code == 200, advice.text
    body = advice.json()
    assert body["transitions"] is None
    assert body["suggestions"]
    assert any(item["code"] for item in body["findings"])
    required = [item["id"] for item in body["suggestions"] if item["level"] == "required"]
    preview = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize",
        "transitions": current, "suggestion_ids": required,
    })
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is True
    assert preview.json()["diff"]["add"]
    assert admin.get("/api/projects/ELEC").json()["workflows"] == before
    stranger, _user_id = make_user("wf_opt_guest")
    denied = stranger.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "mode": "optimize", "transitions": current,
    })
    assert denied.status_code == 403
