"""Turn a natural-language conversation into a validated JQL filter."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import MachineGroup, OrgUnit, Project, Status, User
from . import llm, permissions, profiles
from .agent import audit
from .search import JqlError, compile_jql, search_issues

MAX_TURNS = 12
MAX_PEOPLE = 15

GRAMMAR = """JQL 语法（本系统的方言）：
- 子句：字段 操作符 值；用 AND / OR / NOT 和括号组合；末尾可加 ORDER BY 字段 [ASC|DESC]（多个用逗号分隔）。
- 操作符：= != ~（包含/模糊） !~ > >= < <= ；IN (a, b) / NOT IN (a, b) ；IS EMPTY / IS NOT EMPTY。
- 含空格、斜杠或特殊字符的值用双引号括起来，例如 group = "CMB-1"、machine = "CMB-1/整机"。
- 字段（只能用这些英文名）：
  project 项目 key；type 类型(requirement|bug|task|story|epic|subtask)；
  status 状态名（必须使用下方列出的原始状态名）；category 状态分类(todo|doing|done)；
  priority 优先级(highest|high|medium|low|lowest，可用 > < 比较)；
  assignee 负责人；reporter 报告人；tester 测试人；software_owner 软件负责人
    （人员值用账号 username；currentUser() 表示当前用户；未分配用 IS EMPTY）；
  watcher / watchers 关注人（同 assignee：username / currentUser() / IS EMPTY）；
  linked 关联问题的 key（例 linked = ELEC-12）；link_type 关联类型(blocks|duplicates|relates)；
  machine 机台（"机台组/机台" 或机台名）；group 机台组名；scope 影响范围选项；label 标签；
  software 是否涉及软件(true|false)；software_ready 软件是否准备好(true|false)；
  electrical_scheme_ok 电气确认；process_scheme_ok 工艺确认；software_scheme_ok 软件确认；
  scheme_locked 方案已发起(true|false)；
  software_jira_key 软件 Jira 单号；passed 测试是否通过(true|false)；project_name 项目名称（需求里填写的）；
  team 组织（按组织路径模糊匹配负责人或报告人所在部门）；text 标题/描述全文模糊匹配（用 ~）；
  key 问题编号；sprint 冲刺（opensprints() 表示进行中的冲刺）；epic / parent 父级 key；points 故事点；
  created 创建时间；updated 更新时间；resolved 完成时间；due 截止日期。
- 模板自定义字段使用 cf_<key>，具体可用字段见下方“模板字段”；支持 =、!=、~、IN 和 IS EMPTY。
- 日期值：YYYY-MM-DD、now()、today、startOfWeek()、startOfMonth()，带偏移的 startOfWeek(-1)、startOfMonth(1)，
  或相对值如 -7d、-2w、-1m、+3d。日期不支持加减运算（不要写 startOfWeek() - 1w）。
  例："本周" → created >= startOfWeek()；"上周" → updated >= startOfWeek(-1) AND updated < startOfWeek()；
  "最近 3 天" → updated >= -3d；"已逾期" → due < today AND category != done。
- 模糊搜索关键词必须写成 text ~ "关键词"，不要输出孤立的词或符号。
- "未完成"用 category != done；"已完成"用 category = done。
- 用户说"需求"指 type = requirement，"Bug/缺陷"指 type = bug；泛指的"问题/事项/Issue"不加类型条件。
- "高优先级"用 priority in (highest, high)，"低优先级"用 priority in (low, lowest)。
- 提到部门/组织（如"软件部门负责的""电气组提的"）用 team ~ 组织名，不要换成 currentUser()；只有说"我"时才用 currentUser()。
- "按优先级排序"默认 ORDER BY priority DESC（高的在前）；"最新/最近"默认按 created 或 updated DESC。"""

OUTPUT = """只输出一个 JSON 对象：
{"jql": "完整的 JQL（无法生成时为空字符串）", "explanation": "一句话说明这个筛选条件查的是什么", "question": "需要用户澄清时的问题，否则为 null"}
规则：
1. 每次都输出完整的 JQL，在"当前筛选条件"基础上按用户的最新要求修改（增加、删除或替换条件）；用户明确要重新开始时再从零写。
2. 只使用上面列出的字段和取值；状态、机台组、影响范围等必须用列表里的原始写法。
3. 提到人员时只能使用"候选人员"中的账号；"我"用 currentUser()。找不到对应的人员时，不要编造账号，在 question 中请用户确认。
4. 无法判断用户意图时 jql 留空，并在 question 中提出一个简短的问题。
5. 用户没有提到排序时不要加 ORDER BY。"""


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _people(db: Session, text: str) -> list[User]:
    """Users the conversation seems to mention, by display name, account or account prefix."""
    lowered = text.lower()
    found: list[User] = []
    users = db.scalars(
        select(User).options(selectinload(User.org_unit)).where(User.enabled.is_(True))
    )
    for user in users:
        names = {user.display_name.lower(), user.username.lower()}
        names |= {part for part in user.username.lower().replace("_", ".").split(".")
                  if len(part) >= 3}
        if any(len(name) >= 2 and name in lowered for name in names):
            found.append(user)
            if len(found) >= MAX_PEOPLE:
                break
    return found


def context_block(db: Session, user: User, text: str) -> str:
    hidden = permissions.hidden_project_ids(db, user)
    projects = [p for p in db.scalars(select(Project).where(Project.archived.is_(False)))
                if p.id not in hidden]
    project_ids = [project.id for project in projects]
    statuses = db.execute(
        select(Status.name, Status.category)
        .where(Status.project_id.in_(project_ids))
        .order_by(Status.sort_order)
    ).all()
    status_names = _distinct([f"{name}({category})" for name, category in statuses])
    groups = _distinct(list(db.scalars(
        select(MachineGroup.name)
        .where(MachineGroup.project_id.in_(project_ids))
        .order_by(MachineGroup.name)
    )))
    scopes = _distinct([o for p in projects for o in (p.settings or {}).get(
        "update_scope_options", [])])
    labels = _distinct([o for p in projects for o in (p.settings or {}).get(
        "label_options", [])])
    teams = _distinct(list(db.scalars(
        select(OrgUnit.path)
        .where(OrgUnit.removed.is_(False), OrgUnit.hidden.is_(False), OrgUnit.level <= 3)
        .order_by(OrgUnit.level, OrgUnit.sort_order)
    )))
    people = [
        f"{u.username}（{u.display_name}，{u.org_unit.path if u.org_unit else '未知组织'}）"
        for u in _people(db, text)
    ]
    test_statuses = _distinct([(p.settings or {}).get("test_status") or "测试中" for p in projects])
    custom_fields = _distinct([
        f"cf_{field.key}={field.label}({field.kind})"
        for project in projects for field in profiles.profile_of(project).custom_fields
    ])
    org = user.org_unit.path if user.org_unit else "未知"
    return "\n".join([
        f"今天是 {date.today().isoformat()}。当前用户：{user.display_name}"
        f"（账号 {user.username}，组织 {org}）。",
        "项目：" + "; ".join(f"{p.key}={p.name}" for p in projects),
        "状态（原始名称及分类）：" + "、".join(status_names),
        "测试阶段的状态：" + "、".join(test_statuses)
        + "（\"待我测试\"= tester = currentUser() AND status 为测试阶段状态）",
        "机台组：" + ("、".join(groups[:80]) or "无"),
        "影响范围选项：" + ("、".join(scopes) or "无"),
        "标签选项：" + ("、".join(labels[:80]) or "无"),
        "模板字段：" + ("、".join(custom_fields[:100]) or "无"),
        "组织（team 可用路径中的任一段）：" + ("、".join(teams[:60]) or "无"),
        "候选人员：" + ("；".join(people) or "对话中没有识别到具体人员"),
    ])


def _clean(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    turns = [
        {"role": m["role"], "content": str(m.get("content") or "")[:2000]}
        for m in messages
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    return turns[-MAX_TURNS:]


def _ask(prompt: list[dict[str, Any]]) -> dict[str, Any]:
    message = llm.chat(prompt, temperature=0.1, max_tokens=800, json_mode=True)
    parsed = llm.parse_json(message.get("content"))
    if not isinstance(parsed, dict):
        raise llm.LlmError("无法解析 LLM 返回的 JSON")
    return parsed


def assist(db: Session, user: User, messages: list[dict[str, Any]],
           current_jql: str = "") -> dict[str, Any]:
    started = time.time()
    turns = _clean(messages)
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("请先描述想要筛选的内容")
    conversation = " ".join(t["content"] for t in turns if t["role"] == "user")
    system = (
        "你负责把用户对问题的自然语言筛选需求翻译成 JQL。\n\n" + GRAMMAR + "\n\n"
        + context_block(db, user, conversation) + "\n\n"
        + f"当前筛选条件：{current_jql.strip() or '（空）'}\n\n" + OUTPUT
    )
    prompt: list[dict[str, Any]] = [{"role": "system", "content": system}, *turns]
    try:
        parsed = _ask(prompt)
        jql = str(parsed.get("jql") or "").strip()
        error = _check(jql, user)
        if error:
            prompt += [
                {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                {"role": "user", "content": f"这条 JQL 解析失败：{error}。请修正后重新输出 JSON。"},
            ]
            parsed = _ask(prompt)
            jql = str(parsed.get("jql") or "").strip()
            error = _check(jql, user)
    except llm.LlmError as failure:
        audit(db, user, "jql", conversation, result=str(failure), ok=False, started=started)
        raise
    question = parsed.get("question") or None
    explanation = str(parsed.get("explanation") or "").strip()
    if error:
        audit(db, user, "jql", conversation, result=f"{jql}\n{error}", ok=False, started=started)
        return {"jql": "", "explanation": explanation, "question": question, "error": error,
                "total": None}
    total = search_issues(db, jql, user_id=user.id, limit=1)[0] if jql else None
    audit(db, user, "jql", conversation, result=jql, started=started)
    return {"jql": jql, "explanation": explanation, "question": question, "error": None,
            "total": total}


def _check(jql: str, user: User) -> str | None:
    if not jql:
        return None
    try:
        compile_jql(jql, user_id=user.id)
    except JqlError as error:
        return str(error)
    return None
