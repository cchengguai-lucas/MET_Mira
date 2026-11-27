"""LLM agent: conversational query/ops, NL drafting, triage, summaries and reports."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import is_postgres, session_factory, utcnow
from ..models import (
    ActivityLog,
    AgentAudit,
    AgentConversation,
    AgentMemory,
    AiReport,
    Comment,
    Issue,
    MasterAsset,
    OrgUnit,
    Project,
    ProjectMember,
    Status,
    User,
)
from ..serialize import issue_query
from . import help_kb, llm, permissions, planning, profiles
from .agent_tools import (
    WRITE_TOOLS,
    AgentContext,
    compact,
    detail,
    match_nav,
    nav_intent,
    openai_tools,
    resolve_machines,
    resolve_project,
    run_tool,
    suggest_pages,
    tools_prompt,
)
from .locale import current_lang
from .reports import _aware
from .software import descendant_ids

LOGGER = logging.getLogger(__name__)
MAX_STEPS = 8
MAX_HISTORY_CHARS = 16000

HELP_CUES = (
    "怎么用", "如何使用", "怎么配置", "如何配置", "在哪里设置", "入口",
    "功能说明", "什么意思", "规则", "权限",
)
PM_CUES = (
    "风险", "延期", "延误", "delay", "sequence", "顺序", "基线", "forecast",
    "预测", "目标日", "关键路径", "gate", "milestone", "全局",
)
WRITE_CUES = (
    "创建", "新建", "建一个", "添加", "修改", "更新", "推进", "指派", "流转",
    "评论", "应用", "保存", "create", "update",
)
DATA_CUES = (
    "哪些", "多少", "查询", "查找", "列出", "我的工作", "负责人", "状态",
    "逾期", "问题", "需求", "工作流", "流程", "bug", "issue",
)

TOOL_ROUTES: dict[str, set[str]] = {
    "help": {"product_help", "project_meta"},
    "navigation": {"product_help", "project_meta", "open_page"},
    "pm_risk": {
        "portfolio_overview",
        "project_risk_snapshot",
        "schedule_analysis",
        "get_issue",
        "get_issues",
        "project_meta",
    },
    "data": {
        "my_work",
        "search_issues",
        "get_issue",
        "get_issues",
        "find_users",
        "list_org_units",
        "project_meta",
        "project_stats",
        "analyze_workflow",
        "suggest_workflow",
    },
    "write": {
        "search_issues",
        "get_issue",
        "find_users",
        "project_meta",
        "analyze_workflow",
        "suggest_workflow",
        *WRITE_TOOLS,
    },
    "general": set(),
}


def classify_intent(question: str) -> str:
    raw = (question or "").strip().lower()
    if nav_intent(raw):
        return "navigation"
    if any(cue in raw for cue in PM_CUES):
        return "pm_risk"
    if any(cue in raw for cue in WRITE_CUES):
        return "write"
    if re.search(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b", question or "", re.I):
        return "data"
    if any(cue in raw for cue in HELP_CUES):
        return "help"
    if any(cue in raw for cue in DATA_CUES):
        return "data"
    return "general"


def audit(db: Session, user: User | None, kind: str, prompt: str, *, tool_calls=None,
          result: str = "", ok: bool = True, started: float | None = None) -> None:
    def redacted(value: str) -> str:
        value = re.sub(r"(?i)(password|token|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
        return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)

    db.add(
        AgentAudit(
            user_id=user.id if user else None,
            kind=kind,
            prompt=redacted(prompt)[:8000],
            tool_calls=tool_calls or [],
            result=redacted(result)[:8000],
            ok=ok,
            latency_ms=int((time.time() - started) * 1000) if started else 0,
        )
    )
    db.commit()


def cleanup_retention(
    db: Session,
    *,
    conversation_days: int = 180,
    audit_days: int = 90,
) -> dict[str, int]:
    now = utcnow()
    conversations = db.execute(
        delete(AgentConversation).where(
            AgentConversation.updated_at < now - timedelta(days=conversation_days)
        )
    ).rowcount
    audits = db.execute(
        delete(AgentAudit).where(
            AgentAudit.created_at < now - timedelta(days=audit_days)
        )
    ).rowcount
    db.commit()
    return {"conversations": int(conversations or 0), "audits": int(audits or 0)}


def _project_lines(db: Session, user: User) -> str:
    hidden = permissions.hidden_project_ids(db, user)
    projects = db.scalars(select(Project).where(Project.archived.is_(False)))
    return "; ".join(f"{p.key}={p.name}" for p in projects if p.id not in hidden)


def system_prompt(
    db: Session,
    user: User,
    project_key: str | None,
    native: bool,
    question: str = "",
    *,
    intent: str = "general",
    ui_context: dict[str, Any] | None = None,
) -> str:
    org = user.org_unit.path if user.org_unit else "未知"
    role = "系统管理员" if user.is_admin else "普通用户"
    base = f"""你是 Mira 的智能助手，服务于公司内部的需求与问题跟踪。
当前用户：{user.display_name}（账号 {user.username}，组织 {org}，{role}）。今天是 {date.today().isoformat()}。
可用项目：{_project_lines(db, user)}。当前上下文项目：{project_key or '未指定'}。
规则：
1. 查询数据时先调用工具，基于工具结果回答，不要编造问题或人员。
2. 修改类操作（create_issue/update_issue/transition_issue/assign_issue/add_comment/apply_workflow）只会生成"待确认操作"，
   必须由用户在界面上确认后才执行。调用后告诉用户"已生成待确认操作"，绝不能说已完成。
3. 不确定机台、状态、人员时，先用 project_meta / find_users 查询。
4. 回答使用简洁的中文 Markdown；列出问题时用 `KEY 标题（状态，负责人）` 格式。
5. "需求"类型（requirement）分五段：需求方填写（项目名称、机台、需求说明）、
   方案确认（方案说明、电气确认、工艺确认、涉及软件时的软件确认；只在方案讨论中填写）、
   电气组填写（更新涉及、是否涉及软件、软件负责人、验证方法、验证标准）、
   软件组填写（仅当涉及软件时：软件是否准备好、交付说明、软件 Jira 单号；只由软件负责人确认）、
   测试方填写（更新日期、测试人、备注）。进入测试中、测试通过、测试未通过只能由测试人员或项目管理员操作。
   需求、缺陷、故事从研发中先到开发完成，再到测试中。不涉及软件时可直接进入开发完成。
   涉及软件时，软件 Jira 单号为空不能走下一步，退回不受影响。对方 Jira 状态必须是「待上机验证」才能进入开发完成；否则留下等待，到达该状态后自动进入测试中并提醒测试人。
   进入测试中前必须有测试人、验证方法和验证标准。手动进入时涉及软件还要软件已准备好。
   对方 Jira 状态为「待上机验证」时，系统会把软件是否准备好标为是，并把通讯录里按账号、显示名或中文名唯一对上的经办人和测试人加入关注。
   软件截止日期晚于本问题最终截止日期时，系统给负责人和软件方发邮件，请双方确认时间；软件日期早于或等于最终日期不冲突。助手不得改这两边的日期来“消除冲突”，除非用户明确要求改截止日期。
   助手不得代勾 software_ready。
6. 术语与界面保持一致：Issue 称“问题”，Sprint 称“冲刺”，Epic 称“史诗”，Backlog 称“待办事项”，
   bug 类型称“缺陷”；人员角色称负责人、报告人、测试人、软件负责人。
7. 关于 Mira 功能、入口位置、操作步骤、字段/状态/角色/指标的定义、权限与规则的问题，
   以“使用说明”为唯一依据：先看下方参考资料，不够时调用 product_help（可多次、换关键词或按目录标题读取）。
   说明中没有的功能就明确说“Mira 目前没有该功能”或“使用说明中未提及”，不要凭通用 Jira 经验臆测。
8. 涉及某个项目的实际配置（状态、各类型流程、门禁与审批、SLA、字段、成员角色、可见范围）时，
   调用 project_meta 以项目真实配置为准，说明里的默认值仅作参考。
9. 回答操作类问题时给出界面路径（如「项目 → 设置 → 状态与工作流」），注明需要的权限；
   如果当前用户可能没有权限，提示找项目管理员或系统管理员。
10. 跳转（仅明确意图时）：用户明确说「跳转 / 打开 / 带我去 / 去看 / 在哪（入口）」时，
   再对相关页面调用 open_page（可一次多个），让界面弹出确认跳转；项目内页面带上当前 project，
   打开问题用 issue_key。若只是问规则、定义、是否可见、能不能做等，必须直接据使用说明如实回答，
   可用文字写出路径，但不要调用 open_page，也不要因为查不到项目上下文就去跳转。
11. 工作流编辑：先调用 analyze_workflow 读取真实状态和当前流程；再调用 suggest_workflow 校验完整草稿，
   清楚说明新增/删除连线、警告和受影响问题。只有草稿 valid=true，且用户明确要求应用时，才调用
   apply_workflow 生成待确认操作；不得跳过草稿校验或代替用户确认。
使用说明目录：{'、'.join(help_kb.topics()) if intent in {'help', 'navigation'} else '仅帮助问题可用'}。
当前意图分类：{intent}。只调用当前提供的工具；需要实时数据的回答必须先成功调用读取工具。
界面上下文：{json.dumps(ui_context or {}, ensure_ascii=False)[:1500]}。"""
    project_id = (
        db.scalar(select(Project.id).where(Project.key == project_key))
        if project_key
        else None
    )
    memories = list(
        db.scalars(
            select(AgentMemory)
            .where(
                AgentMemory.user_id == user.id,
                AgentMemory.active.is_(True),
                or_(AgentMemory.project_id.is_(None), AgentMemory.project_id == project_id),
            )
            .order_by(AgentMemory.id.desc())
            .limit(12)
        )
    )
    if memories:
        base += "\n\n## 用户明确确认的长期记忆\n" + "\n".join(
            f"- {memory.content[:500]}" for memory in memories
        )
    reference = (
        help_kb.context_for(question)
        if question and intent in {"help", "navigation"}
        else ""
    )
    if reference:
        base += f"\n\n## 使用说明参考（按用户问题检索，仅用于功能说明；查询数据仍需调用工具）\n{reference}"
    if native:
        return base
    return (
        base
        + "\n\n你可以使用以下工具：\n"
        + tools_prompt(TOOL_ROUTES[intent])
        + '\n\n每次回复必须是一个 JSON 对象：调用工具时输出 {"tool": "名称", "args": {...}}；'
        '给出最终回答时输出 {"answer": "Markdown 文本"}。不要输出其它内容。'
    )


def _chunks(text: str, size: int = 24) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start:start + size]


@dataclass(frozen=True)
class ChatSnapshot:
    user_id: int
    project_key: str | None
    convo: list[dict[str, Any]]
    history: list[dict[str, Any]]
    question: str
    native: bool
    intent: str
    allowed_tools: frozenset[str]
    navigation_allowed: bool


def prepare_chat(
    db: Session,
    user: User,
    messages: list[dict[str, Any]],
    project_key: str | None,
    *,
    native: bool,
    ui_context: dict[str, Any] | None = None,
) -> ChatSnapshot:
    """Build an immutable prompt while the short initial transaction is open."""
    raw_history = [
        {"role": m["role"], "content": str(m.get("content") or "")[:4000]}
        for m in messages[-12:]
        if m.get("role") in {"user", "assistant"}
    ]
    history: list[dict[str, Any]] = []
    used = 0
    for message in reversed(raw_history):
        if used + len(message["content"]) > MAX_HISTORY_CHARS:
            break
        history.append(message)
        used += len(message["content"])
    history.reverse()
    asked = [m["content"] for m in history if m["role"] == "user"]
    question = asked[-1] if asked else ""
    intent = classify_intent(question)
    allowed_tools = frozenset(TOOL_ROUTES[intent])
    convo = [
        {
            "role": "system",
            "content": system_prompt(
                db,
                user,
                project_key,
                native,
                question,
                intent=intent,
                ui_context=ui_context,
            ),
        },
        *history,
    ]
    return ChatSnapshot(
        user.id,
        project_key,
        convo,
        history,
        question,
        native,
        intent,
        allowed_tools,
        intent == "navigation",
    )


def _audit_user(user_id: int, kind: str, prompt: str, **kwargs: Any) -> None:
    """Persist an audit in its own short session after streaming finishes."""
    with session_factory()() as db:
        user = db.get(User, user_id)
        audit(db, user, kind, prompt, **kwargs)


def run_chat(
    db: Session,
    user: User,
    messages: list[dict[str, Any]],
    project_key: str | None = None,
) -> Iterator[dict[str, Any]]:
    probe = llm.probe()
    if not probe.get("reachable"):
        yield {"type": "error", "message": f"LLM 服务不可用：{probe.get('error', '')}"}
        return
    snapshot = prepare_chat(
        db, user, messages, project_key, native=probe.get("tool_mode") == "native"
    )
    yield from run_chat_snapshot(snapshot)


def run_chat_snapshot(snapshot: ChatSnapshot) -> Iterator[dict[str, Any]]:
    """Run LLM/network waits with no checked-out SQLAlchemy session."""
    started = time.time()
    native = snapshot.native
    ctx = AgentContext(
        None,
        None,
        snapshot.project_key,
        snapshot.user_id,
        snapshot.allowed_tools,
        snapshot.navigation_allowed,
    )
    history = snapshot.history
    question = snapshot.question
    convo = list(snapshot.convo)
    calls_log: list[dict[str, Any]] = []
    final = ""
    try:
        for _step in range(MAX_STEPS):
            if native:
                message = llm.chat(
                    convo,
                    tools=openai_tools(set(snapshot.allowed_tools)),
                    max_tokens=2048,
                )
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    final = message.get("content") or ""
                    break
                convo.append({"role": "assistant", "content": message.get("content") or "",
                              "tool_calls": tool_calls})
                for call in tool_calls:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    result = yield from _invoke(ctx, name, args, calls_log)
                    convo.append({"role": "tool", "tool_call_id": call.get("id", name),
                                  "content": json.dumps(result, ensure_ascii=False)[:12000]})
            else:
                message = llm.chat(convo, max_tokens=2048)
                content = message.get("content") or ""
                try:
                    parsed = llm.parse_json(content)
                except llm.LlmError:
                    final = content
                    break
                if isinstance(parsed, dict) and parsed.get("tool"):
                    name = str(parsed["tool"])
                    args = parsed.get("args") or {}
                    result = yield from _invoke(ctx, name, args, calls_log)
                    convo.append({"role": "assistant", "content": content})
                    convo.append({"role": "system", "content": "工具结果：" + json.dumps(
                        result, ensure_ascii=False)[:12000]})
                    continue
                final = str(parsed.get("answer") if isinstance(parsed, dict) else content)
                break
        else:
            final = final or (
                "Too many steps, so the assistant stopped. Try splitting the request into smaller "
                "questions." if current_lang() == "en" else "操作步骤过多，已停止。请把问题拆小一些再试。"
            )
        grounded = any(
            not call.get("error") and call.get("name") not in WRITE_TOOLS | {"open_page"}
            for call in calls_log
        )
        if snapshot.intent in {"data", "pm_risk"} and not grounded:
            final = (
                "我没有成功读取到实时项目数据，因此不能可靠回答这个问题。"
                "请明确项目 Key 或缩小查询范围后重试。"
            )
        for piece in _chunks(final):
            yield {"type": "delta", "text": piece}
        # After the answer: fill in any jump offers the model forgot (keeps the card under the text).
        yield from _ensure_nav_offers(ctx, question, calls_log)
        yield {"type": "done"}
        _audit_user(snapshot.user_id, "chat", history[-1]["content"] if history else "",
                    tool_calls=calls_log, result=final, started=started)
    except llm.LlmError as error:
        yield {"type": "error", "message": str(error)}
        _audit_user(snapshot.user_id, "chat", history[-1]["content"] if history else "",
                    tool_calls=calls_log, result=str(error), ok=False, started=started)


def _invoke(ctx: AgentContext, name: str, args: dict[str, Any],
            calls_log: list[dict[str, Any]]):
    yield {"type": "tool_call", "name": name, "args": args}
    result = run_tool(ctx, name, args if isinstance(args, dict) else {})
    calls_log.append({"name": name, "args": args, "error": result.get("error")})
    if result.get("pending_action_id"):
        yield {"type": "action", "action": {"id": result["pending_action_id"], "tool": name,
                                            "args": args, "summary": result["summary"],
                                            "status": "pending"}}
    else:
        summary = result.get("error") or _result_summary(name, result)
        yield {"type": "tool_result", "name": name, "summary": summary,
               "ok": not result.get("error")}
    return result


def _ensure_nav_offers(ctx: AgentContext, question: str,
                       calls_log: list[dict[str, Any]]):
    """Propose open_page only when the user explicitly asked to go somewhere."""
    if not nav_intent(question):
        return
    already: set[str] = set()
    for call in calls_log:
        if call.get("name") != "open_page" or call.get("error"):
            continue
        dest = str((call.get("args") or {}).get("destination") or "").strip()
        if not dest:
            continue
        try:
            already.add(match_nav(dest).id.lower())
        except Exception:  # noqa: BLE001 - keep raw token so we still de-dupe
            already.add(dest.lower())
    pages = suggest_pages(question, limit=3, min_score=10)
    for page in pages:
        if page["id"].lower() in already:
            continue
        args: dict[str, Any] = {"destination": page["id"]}
        if page.get("needs_project") and ctx.project_key:
            args["project"] = ctx.project_key
        elif page.get("needs_project") and not ctx.project_key:
            continue
        yield from _invoke(ctx, "open_page", args, calls_log)
        already.add(page["id"].lower())


def _result_summary(name: str, result: dict[str, Any]) -> str:
    if name == "search_issues":
        return f"找到 {result.get('total', 0)} 条"
    if name == "get_issue":
        return f"{result.get('key')} {result.get('title', '')[:30]}"
    if name == "find_users":
        return f"{len(result.get('users', []))} 位人员"
    if name == "analyze_workflow":
        errors = sum(1 for row in result.get("findings", []) if row.get("level") == "error")
        return f"{result.get('issue_type')} 工作流 · {errors} 个错误"
    if name == "suggest_workflow":
        diff = result.get("diff") or {}
        return (
            f"{'校验通过' if result.get('valid') else '存在错误'} · "
            f"+{len(diff.get('add') or [])} / -{len(diff.get('remove') or [])} 条连线"
        )
    if name == "product_help":
        titles = [s.split("\n", 1)[0].removeprefix("## ") for s in result.get("sections", [])]
        pages = result.get("suggested_pages") or []
        base = "、".join(titles) or "未找到相关说明"
        if pages:
            base += " · 相关页面 " + "、".join(p["label"] for p in pages[:3])
        return base
    return "完成"


# ---------------------------------------------------------------- triage


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", (text or "").lower())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def recall_candidates(db: Session, project: Project, text: str, exclude_id: int | None = None,
                      limit: int = 10) -> list[tuple[Issue, float]]:
    scores: dict[int, float] = {}
    if is_postgres(db) and text.strip():
        rows = db.execute(
            select(Issue.id, func.similarity(Issue.title, text))
            .where(Issue.project_id == project.id, func.similarity(Issue.title, text) > 0.08)
            .order_by(func.similarity(Issue.title, text).desc())
            .limit(40)
        ).all()
        scores.update({row[0]: float(row[1]) for row in rows})
    query_grams = _bigrams(text)
    recent = db.execute(
        select(Issue.id, Issue.title, Issue.description)
        .where(Issue.project_id == project.id)
        .order_by(Issue.id.desc())
        .limit(600)
    ).all()
    for issue_id, title, description in recent:
        score = max(
            _jaccard(query_grams, _bigrams(title)),
            0.8 * _jaccard(query_grams, _bigrams(f"{title}{(description or '')[:300]}")),
        )
        if score > scores.get(issue_id, 0):
            scores[issue_id] = score
    if exclude_id:
        scores.pop(exclude_id, None)
    best = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    best = [(i, s) for i, s in best if s >= 0.12]
    if not best:
        return []
    issues = {i.id: i for i in db.scalars(issue_query().where(Issue.id.in_([b[0] for b in best])))}
    return [(issues[i], s) for i, s in best if i in issues]


def assignee_candidates(db: Session, project: Project, machine_ids: list[int],
                        scope: list[str]) -> list[dict[str, Any]]:
    since = utcnow() - timedelta(days=365)
    history = list(
        db.scalars(
            issue_query().where(Issue.project_id == project.id, Issue.assignee_id.is_not(None),
                                Issue.created_at >= since)
        )
    )
    group_ids = set()
    if machine_ids:
        from ..models import Machine

        group_ids = set(db.scalars(select(Machine.group_id).where(Machine.id.in_(machine_ids))))
    related: Counter[int] = Counter()
    total: Counter[int] = Counter()
    load: Counter[int] = Counter()
    for issue in history:
        total[issue.assignee_id] += 1
        if issue.status.category != "done":
            load[issue.assignee_id] += 1
        issue_groups = {m.group_id for m in issue.machines}
        if (group_ids & issue_groups) or (set(scope) & set(issue.update_scope or [])):
            related[issue.assignee_id] += 1
    profile = profiles.profile_of(project)
    preferred_roles = {
        section.role for section in profile.sections
        if section.role and "requirement" in section.issue_types
    }
    candidate_roles = {*preferred_roles, "admin", "member"}
    members = list(
        db.scalars(
            select(ProjectMember).options(selectinload(ProjectMember.user))
            .where(ProjectMember.project_id == project.id,
                   ProjectMember.role.in_(candidate_roles))
        )
    )
    ids = set(total) | {m.user_id for m in members}
    users = {u.id: u for u in db.scalars(
        select(User).options(selectinload(User.org_unit))
        .where(User.id.in_(ids), User.enabled.is_(True))
    )}
    roles: dict[int, set[str]] = {}
    for member in members:
        roles.setdefault(member.user_id, set()).add(member.role)
    result = []
    for uid, user in users.items():
        result.append({
            "username": user.username,
            "name": user.display_name,
            "title": user.title,
            "org": user.org_unit.path if user.org_unit else None,
            "roles": sorted(roles.get(uid, set())),
            "related_history": related.get(uid, 0),
            "total_history": total.get(uid, 0),
            "open_load": load.get(uid, 0),
        })
    result.sort(key=lambda r: (-r["related_history"],
                               not bool(preferred_roles & set(r["roles"])),
                               r["open_load"]))
    return result[:12]


RELATED_JUDGE_RULES = """你是工程问题关联分析助手。必须通读标题、完整描述、机台、范围与标签后再判断。

判定原则（严格）：
1. duplicate：实质是同一件事（同一故障/同一需求目标/同一变更），只是表述不同。
2. related：存在明确业务关联——同一根因、前后依赖、同一变更的不同侧面、或直接互为阻塞。
3. 以下情况一律视为无关，禁止输出：
   - 仅机台/产线/项目名相同，但现象、目标或工作内容不同；
   - 仅关键词、模板措辞、字段结构相似；
   - 同类型例行任务但对象/目的不同；
   - 信息不足、只能猜测相关。
4. 宁缺毋滥：没有把握就不要列入；reason 须说明「为何实质相关」，不要写「描述类似」「同机台」。"""


def _candidate_payload(issue: Issue, score: float) -> dict[str, Any]:
    machines = [f"{m.group.name}/{m.name}" for m in issue.machines]
    return {
        "key": issue.key,
        "type": issue.type,
        "title": issue.title,
        "status": issue.status.name if issue.status else None,
        "priority": issue.priority,
        "machines": machines,
        "update_scope": issue.update_scope or [],
        "labels": list(issue.labels or []),
        "description": (issue.description or "")[:2500],
        "lexical_score": round(score, 2),
    }


def recall_related_pool(db: Session, project: Project, issue: Issue,
                        limit: int = 16) -> list[tuple[Issue, float]]:
    """Lexical recall plus same-machine neighbours so the LLM can reject false friends."""
    text = f"{issue.title}\n{issue.description or ''}".strip()
    by_id: dict[int, tuple[Issue, float]] = {
        i.id: (i, score)
        for i, score in recall_candidates(db, project, text, exclude_id=issue.id, limit=limit)
    }
    machine_ids = [m.id for m in issue.machines]
    if machine_ids:
        from ..models import IssueMachine

        # DISTINCT + ORDER BY requires the sort column in the select list on Postgres.
        neighbour_rows = db.execute(
            select(Issue.id, Issue.updated_at)
            .join(IssueMachine, IssueMachine.issue_id == Issue.id)
            .where(
                Issue.project_id == project.id,
                Issue.id != issue.id,
                IssueMachine.machine_id.in_(machine_ids),
            )
            .distinct()
            .order_by(Issue.updated_at.desc())
            .limit(24)
        ).all()
        neighbour_ids = [row[0] for row in neighbour_rows]
        neighbours = list(db.scalars(issue_query().where(Issue.id.in_(neighbour_ids)))) if neighbour_ids else []
        for neighbour in neighbours:
            if neighbour.id not in by_id:
                by_id[neighbour.id] = (neighbour, 0.05)
    ranked = sorted(by_id.values(), key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def triage(db: Session, user: User, project: Project, title: str, description: str = "",
           machine_ids: list[int] | None = None, scope: list[str] | None = None,
           exclude_issue_id: int | None = None) -> dict[str, Any]:
    started = time.time()
    text = f"{title}\n{description}".strip()
    candidates = recall_candidates(db, project, title or text, exclude_issue_id)
    people = assignee_candidates(db, project, machine_ids or [], scope or [])
    heuristic = {
        "duplicates": [
            {**compact(issue), "score": round(score, 2),
             "relation": "duplicate" if score >= 0.55 else "similar",
             "reason": "标题高度相似" if score >= 0.55 else "内容相近"}
            for issue, score in candidates[:5]
        ],
        "assignee": ({**people[0], "reason": "历史上处理过相同机台/类型的需求"}
                     if people and people[0]["related_history"] else None),
        "priority": None,
        "priority_reason": None,
        "source": "heuristic",
    }
    if not llm.config().enabled:
        return heuristic
    prompt = {
        "new_issue": {
            "title": title,
            "description": description[:2500],
            "scope": scope or [],
            "machine_ids": machine_ids or [],
        },
        "candidates": [_candidate_payload(i, s) for i, s in candidates],
        "assignee_candidates": people,
    }
    try:
        message = llm.chat(
            [
                {"role": "system", "content": (
                    "你是需求分派助手。先按关联规则判断候选是否真是重复/相关，"
                    "再从候选人中推荐负责人，并建议优先级(highest/high/medium/low/lowest)。\n"
                    + RELATED_JUDGE_RULES
                    + "\n只输出 JSON：{\"duplicates\":[{\"key\":\"\",\"relation\":\"duplicate|similar\","
                    "\"reason\":\"\"}],\"assignee\":{\"username\":\"\",\"reason\":\"\"}|null,"
                    "\"priority\":\"medium\",\"priority_reason\":\"\"}。"
                    "duplicates 里 similar 对应 related；无关候选不要列出。"
                )},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=1600,
            json_mode=True,
        )
        parsed = llm.parse_json(message.get("content"))
    except llm.LlmError as error:
        audit(db, user, "triage", title, result=str(error), ok=False, started=started)
        return heuristic
    by_key = {i.key: (i, s) for i, s in candidates}
    duplicates = []
    for item in parsed.get("duplicates") or []:
        hit = by_key.get(str(item.get("key", "")).upper())
        if hit is None:
            continue
        issue, score = hit
        relation = item.get("relation", "similar")
        if relation not in {"duplicate", "similar", "related"}:
            relation = "similar"
        if relation == "related":
            relation = "similar"
        duplicates.append({**compact(issue), "score": round(score, 2),
                           "relation": relation,
                           "reason": item.get("reason", "")})
    assignee = None
    pick = parsed.get("assignee") or {}
    for person in people:
        if pick and person["username"] == pick.get("username"):
            assignee = {**person, "reason": pick.get("reason", "")}
    priority = parsed.get("priority")
    result = {
        "duplicates": duplicates,
        "assignee": assignee or heuristic["assignee"],
        "priority": priority if priority in {"highest", "high", "medium", "low", "lowest"}
        else None,
        "priority_reason": parsed.get("priority_reason"),
        "source": "llm",
    }
    audit(db, user, "triage", title, result=json.dumps(result, ensure_ascii=False)[:4000],
          started=started)
    return result


def suggest_related(db: Session, user: User, issue: Issue) -> dict[str, Any]:
    """AI judges which existing issues are truly related — not merely lexically similar."""
    started = time.time()
    if not llm.config().enabled:
        raise llm.LlmError("LLM 未启用")
    project = db.get(Project, issue.project_id)
    assert project is not None
    pool = recall_related_pool(db, project, issue)
    if not pool:
        audit(db, user, "suggest_related", issue.key, result="[]", started=started)
        return {"related": [], "source": "llm"}
    from ..models import IssueLink

    linked_ids = set(
        db.scalars(select(IssueLink.target_id).where(IssueLink.source_id == issue.id))
    ) | set(
        db.scalars(select(IssueLink.source_id).where(IssueLink.target_id == issue.id))
    )
    pool = [(i, s) for i, s in pool if i.id not in linked_ids]
    subject = {
        "key": issue.key,
        "type": issue.type,
        "title": issue.title,
        "description": (issue.description or "")[:4000],
        "machines": [f"{m.group.name}/{m.name}" for m in issue.machines],
        "update_scope": issue.update_scope or [],
        "labels": list(issue.labels or []),
        "priority": issue.priority,
        "status": issue.status.name if issue.status else None,
    }
    prompt = {
        "subject": subject,
        "candidates": [_candidate_payload(i, s) for i, s in pool],
    }
    instructions = (
        RELATED_JUDGE_RULES
        + "\n对 subject 与每位候选做综合判断；lexical_score 仅作召回参考，绝不能当作相关证据。"
        + "只输出 JSON：{\"related\":[{\"key\":\"\",\"relation\":\"duplicate|related\","
        "\"reason\":\"一句话说明实质关联点\"}]}。"
        + "最多返回 8 条；按把握从高到低。"
    )
    try:
        message = llm.chat(
            [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=2000,
            json_mode=True,
        )
        parsed = llm.parse_json(message.get("content"))
    except llm.LlmError as error:
        audit(db, user, "suggest_related", issue.key, result=str(error), ok=False, started=started)
        raise
    by_key = {i.key: (i, s) for i, s in pool}
    related: list[dict[str, Any]] = []
    for item in parsed.get("related") or []:
        if not isinstance(item, dict):
            continue
        hit = by_key.get(str(item.get("key", "")).upper())
        if hit is None:
            continue
        other, score = hit
        relation = item.get("relation", "related")
        if relation not in {"duplicate", "related", "similar"}:
            continue
        if relation == "similar":
            relation = "related"
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        related.append({
            **compact(other),
            "score": round(score, 2),
            "relation": relation,
            "reason": reason[:300],
        })
        if len(related) >= 8:
            break
    audit(db, user, "suggest_related", issue.key,
          result=json.dumps(related, ensure_ascii=False)[:4000], started=started)
    return {"related": related, "source": "llm"}


# ---------------------------------------------------------------- NL drafting


DRAFT_SCHEMA_HINT = """输出 JSON：{"issues":[{
 "type":"requirement|bug|task|story|epic",
 "title":"简洁标题(<=40字)",
 "description":"Markdown 需求说明，包含背景、现象/目标、范围",
 "priority":"highest|high|medium|low|lowest",
 "machines":["机台组/机台 或 机台组"],
 "update_scope":["从可选项中选择"],
 "project_name":"业务项目名称，没有则空字符串",
 "involves_software":true|false|null,
 "verify_method":"建议的验证方法",
 "verify_criteria":"建议的验证标准",
 "custom_fields":{"模板字段 key":"按字段类型填写"},
 "labels":[],
 "children":[{"type":"task|subtask","title":"","description":"","priority":"medium"}]
}]}"""


def _parse_draft_payload(raw: Any) -> list[dict[str, Any]]:
    items = raw.get("issues") if isinstance(raw, dict) else raw
    return [item for item in (items or []) if isinstance(item, dict)]


def _draft_child_count(drafts: list[dict[str, Any]]) -> int:
    return sum(len(d.get("children") or []) for d in drafts)


def draft_issues(db: Session, user: User, project: Project, text: str,
                 split: bool = False) -> dict[str, Any]:
    started = time.time()
    meta = run_tool(AgentContext(db, user, project.key), "project_meta", {"project": project.key})
    profile = profiles.profile_of(project)
    custom_hint = json.dumps(profiles.layout(profile)["custom_fields"], ensure_ascii=False)
    split_rule = (
        "用户已明确要求拆分：必须只输出 1 个 type=epic 的父项，并在 children 中拆出 2～6 个可执行子任务"
        "（按实施步骤/角色/机台拆分均可）；禁止只返回单个无 children 的问题；"
        if split else
        "尽量只生成一个问题，除非描述明显包含多个无关需求；children 为空；"
    )
    instructions = (
        "你负责把用户的自然语言描述整理成问题草稿。"
        + split_rule
        + "机台只能从项目机台中选择；更新涉及只能从 update_scope_options 中选择；"
        + f"项目模板为 {profile.label}，custom_fields 只能按以下定义填写：{custom_hint}；"
        + "无法判断的字段留空。不要填写软件是否准备好，该字段只能由软件负责人在界面确认。"
        "可以建议是否涉及软件、验证方法和验证标准。\n"
        + DRAFT_SCHEMA_HINT
    )
    user_content = (
        "项目信息：" + json.dumps(meta, ensure_ascii=False)
        + "\n\n用户描述：\n" + text[:6000]
    )
    try:
        message = llm.chat(
            [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=3000,
            json_mode=True,
        )
        parsed = llm.parse_json(message.get("content"))
        raw_items = _parse_draft_payload(parsed)
        # Split mode must yield children; one soft retry if the model ignored the flag.
        if split and not any((item.get("children") or []) for item in raw_items):
            retry = llm.chat(
                [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": message.get("content") or "{}"},
                    {"role": "user", "content": (
                        "不符合要求：必须拆成 1 个 epic + 至少 2 个 children。"
                        "请重新输出完整 JSON，不要解释。"
                    )},
                ],
                temperature=0.3,
                max_tokens=3000,
                json_mode=True,
            )
            parsed = llm.parse_json(retry.get("content"))
            raw_items = _parse_draft_payload(parsed)
    except llm.LlmError as error:
        audit(db, user, "draft", text, result=str(error), ok=False, started=started)
        raise
    drafts = [_normalise_draft(db, project, item) for item in raw_items]
    if split:
        # Prefer epic+children; if the model returned sibling issues, fold them.
        if drafts and _draft_child_count(drafts) == 0 and len(drafts) > 1:
            parent, *rest = drafts
            parent["type"] = "epic"
            parent["children"] = [
                {
                    "type": "task",
                    "title": c["title"],
                    "description": c.get("description") or "",
                    "priority": c.get("priority") or "medium",
                }
                for c in rest
            ]
            drafts = [parent]
        if not drafts or _draft_child_count(drafts) == 0:
            audit(db, user, "draft", text, result="split_without_children", ok=False,
                  started=started)
            raise ValueError("未能拆分为史诗和子任务，请补充实施步骤后再试")
        if len(drafts) == 1:
            drafts[0]["type"] = "epic"
    first = drafts[0] if drafts else None
    tri = None
    if first is not None:
        tri = triage(db, user, project, first["title"], first.get("description", ""),
                     first.get("machine_ids"), first.get("update_scope"))
        if tri.get("assignee") and not first.get("assignee_username"):
            first["assignee_username"] = tri["assignee"]["username"]
            first["assignee_reason"] = tri["assignee"].get("reason")
        if tri.get("priority"):
            first["priority"] = tri["priority"]
    audit(db, user, "draft", text, result=json.dumps(drafts, ensure_ascii=False)[:4000],
          started=started)
    return {"drafts": drafts, "triage": tri}


def _draft_custom_fields(db: Session, project: Project,
                         values: dict[str, Any]) -> dict[str, Any]:
    """Keep each valid AI-suggested template value and resolve readable references."""
    profile = profiles.profile_of(project)
    out: dict[str, Any] = {}
    for key, value in values.items():
        field = profile.custom_field(str(key))
        if field is None:
            continue
        candidate = value
        if field.kind == "master_ref" and field.master_kind:
            raw = value if isinstance(value, list) else [value]
            ids: list[int] = []
            for item in raw:
                if isinstance(item, int) and not isinstance(item, bool):
                    ids.append(item)
                    continue
                text = str(item or "").strip()
                if not text:
                    continue
                asset_id = db.scalar(select(MasterAsset.id).where(
                    MasterAsset.kind == field.master_kind,
                    MasterAsset.active.is_(True),
                    or_(func.lower(MasterAsset.code) == text.lower(),
                        func.lower(MasterAsset.name) == text.lower()),
                ))
                if asset_id is not None:
                    ids.append(asset_id)
            if ids and db.scalar(select(func.count(MasterAsset.id)).where(
                MasterAsset.id.in_(ids),
                MasterAsset.kind == field.master_kind,
                MasterAsset.active.is_(True),
            )) != len(set(ids)):
                continue
            candidate = ids if field.multiple else (ids[0] if ids else None)
        elif field.kind == "user" and isinstance(value, str):
            candidate = db.scalar(select(User.id).where(
                User.username == value.strip(), User.enabled.is_(True)
            ))
        elif field.kind == "machine":
            names = value if isinstance(value, list) else [value]
            try:
                candidate = resolve_machines(db, project, [str(name) for name in names if name])
            except Exception:  # noqa: BLE001 - drop only the invalid AI suggestion
                continue
        try:
            out.update(profiles.validate_custom_fields(profile, {field.key: candidate}))
        except ValueError:
            continue
    return out


def _normalise_draft(db: Session, project: Project, item: dict[str, Any]) -> dict[str, Any]:
    options = (project.settings or {}).get("update_scope_options", [])
    machine_ids: list[int] = []
    unresolved: list[str] = []
    for name in item.get("machines") or []:
        try:
            machine_ids += resolve_machines(db, project, [name])
        except Exception:  # noqa: BLE001 - keep the draft, surface unknown names
            unresolved.append(str(name))
    issue_type = item.get("type") if item.get("type") in {
        "requirement", "bug", "task", "story", "epic", "subtask"} else "requirement"
    draft = {
        "type": issue_type,
        "title": str(item.get("title") or "未命名")[:200],
        "description": str(item.get("description") or ""),
        "priority": item.get("priority") if item.get("priority") in {
            "highest", "high", "medium", "low", "lowest"} else "medium",
        "machine_ids": list(dict.fromkeys(machine_ids)),
        "unresolved_machines": unresolved,
        "update_scope": [s for s in item.get("update_scope") or [] if s in options],
        "project_name": str(item.get("project_name") or ""),
        "involves_software": item.get("involves_software")
        if isinstance(item.get("involves_software"), bool) else None,
        "verify_method": str(item.get("verify_method") or ""),
        "verify_criteria": str(item.get("verify_criteria") or ""),
        "labels": [str(x) for x in item.get("labels") or []][:8],
        "children": [],
    }
    draft["custom_fields"] = _draft_custom_fields(
        db, project,
        item.get("custom_fields") if isinstance(item.get("custom_fields"), dict) else {},
    )
    for child in item.get("children") or []:
        if isinstance(child, dict) and child.get("title"):
            draft["children"].append({
                "type": child.get("type") if child.get("type") in {"task", "subtask", "story",
                                                                     "bug"} else "task",
                "title": str(child["title"])[:200],
                "description": str(child.get("description") or ""),
                "priority": child.get("priority") if child.get("priority") in {
                    "highest", "high", "medium", "low", "lowest"} else "medium",
            })
    return draft


def suggest_subtasks(db: Session, user: User, issue: Issue) -> dict[str, Any]:
    """Propose 2–6 subtask drafts from an issue's title and description."""
    started = time.time()
    if issue.type == "subtask":
        raise ValueError("子任务不能再拆分子任务")
    if not llm.config().enabled:
        raise llm.LlmError("LLM 未启用")
    existing = [
        {"key": c.key, "title": c.title}
        for c in db.scalars(
            select(Issue).where(Issue.parent_id == issue.id).order_by(Issue.rank).limit(20)
        )
    ] if issue.type != "epic" else [
        {"key": c.key, "title": c.title}
        for c in db.scalars(
            select(Issue).where(Issue.epic_id == issue.id).order_by(Issue.rank).limit(20)
        )
    ]
    child_kind = "task" if issue.type == "epic" else "subtask"
    prompt = {
        "issue": {
            "key": issue.key,
            "type": issue.type,
            "title": issue.title,
            "description": (issue.description or "")[:4000],
        },
        "existing_children": existing,
        "child_kind": child_kind,
    }
    instructions = (
        f"你是工作拆分助手。根据现有问题的标题与描述，建议 2～6 个可执行的"
        f"{'子项（task）' if child_kind == 'task' else '子任务（subtask）'}。"
        "不要重复 existing_children 里已有的工作；标题简洁（<=40字）；"
        "description 用简短 Markdown 说明目标即可。"
        '只输出 JSON：{"subtasks":[{"title":"","description":"","priority":"medium|high|low"}]}。'
    )
    try:
        message = llm.chat(
            [
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=2000,
            json_mode=True,
        )
        parsed = llm.parse_json(message.get("content"))
    except llm.LlmError as error:
        audit(db, user, "suggest_subtasks", issue.key, result=str(error), ok=False, started=started)
        raise
    raw = parsed.get("subtasks") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        raw = parsed.get("children") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        raw = []
    subtasks: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        priority = item.get("priority")
        subtasks.append({
            "title": str(item["title"])[:200],
            "description": str(item.get("description") or ""),
            "priority": priority if priority in {"highest", "high", "medium", "low", "lowest"}
            else "medium",
            "type": child_kind,
        })
        if len(subtasks) >= 6:
            break
    if len(subtasks) < 2:
        audit(db, user, "suggest_subtasks", issue.key, result="too_few", ok=False, started=started)
        raise ValueError("未能生成足够的子任务建议，请补充描述后再试")
    audit(db, user, "suggest_subtasks", issue.key,
          result=json.dumps(subtasks, ensure_ascii=False)[:4000], started=started)
    return {"subtasks": subtasks, "child_kind": child_kind}


# ---------------------------------------------------------------- summaries & reports


def prepare_summary(db: Session, issue: Issue) -> tuple[str, list[dict[str, str]]]:
    info = detail(db, issue)
    comments = list(db.scalars(
        select(Comment).options(selectinload(Comment.author))
        .where(Comment.issue_id == issue.id).order_by(Comment.id)
    ))
    info["comments"] = [
        {"author": c.author.display_name if c.author else None, "body": c.body[:1200],
         "at": c.created_at.strftime("%m-%d %H:%M")}
        for c in comments[-40:]
    ]
    history = db.scalars(
        select(ActivityLog).options(selectinload(ActivityLog.actor))
        .where(ActivityLog.issue_id == issue.id, ActivityLog.field.is_not(None))
        .order_by(ActivityLog.id.desc()).limit(30)
    )
    info["history"] = [
        f"{a.created_at.strftime('%m-%d')} {a.actor.display_name if a.actor else ''} "
        f"{a.field}: {a.old_value} -> {a.new_value}"
        for a in history
    ]
    messages = [
        {"role": "system", "content": (
            "你是项目助理。用中文 Markdown 总结这个问题："
            "**一句话结论**、**当前进展**、**关键讨论/决策**、**风险与阻塞**、**下一步（含负责人）**。"
            "简洁，不超过 250 字，不编造信息。")},
        {"role": "user", "content": json.dumps(info, ensure_ascii=False)[:20000]},
    ]
    return issue.key, messages


def summarize_snapshot_stream(
    user_id: int,
    issue_key: str,
    messages: list[dict[str, str]],
) -> Iterator[str]:
    started = time.time()
    collected: list[str] = []
    try:
        for piece in llm.stream_chat(messages, max_tokens=1200):
            collected.append(piece)
            yield piece
        _audit_user(user_id, "summary", issue_key, result="".join(collected), started=started)
    except llm.LlmError as error:
        _audit_user(user_id, "summary", issue_key, result=str(error), ok=False, started=started)
        raise


def summarize_issue_stream(db: Session, user: User, issue: Issue) -> Iterator[str]:
    issue_key, messages = prepare_summary(db, issue)
    yield from summarize_snapshot_stream(user.id, issue_key, messages)


def _report_data(db: Session, project: Project, period: str,
                 org_unit: OrgUnit | None) -> dict[str, Any]:
    days = 1 if period == "daily" else 7
    since = utcnow() - timedelta(days=days)
    member_ids: set[int] | None = None
    if org_unit is not None:
        unit_ids = descendant_ids(db.scalars(select(OrgUnit)), org_unit.id)
        member_ids = set(db.scalars(select(User.id).where(User.org_unit_id.in_(unit_ids))))

    def scoped(issue: Issue) -> bool:
        if member_ids is None:
            return True
        return bool({issue.assignee_id, issue.reporter_id, issue.tester_id} & member_ids)

    issues = [i for i in db.scalars(issue_query().where(Issue.project_id == project.id))
              if scoped(i)]
    created = [compact(i) for i in issues if i.created_at and _aware(i.created_at) >= since]
    resolved = [compact(i) for i in issues if i.resolved_at and _aware(i.resolved_at) >= since]
    open_items = [i for i in issues if i.status.category != "done"]
    overdue = [compact(i) for i in open_items if i.due_date and i.due_date < date.today()]
    high = [compact(i) for i in open_items if i.priority in {"highest", "high"}]
    moves = db.execute(
        select(ActivityLog.issue_id, ActivityLog.old_value, ActivityLog.new_value)
        .join(Issue, Issue.id == ActivityLog.issue_id)
        .where(Issue.project_id == project.id, ActivityLog.field == "status_id",
               ActivityLog.created_at >= since)
    ).all()
    keys = {i.id: i.key for i in issues}
    moves = [m for m in moves if m[0] in keys]
    status_counts = Counter(i.status.name for i in open_items)
    testing = [compact(i) for i in open_items
               if i.status.name == (project.settings or {}).get("test_status", "测试中")]
    failed = [compact(i) for i in issues if i.test_passed is False and i.updated_at
              and _aware(i.updated_at) >= since]
    totals = {"created": len(created), "resolved": len(resolved), "open": len(open_items),
              "overdue": len(overdue), "in_testing": len(testing), "test_failed": len(failed),
              "open_high_priority": len(high)}
    period_text = f"{(date.today() - timedelta(days=days)).isoformat()} ~ {date.today().isoformat()}"
    forecast = _forecast_summary(db, project)
    scope = org_unit.path if org_unit else "全项目"
    return {
        "project": f"{project.key} {project.name}",
        "scope": scope,
        "period": period_text,
        "totals": totals,
        "created": created[:40],
        "resolved": resolved[:40],
        "status_moves": [f"{keys.get(m[0], m[0])}: {m[1]} -> {m[2]}" for m in moves][:60],
        "open_by_status": dict(status_counts),
        "in_testing": testing[:30],
        "test_failed": failed[:20],
        "overdue": overdue[:30],
        "open_high_priority": high[:20],
        "forecast": forecast,
        "metrics": {**_report_metrics(issues, open_items, totals, period_text, scope),
                    "forecast": forecast},
    }


TREND_DAYS = 7
FORECAST_SAMPLE_DAYS = 30
FORECAST_CURVE_POINTS = 8


def _forecast_summary(db: Session, project: Project) -> dict[str, Any] | None:
    """Delivery forecast of the active sprint (or all open work) for reports and their charts.

    It is project-wide even for a department report: throughput is a team-level signal."""
    try:
        result = planning.forecast(db, project, FORECAST_SAMPLE_DAYS, scope="sprint")
    except Exception:  # a forecast problem must never block the report itself
        LOGGER.exception("Forecast for %s report failed", project.key)
        return None
    scope = result["scope"]
    # Ongoing projects only keep a sprint-scoped finish card, never a project finish date.
    if permissions.is_ongoing(project) and scope.get("kind") != "sprint":
        return None
    curve = result["curve"]
    step = max(1, math.ceil(len(curve) / FORECAST_CURVE_POINTS))
    sampled = curve[::step]
    if curve and sampled[-1] is not curve[-1]:
        sampled.append(curve[-1])
    finish = result["finish"] or {}
    return {
        "scope": {"kind": scope["kind"], "name": scope.get("name") or scope.get("key"),
                  "total": scope["total"], "remaining": scope["remaining"],
                  "end_date": scope.get("end_date")},
        "sample_days": FORECAST_SAMPLE_DAYS,
        "throughput_total": result["throughput"]["total"],
        "throughput_per_week": result["throughput"]["per_week"],
        "finish": {k: finish.get(k) for k in ("p50", "p85", "p95")} if finish else None,
        "unreachable_pct": round(result["unreachable"] * 100, 1),
        "target": result["target"],
        "curve": [{"date": c["date"][5:], "probability": c["probability"]} for c in sampled],
    }
PRIORITY_ORDER = ("highest", "high", "medium", "low", "lowest")


def _report_metrics(issues: list[Issue], open_items: list[Issue], totals: dict[str, int],
                    period_text: str, scope: str) -> dict[str, Any]:
    """Chart data for the HTML report: daily trend, open work by status/priority/assignee."""
    today = date.today()

    def day(value: Any) -> date:
        return _aware(value).astimezone().date()

    created_days = Counter(day(i.created_at) for i in issues if i.created_at)
    resolved_days = Counter(day(i.resolved_at) for i in issues if i.resolved_at)
    trend = []
    for offset in range(TREND_DAYS - 1, -1, -1):
        current = today - timedelta(days=offset)
        trend.append({"date": current.strftime("%m-%d"), "created": created_days[current],
                      "resolved": resolved_days[current]})
    statuses: dict[int, dict[str, Any]] = {}
    for issue in open_items:
        row = statuses.setdefault(issue.status_id, {
            "name": issue.status.name, "color": issue.status.color, "count": 0,
            "order": issue.status.sort_order})
        row["count"] += 1
    priorities = Counter(i.priority for i in open_items)
    load: dict[str, dict[str, Any]] = {}
    for issue in open_items:
        name = issue.assignee.display_name if issue.assignee else "未分配"
        row = load.setdefault(name, {"name": name, "open": 0, "overdue": 0})
        row["open"] += 1
        row["overdue"] += int(bool(issue.due_date and issue.due_date < today))
    return {
        "period": period_text,
        "scope": scope,
        "totals": totals,
        "trend": trend,
        "by_status": [
            {k: v for k, v in row.items() if k != "order"}
            for row in sorted(statuses.values(), key=lambda r: (r["order"], r["name"]))
        ],
        "by_priority": [{"key": p, "count": priorities[p]} for p in PRIORITY_ORDER
                        if priorities[p]],
        "workload": sorted(load.values(), key=lambda r: (-r["open"], r["name"]))[:8],
    }


@dataclass(frozen=True)
class ReportSnapshot:
    user_id: int
    project_id: int
    project_key: str
    project_name: str
    period: str
    org_unit_id: int | None
    org_unit_label: str | None
    data: dict[str, Any]
    metrics: dict[str, Any]


def prepare_report(
    db: Session,
    user: User,
    project: Project,
    period: str,
    org_unit: OrgUnit | None,
) -> ReportSnapshot:
    data = _report_data(db, project, period, org_unit)
    metrics = data.pop("metrics")
    return ReportSnapshot(
        user.id,
        project.id,
        project.key,
        project.name,
        period,
        org_unit.id if org_unit else None,
        org_unit.label if org_unit else None,
        data,
        metrics,
    )


def _report_messages(data: dict[str, Any], label: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": (
            f"你是项目经理助理，请根据数据撰写中文{label}（Markdown）。结构："
            "## 概览\n## 本期完成\n## 新增需求\n## 进行中与测试\n## 风险与逾期\n## 交付预测\n## 下期建议。"
            "交付预测依据 forecast：基于近 sample_days 天每日完成量的蒙特卡洛模拟，"
            "finish.p50/p85/p95 为有 50%/85%/95% 把握完成剩余工作的日期，"
            "target.probability 为在 target.date（冲刺结束日或两周后）前完成的概率；"
            "请解读是否能按期交付、主要风险和需要的吞吐量；forecast 为空或 finish 为空时说明数据不足。"
            "关键数字、趋势与分布图表由系统另行生成并放在正文之前，"
            "概览只需用 2-3 句话点出结论和最值得关注的变化，数字以 totals 为准。"
            "引用问题时使用 KEY。数据为空的部分写“无”。不要编造。")},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)[:24000]},
    ]


def report_snapshot_stream(
    snapshot: ReportSnapshot,
    *,
    on_saved: Callable[[int], None] | None = None,
) -> Iterator[str]:
    """Stream from frozen report data, then save and audit in fresh short sessions."""
    started = time.time()
    label = "日报" if snapshot.period == "daily" else "周报"
    collected: list[str] = []
    try:
        for piece in llm.stream_chat(
            _report_messages(snapshot.data, label), max_tokens=2500
        ):
            collected.append(piece)
            yield piece
    except llm.LlmError as error:
        _audit_user(
            snapshot.user_id,
            "report",
            f"{snapshot.project_key} {snapshot.period}",
            result=str(error),
            ok=False,
            started=started,
        )
        raise
    content = "".join(collected)
    if content.strip():
        title = f"{snapshot.project_name} {label} {date.today().isoformat()}"
        if snapshot.org_unit_label:
            title += f"（{snapshot.org_unit_label}）"
        with session_factory()() as db:
            report = AiReport(
                project_id=snapshot.project_id,
                org_unit_id=snapshot.org_unit_id,
                period=snapshot.period,
                title=title,
                content=content,
                metrics=snapshot.metrics,
                created_by_id=snapshot.user_id,
                scheduled=False,
            )
            db.add(report)
            db.commit()
            report_id = report.id
        if on_saved is not None:
            on_saved(report_id)
    _audit_user(
        snapshot.user_id,
        "report",
        f"{snapshot.project_key} {snapshot.period}",
        result=content[:2000],
        started=started,
    )


def report_stream(db: Session, user: User | None, project: Project, period: str,
                  org_unit: OrgUnit | None = None, *, save: bool = True,
                  scheduled: bool = False,
                  on_saved: Callable[[AiReport], None] | None = None) -> Iterator[str]:
    started = time.time()
    data = _report_data(db, project, period, org_unit)
    metrics = data.pop("metrics")
    label = "日报" if period == "daily" else "周报"
    messages = _report_messages(data, label)
    collected: list[str] = []
    try:
        for piece in llm.stream_chat(messages, max_tokens=2500):
            collected.append(piece)
            yield piece
    except llm.LlmError as error:
        audit(db, user, "report", f"{project.key} {period}", result=str(error), ok=False,
              started=started)
        raise
    content = "".join(collected)
    if save and content.strip():
        title = f"{project.name} {label} {date.today().isoformat()}"
        if org_unit is not None:
            title += f"（{org_unit.label}）"
        report = AiReport(project_id=project.id, org_unit_id=org_unit.id if org_unit else None,
                          period=period, title=title, content=content, metrics=metrics,
                          created_by_id=user.id if user else None, scheduled=scheduled)
        db.add(report)
        db.commit()
        if on_saved is not None:
            on_saved(report)
    audit(db, user, "report", f"{project.key} {period}", result=content[:2000], started=started)


def generate_scheduled_reports(db: Session, period: str = "weekly") -> int:
    count = 0
    projects = list(db.scalars(select(Project).where(Project.archived.is_(False))))
    for project in projects:
        active = db.scalar(
            select(func.count(Issue.id)).join(Status, Status.id == Issue.status_id)
            .where(Issue.project_id == project.id, Status.category != "done")
        )
        if not active:
            continue
        saved: list[AiReport] = []
        try:
            for _ in report_stream(db, None, project, period, scheduled=True,
                                   on_saved=saved.append):
                pass
            count += 1
        except llm.LlmError as error:
            LOGGER.warning("Scheduled report for %s failed: %s", project.key, error)
            continue
        for report in saved:
            from .reminders import mail_report

            LOGGER.info(
                "Queued %s report mail for %s: %s",
                period,
                project.key,
                mail_report(db, project, report),
            )
            db.commit()
    return count


__all__ = [
    "draft_issues",
    "generate_scheduled_reports",
    "recall_candidates",
    "report_stream",
    "resolve_project",
    "run_chat",
    "summarize_issue_stream",
    "triage",
]
