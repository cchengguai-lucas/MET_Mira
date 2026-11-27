from __future__ import annotations

import pytest

from backend.services import help_kb

QUESTIONS = [
    ("为什么我不能把需求直接拖到已完成？", "流转规则：为什么不能直接拖到已完成"),
    ("周期时间是怎么算的", "流动指标：周期时间与前置时间"),
    ("What is cycle time?", "流动指标：周期时间与前置时间"),
    ("会签审批怎么配置", "状态门禁与会签审批"),
    ("who can approve a transition", "状态门禁与会签审批"),
    ("主数据有什么用", "共享主数据"),
    ("SLA 是多久", "SLA 时效"),
    ("怎么导入Excel", "Excel 导入"),
    ("How do I import Excel?", "Excel 导入"),
    ("软件负责人选不到人", "软件负责人"),
    ("发布模板新版本会影响已有项目吗", "部门模板"),
    ("测试不通过后会怎样", "测试闭环与测试结果自动流转"),
    ("健康分怎么计算", "健康度定义"),
    ("WIP 是什么", "看板"),
    ("怎么切换英文", "语言、主题与头像"),
    ("cf_drawing_no 怎么用", "JQL 语法参考"),
    ("看板上看不到问题", "常见问题"),
    ("冲刺怎么开始", "待办事项与冲刺"),
    ("谁能删除问题", "角色与权限总览"),
    ("收不到邮件", "通知、邮件与企业微信提醒"),
    ("项目怎么归档", "项目设置总览与基本信息"),
    ("已驳回算完成吗", "状态与状态类别"),
    ("子任务能再建子任务吗", "父任务、子任务与史诗关系"),
    ("怎么共享筛选器", "已保存与共享筛选器"),
    ("预测的P85是什么意思", "预测（蒙特卡洛）"),
    ("机械模板有哪些字段", "内置部门模板（电气 / 机械 / 生产）"),
    ("怎么新建项目", "项目列表与新建项目"),
    ("快捷键有哪些", "快速跳转与快捷键"),
    ("怎么自定义快捷键", "快速跳转与快捷键"),
    ("打开AI助手的热键是什么", "快速跳转与快捷键"),
    ("怎么添加成员", "成员与角色设置"),
    ("怎么更换项目模板", "更换项目模板（迁移向导）"),
    ("AI 助手能做什么", "AI 助手（对话）"),
]


def test_sections_are_well_formed():
    titles = help_kb.topics()
    assert len(titles) == len(set(titles)) >= 50
    for section in help_kb.sections():
        assert section.keywords, section.title
        assert len(section.body) > 40, section.title


@pytest.mark.parametrize(("question", "title"), QUESTIONS)
def test_questions_find_their_section(question, title):
    ranked = [section.title for section, _ in help_kb.search(question, 3)]
    assert title in ranked, (question, ranked)
    assert title in help_kb.context_for(question)


def test_data_questions_get_no_reference():
    assert help_kb.context_for("ELEC-12 现在什么状态") == ""
    assert help_kb.context_for("你好") == ""


def test_knowledge_matches_code_defaults():
    """Guards the guide against drifting from the built-in definitions."""
    from backend.services import profiles, workflow
    from backend.services.search import FIELD_ALIASES, ORDER_FIELDS

    text = "\n".join(section.text for section in help_kb.sections())
    for name, _category, _color in workflow.DEFAULT_STATUSES:
        assert name in text
    for scope in workflow.DEFAULT_UPDATE_SCOPES:
        assert scope in text
    hours = workflow.default_settings()["sla_hours"]
    assert (f"最高 {hours['highest']}h、高 {hours['high']}h、中 {hours['medium']}h、"
            f"低 {hours['low']}h、最低 {hours['lowest']}h") in text
    jql = next(s.text for s in help_kb.sections() if s.title == "JQL 语法参考")
    for field in set(FIELD_ALIASES.values()) - {"project_name"}:
        assert field in jql, field
    for field in ORDER_FIELDS:
        assert field in jql, field
    for profile in profiles.PROFILES.values():
        for field in profile.custom_fields:
            assert field.label in text, field.label
        for status in profile.default_statuses:
            assert status[0] in text, status


def test_product_help_tool_and_project_meta(admin):
    from backend.db import session_factory
    from backend.models import User
    from backend.services.agent_tools import AgentContext, run_tool

    with session_factory()() as db:
        user = db.query(User).filter_by(username="admin").one()
        ctx = AgentContext(db=db, user=user, project_key="ELEC")
        found = run_tool(ctx, "product_help", {"query": "会签审批"})
        assert found["sections"][0].startswith("## 状态门禁与会签审批")
        exact = run_tool(ctx, "product_help", {"topic": "健康度定义"})
        assert exact["sections"][0].startswith("## 健康度定义")
        missing = run_tool(ctx, "product_help", {"query": "zzzz"})
        assert missing["sections"] == [] and "健康度定义" in missing["topics"]
        meta = run_tool(ctx, "project_meta", {"project": "ELEC"})
        assert meta["workflows"]["requirement"]["测试中"] == ["测试通过", "测试未通过"]
        assert meta["workflows"]["requirement"]["测试通过"] == ["已完成", "已驳回"]
        assert meta["test_flow"]["test_status"] == "测试中"
        assert meta["test_flow"]["pass_status"] == "测试通过"
        assert meta["sla_hours"]["highest"] == 24
        assert "definition" not in meta["profile"]
        assert {"name": "已驳回", "category": "done"} in meta["statuses"]


def test_chat_injects_guide_and_answers_from_tool(admin, monkeypatch):
    from backend.db import session_factory
    from backend.models import User
    from backend.services import agent, llm

    prompts: list[str] = []
    replies = iter([
        {"content": "", "tool_calls": [{"id": "1", "function": {
            "name": "product_help", "arguments": '{"query": "SLA"}'}}]},
        {"content": "SLA 按优先级计算。"},
    ])

    def fake_chat(convo, **_kwargs):
        prompts.append(convo[0]["content"])
        return next(replies)

    monkeypatch.setattr(llm, "probe", lambda: {"reachable": True, "tool_mode": "native"})
    monkeypatch.setattr(llm, "chat", fake_chat)
    with session_factory()() as db:
        user = db.query(User).filter_by(username="admin").one()
        events = list(agent.run_chat(db, user, [{"role": "user", "content": "会签审批怎么配置"}]))
    assert "使用说明参考" in prompts[0] and "## 状态门禁与会签审批" in prompts[0]
    assert "使用说明目录" in prompts[0]
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and results[0]["name"] == "product_help" and "SLA 时效" in results[0]["summary"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "SLA 按优先级计算。"
