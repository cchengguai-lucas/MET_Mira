from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.db import session_factory
from backend.models import AgentConversation, AgentMessage, AiReport, Project, User
from backend.services import agent, agent_tools, llm

ELEC_ISSUE = {"project": "ELEC", "due_date": "2099-12-31"}


@pytest.fixture()
def fake_llm(monkeypatch):
    """Script the LLM: each call pops the next prepared assistant message."""
    script: list[dict] = []
    calls: list[dict] = []

    def fake_chat(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return script.pop(0)

    monkeypatch.setattr(llm, "config", lambda: llm.LlmConfig(True, "http://fake/v1", "m", 5))
    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "probe", lambda force=False: {"reachable": True,
                                                           "tool_mode": "native"})
    monkeypatch.setattr(llm, "stream_chat", lambda messages, **kw: iter(["## 周报\n", "无风险"]))
    return script, calls


def _tool_call(name: str, args: dict) -> dict:
    return {"content": None, "tool_calls": [
        {"id": f"call-{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def test_workflow_agent_drafts_then_applies_after_confirmation(admin):
    draft = {"待评估": ["已完成"], "已完成": ["待评估"]}
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        ctx = agent_tools.AgentContext(db, user, "ELEC")
        analysis = agent_tools.run_tool(
            ctx, "analyze_workflow", {"project": "ELEC", "issue_type": "task"}
        )
        assert analysis["project"] == "ELEC"
        assert analysis["transitions"]

        preview = agent_tools.run_tool(
            ctx,
            "suggest_workflow",
            {"project": "ELEC", "issue_type": "task", "transitions": draft},
        )
        assert preview["valid"] is True
        assert preview["transitions"] == draft
        assert preview["diff"]

        invalid = agent_tools.run_tool(
            ctx,
            "suggest_workflow",
            {
                "project": "ELEC",
                "issue_type": "task",
                "transitions": {"待评估": ["不存在"]},
            },
        )
        assert invalid["valid"] is False

        pending = agent_tools.run_tool(
            ctx,
            "apply_workflow",
            {"project": "ELEC", "issue_type": "task", "transitions": draft},
        )
        assert pending["pending_action_id"]

    # Proposal alone changes nothing.
    before = admin.get("/api/projects/ELEC").json()
    assert before["workflows"]["task"] != draft

    confirmed = admin.post(
        f"/api/agent/actions/{pending['pending_action_id']}/confirm", json={}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["result"]["transitions"] == draft
    after = admin.get("/api/projects/ELEC").json()
    assert after["workflows"]["task"] == draft


def test_intent_router_keeps_new_question_isolated(app, admin):
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user is not None
        snapshot = agent.prepare_chat(
            db,
            user,
            [
                {"role": "user", "content": "状态门禁是什么意思"},
                {"role": "assistant", "content": "说明"},
                {"role": "user", "content": "我负责的未完成问题有哪些"},
            ],
            "ELEC",
            native=True,
        )
    assert snapshot.question == "我负责的未完成问题有哪些"
    assert snapshot.intent == "data"
    assert "my_work" in snapshot.allowed_tools
    assert "product_help" not in snapshot.allowed_tools
    assert "open_page" not in snapshot.allowed_tools


def test_server_owned_conversation_idempotency_and_memory(admin, fake_llm):
    script, calls = fake_llm
    script.append({"content": "第一条回答"})
    first = _events(admin.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "你好"}],
            "client_message_id": "message-one",
            "project": "ELEC",
        },
    ))
    conversation_id = next(
        event["id"] for event in first if event["type"] == "conversation"
    )
    script.append({"content": "第二条回答"})
    second = _events(admin.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "继续"}],
            "conversation_id": conversation_id,
            "client_message_id": "message-two",
            "project": "ELEC",
        },
    ))
    assert any(event.get("text") == "第二条回答" for event in second)
    assert any(
        message.get("content") == "第一条回答"
        for message in calls[-1]["messages"]
    )
    replay = _events(admin.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "继续"}],
            "conversation_id": conversation_id,
            "client_message_id": "message-two",
            "project": "ELEC",
        },
    ))
    assert any(event.get("text") == "第二条回答" for event in replay)
    with session_factory()() as db:
        conversation = db.get(AgentConversation, conversation_id)
        assert conversation is not None and conversation.summary
        messages = list(db.scalars(
            select(AgentMessage).where(
                AgentMessage.conversation_id == conversation_id
            )
        ))
        assert len(messages) == 4

    created = admin.post(
        "/api/agent/memories",
        json={"content": "风险汇报按 Gate 分组", "project": "ELEC"},
    )
    assert created.status_code == 200
    memories = admin.get("/api/agent/memories?project=ELEC").json()["memories"]
    assert any(row["content"] == "风险汇报按 Gate 分组" for row in memories)
    assert admin.delete(f"/api/agent/memories/{created.json()['id']}").status_code == 200


def test_chat_reads_and_proposes_write(admin, fake_llm):
    script, calls = fake_llm
    issue = admin.post("/api/issues", json={**ELEC_ISSUE, "title": "真空泵异响"}).json()
    script += [
        _tool_call("search_issues", {"jql": "project = ELEC AND text ~ 真空泵"}),
        _tool_call("transition_issue", {"key": issue["key"], "status": "方案讨论"}),
        {"content": "已生成待确认操作。"},
    ]
    response = admin.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "把真空泵那条推进到方案讨论"}],
        "project": "ELEC"})
    events = _events(response)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "tool_call" and "action" in kinds and kinds[-1] == "done"
    tool_message = calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and issue["key"] in tool_message["content"]
    action = next(e["action"] for e in events if e["type"] == "action")
    assert admin.get(f"/api/issues/{issue['key']}").json()["status"]["name"] == "待评估"

    confirmed = admin.post(f"/api/agent/actions/{action['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    assert admin.get(f"/api/issues/{issue['key']}").json()["status"]["name"] == "方案讨论"
    again = admin.post(f"/api/agent/actions/{action['id']}/confirm", json={})
    assert again.status_code == 400


def test_invalid_tool_args_are_reported_back(admin, fake_llm):
    script, calls = fake_llm
    script += [
        _tool_call("create_issue", {"title": "x", "machines": ["不存在的机台"]}),
        {"content": "机台不存在"},
    ]
    events = _events(admin.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "建一个"}],
        "project": "ELEC",
    }))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and "机台" in result["summary"]
    assert admin.get("/api/agent/actions").json()["actions"] == []


def test_draft_triage_commit_and_report(admin, fake_llm, monkeypatch):
    script, _ = fake_llm
    prompts: list[list[dict]] = []
    monkeypatch.setattr(llm, "stream_chat", lambda messages, **kw: (
        prompts.append(messages), iter(["## 周报\n", "无风险"]))[1])
    admin.post("/api/issues", json={**ELEC_ISSUE, "title": "CMB-1 PM-A 温度报警阈值调整"})
    script += [
        {"content": json.dumps({"issues": [{
            "type": "epic", "title": "PM-A 温度报警优化", "description": "背景",
            "priority": "high", "machines": ["CMB-1/PM-A", "XYZ"], "update_scope": ["软件", "乱填"],
            "children": [{"title": "修改阈值"}, {"title": "上机验证"}]}]}, ensure_ascii=False)},
        {"content": json.dumps({"duplicates": [{"key": "ELEC-1", "relation": "similar",
                                                "reason": "同一报警"}],
                                "assignee": None, "priority": "high",
                                "priority_reason": "影响生产"}, ensure_ascii=False)},
    ]
    drafted = admin.post("/api/agent/draft", json={"project": "ELEC",
                                                   "text": "PM-A 温度报警太频繁", "split": True})
    assert drafted.status_code == 200, drafted.text
    data = drafted.json()
    draft = data["drafts"][0]
    assert draft["update_scope"] == ["软件"] and draft["unresolved_machines"] == ["XYZ"]
    assert len(draft["machine_ids"]) == 1 and len(draft["children"]) == 2
    assert data["triage"]["duplicates"][0]["key"] == "ELEC-1"

    committed = admin.post("/api/agent/draft/commit", json={"project": "ELEC",
                                                            "drafts": data["drafts"]}).json()
    assert len(committed["created"]) == 3
    epic_key = committed["created"][0]["key"]
    epic = admin.get(f"/api/issues/{epic_key}").json()
    assert len(epic["children"]) == 2

    report = _events(admin.post("/api/agent/report", json={"project": "ELEC"}))
    assert "".join(e.get("text", "") for e in report).startswith("## 周报")
    saved = admin.get("/api/projects/ELEC/ai-reports").json()["reports"]
    assert saved and saved[0]["period"] == "weekly"
    assert {"type": "report", "id": saved[0]["id"]} in report
    page = admin.get(f"/api/projects/ELEC/ai-reports/{saved[0]['id']}/html").text
    assert "本期新建" in page and "新建与完成趋势" in page and "未完成 · 按状态" in page
    assert "交付预测" in page
    prompt = json.loads(prompts[-1][-1]["content"])
    assert prompt["forecast"]["scope"]["remaining"] >= 1 and "metrics" not in prompt
    audits = admin.get("/api/admin/agent/audits").json()["audits"]
    assert {a["kind"] for a in audits} >= {"draft", "triage", "report"}


def test_draft_split_retries_then_folds_siblings(admin, fake_llm):
    """split=true must not return a lone leaf issue; retry / fold into epic+children."""
    script, calls = fake_llm
    script += [
        # First attempt ignores split (no children) → triggers retry.
        {"content": json.dumps({"issues": [{
            "type": "requirement", "title": "部署推理服务", "description": "整包部署",
            "priority": "medium", "children": [],
        }]}, ensure_ascii=False)},
        # Retry still returns siblings without nested children → fold.
        {"content": json.dumps({"issues": [
            {"type": "requirement", "title": "推理服务部署", "description": "总览",
             "priority": "high"},
            {"type": "task", "title": "环境准备", "description": ""},
            {"type": "task", "title": "上线验证", "description": ""},
        ]}, ensure_ascii=False)},
        {"content": json.dumps({
            "duplicates": [], "assignee": None, "priority": "high", "priority_reason": "",
        })},
    ]
    drafted = admin.post("/api/agent/draft", json={
        "project": "ELEC", "text": "模型推理服务部署到 CMB-2", "split": True,
    })
    assert drafted.status_code == 200, drafted.text
    draft = drafted.json()["drafts"][0]
    assert draft["type"] == "epic"
    assert [c["title"] for c in draft["children"]] == ["环境准备", "上线验证"]
    assert len(calls) >= 2
    assert "必须只输出 1 个 type=epic" in calls[0]["messages"][0]["content"]


def test_draft_split_fails_without_children(admin, fake_llm):
    script, _ = fake_llm
    leaf = {"content": json.dumps({"issues": [{
        "type": "requirement", "title": "单条", "children": [],
    }]}, ensure_ascii=False)}
    script += [leaf, leaf]
    drafted = admin.post("/api/agent/draft", json={
        "project": "ELEC", "text": "随便写一句", "split": True,
    })
    assert drafted.status_code == 400
    assert "拆分" in drafted.json()["detail"]


def test_draft_uses_project_profile_custom_fields(admin, fake_llm):
    script, calls = fake_llm
    assert admin.post("/api/projects", json={
        "key": "AIMECH", "name": "AI 机械项目", "profile": "mechanical",
    }).status_code == 200
    material = admin.post("/api/assets", json={
        "kind": "material", "code": "SUS304", "name": "不锈钢 304",
    })
    assert material.status_code == 200, material.text
    script += [
        {"content": json.dumps({"issues": [{
            "title": "夹具变更",
            "custom_fields": {"drawing_no": "DWG-AI", "material": "SUS304"},
        }]}, ensure_ascii=False)},
        {"content": json.dumps({
            "duplicates": [], "assignee": None, "priority": "medium",
            "priority_reason": "",
        })},
    ]

    response = admin.post("/api/agent/draft", json={
        "project": "AIMECH", "text": "按 DWG-AI 修改 SUS304 夹具",
    })
    assert response.status_code == 200, response.text
    custom = response.json()["drafts"][0]["custom_fields"]
    assert custom["drawing_no"] == "DWG-AI"
    assert custom["material"] == material.json()["id"]
    prompt = calls[0]["messages"][0]["content"]
    assert "机械" in prompt and "drawing_no" in prompt


def test_jql_assist_validates_and_retries(admin, fake_llm):
    script, calls = fake_llm
    admin.post("/api/issues", json={**ELEC_ISSUE, "title": "真空泵异响", "priority": "high"})
    script += [
        {"content": json.dumps({"jql": "priority = high AND colour = red",
                                "explanation": "x", "question": None})},
        {"content": json.dumps({"jql": "project = ELEC AND priority = high AND assignee = admin",
                                "explanation": "ELEC 中 admin 负责的高优先级", "question": None},
                               ensure_ascii=False)},
    ]
    resp = admin.post("/api/agent/jql", json={
        "messages": [{"role": "user", "content": "admin 负责的高优先级问题"}],
        "current_jql": "project = ELEC"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["jql"].endswith("assignee = admin") and data["error"] is None
    assert data["total"] == 0
    system = calls[0]["messages"][0]["content"]
    assert "当前筛选条件：project = ELEC" in system and "admin（" in system
    assert "解析失败" in calls[1]["messages"][-1]["content"]

    script.append({"content": json.dumps({"jql": "", "explanation": "",
                                          "question": "你指的是哪个项目？"}, ensure_ascii=False)})
    asked = admin.post("/api/agent/jql", json={
        "messages": [{"role": "user", "content": "那个东西"}]}).json()
    assert asked["jql"] == "" and asked["question"] == "你指的是哪个项目？"
    assert admin.post("/api/agent/jql", json={"messages": []}).status_code == 400


def test_triage_heuristic_without_llm(admin):
    admin.post("/api/issues", json={**ELEC_ISSUE, "title": "LL 腔门传感器失效"})
    result = admin.post("/api/agent/triage", json={"project": "ELEC",
                                                   "title": "LL 腔门传感器失效报警"}).json()
    assert result["source"] == "heuristic"
    assert result["duplicates"][0]["relation"] == "duplicate"


def test_nav_intent_requires_explicit_jump_words():
    from backend.services.agent_tools import nav_intent

    assert nav_intent("带我去设置流程审批人的地方")
    assert nav_intent("打开项目设置")
    assert nav_intent("可见范围在哪配置？")
    assert not nav_intent("那设置的项目是所有人可见吗")
    assert not nav_intent("项目可见性是什么意思")
    assert not nav_intent("系统管理员能看到所有项目吗")


def test_open_page_proposes_navigation(admin, fake_llm):
    from backend.services.agent_tools import resolve_nav, suggest_pages

    nav = resolve_nav("工作流", project_key="ELEC")
    assert nav["path"] == "/projects/ELEC/settings?section=workflow"
    assert "工作流" in nav["label"]
    pages = suggest_pages("带我去设置流程审批人的地方")
    assert pages and pages[0]["id"] == "project.settings.workflow"

    script, _calls = fake_llm
    # Model answers without calling open_page — safety net must still offer the jump.
    script += [{"content": "审批人在「状态与工作流」里配置。"}]
    events = _events(admin.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "带我去设置流程审批人的地方"}],
        "project": "ELEC",
    }))
    action = next(e["action"] for e in events if e["type"] == "action")
    assert action["tool"] == "open_page"
    assert action["args"]["destination"] == "project.settings.workflow"
    confirmed = admin.post(f"/api/agent/actions/{action['id']}/confirm", json={})
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["result"]
    assert result["navigate"] is True
    assert result["path"] == "/projects/ELEC/settings?section=workflow"


def test_factual_question_does_not_auto_jump(admin, fake_llm):
    script, _calls = fake_llm
    script += [
        _tool_call("product_help", {"query": "项目可见范围"}),
        {"content": "默认公开：所有登录用户可见；也可设为仅成员。"},
    ]
    events = _events(admin.post("/api/agent/chat", json={
        "messages": [{"role": "user", "content": "那设置的项目是所有人可见吗"}],
    }))
    assert not any(e.get("type") == "action" for e in events)
    assert not any(e.get("name") == "open_page" for e in events if e.get("type") == "tool_call")
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert "仅成员" in text or "可见" in text


def test_delete_ai_report_permissions(admin, make_user):
    member, member_id = make_user("writer", "写报告的人")
    other, other_id = make_user("reader", "旁观者")
    for user_id in (member_id, other_id):
        assert admin.post("/api/projects/ELEC/members",
                          json={"user_id": user_id, "role": "electrical"}).status_code == 200
    with session_factory()() as db:
        project_id = db.scalar(select(Project.id).where(Project.key == "ELEC"))
        mine = AiReport(project_id=project_id, period="weekly", title="我的", content="x",
                        created_by_id=member_id)
        auto = AiReport(project_id=project_id, period="weekly", title="自动", content="y",
                        scheduled=True)
        db.add_all([mine, auto])
        db.commit()
        mine_id, auto_id = mine.id, auto.id

    flags = {r["id"]: r["can_delete"]
             for r in member.get("/api/projects/ELEC/ai-reports").json()["reports"]}
    assert flags == {mine_id: True, auto_id: False}
    assert other.delete(f"/api/projects/ELEC/ai-reports/{mine_id}").status_code == 403
    assert member.delete(f"/api/projects/ELEC/ai-reports/{auto_id}").status_code == 403
    assert member.delete(f"/api/projects/ELEC/ai-reports/{mine_id}").status_code == 200
    assert admin.delete(f"/api/projects/ELEC/ai-reports/{auto_id}").status_code == 200
    assert admin.delete(f"/api/projects/ELEC/ai-reports/{auto_id}").status_code == 404
    assert admin.get("/api/projects/ELEC/ai-reports").json()["reports"] == []


def test_polish_restores_embeds_and_keeps_language(admin, fake_llm, monkeypatch):
    seen: dict = {}

    def fake_stream(messages, **kw):
        seen.update(messages=messages, **kw)
        return iter(["## 现象\n- 划伤\n\n⟦", "1⟧\n\n参见 ⟦2", "⟧"])

    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    image = "![paste.png](/api/attachments/7?inline=true)"
    link = "[规格书](https://example.com/spec)"
    lost = "![b.png](/api/attachments/8?inline=true)"
    text = f"现象 划伤\n{image}\n参见 {link}\n{lost}"
    events = _events(admin.post("/api/agent/polish", json={"text": text, "title": "缺陷分类"},
                                headers={"X-MetJira-Lang": "en"}))
    out = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert out == f"## 现象\n- 划伤\n\n{image}\n\n参见 {link}\n\n{lost}"
    prompt = seen["messages"][1]["content"]
    assert "/api/attachments" not in prompt and "⟦3⟧" in prompt
    assert "严禁据此扩写" in prompt
    assert seen["localized"] is False
    assert seen["temperature"] == 0.1
    assert events[-1]["type"] == "done"


def test_polish_skips_llm_when_only_images_or_whitespace(admin, fake_llm, monkeypatch):
    called = False

    def fail_if_called(messages, **kw):
        nonlocal called
        called = True
        return iter(["不应该生成"])

    monkeypatch.setattr(llm, "stream_chat", fail_if_called)

    for text in (
        "   \n\t ",
        "![paste-1.png](/api/attachments/51?inline=true)\n",
        "![a.png](/api/attachments/51?inline=true)\n![b.png](/api/attachments/52?inline=true)\n",
        "---\n![a.png](/api/attachments/51?inline=true)",
    ):
        events = _events(admin.post("/api/agent/polish", json={
            "text": text,
            "title": "Alerts增加功能",
            "kind": "delivery",
        }))
        out = "".join(e.get("text", "") for e in events if e["type"] == "delta")
        assert out == text
        assert events[-1]["type"] == "done"
    assert called is False


def test_workflow_draft_previews_canvas_without_saving(admin, fake_llm, make_user):
    detail = admin.get("/api/projects/ELEC").json()
    by_category: dict[str, list[str]] = {}
    for status in detail["statuses"]:
        by_category.setdefault(status["category"], []).append(status["name"])
    todo, doing, done = by_category["todo"][0], by_category["doing"][0], by_category["done"][0]
    current = {todo: [doing], doing: [done]}
    proposed = {todo: [doing], doing: [done, todo], done: [todo]}
    script, calls = fake_llm
    assert admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "messages": [],
    }).status_code == 400
    assert admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "nope",
        "messages": [{"role": "user", "content": "改一下"}],
    }).status_code == 400
    stranger, _user_id = make_user("wf_guest")
    assert stranger.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "transitions": current,
        "messages": [{"role": "user", "content": "改一下"}],
    }).status_code == 403

    script += [
        {"content": json.dumps({
            "explanation": "bad", "transitions": {todo: ["不存在的状态"]}, "question": None,
        }, ensure_ascii=False)},
        {"content": json.dumps({
            "explanation": "补上退回和重新打开", "transitions": proposed, "question": None,
        }, ensure_ascii=False)},
    ]
    before = detail["workflows"]
    response = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC",
        "issue_type": "task",
        "transitions": current,
        "messages": [{"role": "user", "content": "研发中可以退回，完成后可以重新打开"}],
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["valid"] is True
    assert data["transitions"][doing] == [done, todo]
    assert {"from": done, "to": todo} in data["diff"]["add"]
    assert {"from": doing, "to": todo} in data["diff"]["add"]
    assert "不能用" in calls[1]["messages"][-1]["content"]
    assert "当前画布上的草稿" in calls[0]["messages"][0]["content"]
    assert admin.get("/api/projects/ELEC").json()["workflows"] == before

    script.append({"content": json.dumps({
        "explanation": "", "transitions": None, "question": "要保留驳回吗？",
    }, ensure_ascii=False)})
    asked = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task", "transitions": current,
        "messages": [{"role": "user", "content": "那个改一下"}],
    })
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["transitions"] is None and body["valid"] is False
    assert body["question"] == "要保留驳回吗？"


def test_workflow_draft_can_propose_a_new_status_without_creating_it(admin, fake_llm):
    detail = admin.get("/api/projects/ELEC").json()
    names = [status["name"] for status in detail["statuses"]]
    todo = next(status["name"] for status in detail["statuses"] if status["category"] == "todo")
    doing = next(status["name"] for status in detail["statuses"] if status["category"] == "doing")
    done = next(status["name"] for status in detail["statuses"] if status["category"] == "done"
                and status["name"] != "已驳回")
    script, _calls = fake_llm
    script.append({"content": json.dumps({
        "explanation": "在研发中之后增加联调",
        "transitions": {todo: [doing], doing: ["联调"], "联调": [done]},
        "new_statuses": [{"name": "联调", "category": "doing"}],
        "question": None,
    }, ensure_ascii=False)})
    response = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC",
        "issue_type": "task",
        "transitions": {todo: [doing], doing: [done]},
        "messages": [{"role": "user", "content": "增加一个进行中的联调状态"}],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["new_statuses"] == [{
        "name": "联调", "category": "doing", "color": "#0a84ff",
    }]
    assert {"from": "联调", "to": done} in body["diff"]["add"]
    assert "联调" not in names
    after = [status["name"] for status in admin.get("/api/projects/ELEC").json()["statuses"]]
    assert "联调" not in after


def test_workflow_draft_styles_known_statuses_and_heals_a_dead_end(admin, fake_llm):
    detail = admin.get("/api/projects/ELEC").json()
    names = [status["name"] for status in detail["statuses"]]
    todo = next(status["name"] for status in detail["statuses"] if status["category"] == "todo")
    doing = next(
        status["name"] for status in detail["statuses"]
        if status["category"] == "doing" and status["name"] != "测试通过"
    )
    other = next(
        status["name"] for status in detail["statuses"]
        if status["category"] == "doing" and status["name"] not in {doing, "测试通过"}
    )
    script, calls = fake_llm
    script.append({"content": json.dumps({
        "explanation": "接上试装和终检",
        "transitions": {todo: ["试装"], "试装": ["终检"], "终检": ["已完成"]},
        "question": None,
    }, ensure_ascii=False)})
    styled = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task",
        "transitions": {todo: [doing]},
        "messages": [{"role": "user", "content": "后面加上试装和终检"}],
    })
    assert styled.status_code == 200, styled.text
    body = styled.json()
    assert body["valid"] is True
    assert {item["name"]: item["category"] for item in body["new_statuses"]} == {
        "试装": "doing", "终检": "doing",
    }
    assert len(calls) == 1

    cycle = {doing: [other], other: [doing]}
    script.extend([
        {"content": json.dumps({
            "explanation": "互相流转", "transitions": cycle, "question": None,
        }, ensure_ascii=False)},
        {"content": json.dumps({
            "explanation": "还是互相流转", "transitions": cycle, "question": None,
        }, ensure_ascii=False)},
    ])
    healed = admin.post("/api/agent/workflow-draft", json={
        "project": "ELEC", "issue_type": "task",
        "transitions": {todo: [doing], doing: ["已完成"]},
        "messages": [{"role": "user", "content": "只在这两个状态之间流转"}],
    })
    assert healed.status_code == 200, healed.text
    healed_body = healed.json()
    assert healed_body["valid"] is True
    assert "补成" in healed_body["explanation"]
    assert "已完成" in {
        target for targets in healed_body["transitions"].values() for target in targets
    }
    after = [status["name"] for status in admin.get("/api/projects/ELEC").json()["statuses"]]
    assert after == names
