"""Turn a short instruction into a reviewable workflow draft for the editor.

The result is not saved. The project settings page applies it to the local canvas,
where the user can still edit it before pressing save.
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Issue, Project, User
from ..models.issue import ISSUE_TYPES
from . import llm, profiles, workflow, workflow_audit
from .agent import audit

MAX_TURNS = 8

OUTPUT = """只输出一个 JSON 对象，不要写解释性前后文：
{"explanation": "一句话说明改了什么",
 "transitions": {"源状态": ["目标状态", "其他目标"]},
 "new_statuses": [{"name": "新状态名", "category": "todo"}],
 "question": null}
规则：
1. transitions 必须是完整流转表，不是补丁。每个状态的目标数组里，第一个是主线的下一步。
2. 已有状态必须用列表中的原始名称，不要翻译或改名。
3. 用户明确要求新增状态时，把新状态写入 new_statuses。
   category 只能是 todo、doing 或 done，name 用用户说的原文，并在 transitions 里使用同一名称。
   没有要求新增时 new_statuses 为空数组。
4. 用户没提到的连线保持不变；用户明确要求删除、替换或恢复默认时才改掉。
5. 不要写自己流转到自己。不要输出空数组。
6. 意图不清时 transitions 设为 null，并用 question 提一个简短问题。
7. explanation 和 question 用用户界面语言；状态名保持原文。
8. 进入测试中前的软件准备好、验证方法和验证标准是字段门禁，不是新状态。
   用户要求软件没准备好不能进测试时，不要新增状态，transitions 保持原样，
   explanation 说明这是已有字段门禁。用户另外明确点名要新增某个状态时，仍按规则 3。
9. 电气需求进入研发中前的三方方案确认也是字段门禁，不是新状态。
   用户要求未确认不能进入研发中时，不要新增状态，transitions 保持原样。"""

STATUS_COLORS = {"todo": "#8e8e93", "doing": "#0a84ff", "done": "#30d158"}
MAX_NEW_STATUSES = 8
SOFTWARE_GATE_NOTE = (
    "涉及软件的需求进入测试中前，要先有软件负责人确认准备好，并填好验证方法和验证标准。"
    "这不是连线问题，修复不会为此新增状态。"
)
SCHEME_GATE_NOTE = (
    "电气需求进入研发中前，要先填写方案说明、发起方案确认，并由电气、工艺"
    "以及涉及软件时的软件负责人确认为是。这不是连线问题，修复不会为此新增状态。"
)


def gate_notes(project: Project, issue_type: str) -> list[str]:
    """A non-actionable note. Optimize must not turn this into a new status."""
    profile = profiles.profile_of(project)
    notes = []
    if profiles.form_has_field(profile, "software_ready", issue_type):
        notes.append(SOFTWARE_GATE_NOTE)
    if profiles.form_has_field(profile, "scheme_note", issue_type):
        notes.append(SCHEME_GATE_NOTE)
    return notes


def _clean(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    turns = [
        {"role": message["role"], "content": str(message.get("content") or "")[:2000]}
        for message in messages
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
    return turns[-MAX_TURNS:]


def normalize_rules(raw: object) -> dict[str, list[str]] | None:
    """Drop malformed entries and self-loops. Unknown names stay for the linter."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    clean: dict[str, list[str]] = {}
    for source, targets in raw.items():
        if not isinstance(source, str) or not isinstance(targets, list):
            continue
        seen: list[str] = []
        for target in targets:
            if isinstance(target, str) and target != source and target not in seen:
                seen.append(target)
        if seen:
            clean[source] = seen
    return clean


def normalize_new_statuses(raw: object, existing: set[str]) -> list[dict[str, str]]:
    """Keep only genuinely new statuses. Existing names are not renamed or duplicated."""
    if not isinstance(raw, list):
        return []
    found: list[dict[str, str]] = []
    seen = set(existing)
    for item in raw:
        if not isinstance(item, dict) or len(found) >= MAX_NEW_STATUSES:
            continue
        name = str(item.get("name") or "").strip()
        category = item.get("category")
        if category not in STATUS_COLORS or not name or len(name) > 64 or name in seen:
            continue
        color = item.get("color")
        if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
            color = STATUS_COLORS[category]
        found.append({"name": name, "category": category, "color": color.lower()})
        seen.add(name)
    return found


def declare_missing_statuses(
    rules: dict[str, list[str]],
    declared: list[dict[str, str]],
    existing: set[str],
) -> list[dict[str, str]]:
    """A transition to an unknown name is a request to add that status."""
    known = set(existing) | {item["name"] for item in declared}
    mentioned = {source for source in rules}
    mentioned.update(target for targets in rules.values() for target in targets)
    missing = sorted(mentioned - known)
    found = list(declared)
    for name in missing:
        if len(found) >= MAX_NEW_STATUSES or len(name) > 64:
            continue
        found.append({"name": name, "category": "todo", "color": STATUS_COLORS["todo"]})
    return found


def _edges(rules: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {(source, target) for source, targets in rules.items() for target in targets}


def diff_rules(
    current: dict[str, list[str]],
    draft: dict[str, list[str]],
) -> dict[str, list[dict[str, str]]]:
    current_edges = _edges(current)
    draft_edges = _edges(draft)
    return {
        "add": [
            {"from": source, "to": target}
            for source, target in sorted(draft_edges - current_edges)
        ],
        "remove": [
            {"from": source, "to": target}
            for source, target in sorted(current_edges - draft_edges)
        ],
    }


def _ask(prompt: list[dict[str, Any]]) -> dict[str, Any]:
    message = llm.chat(prompt, temperature=0.1, max_tokens=1200, json_mode=True)
    parsed = llm.parse_json(message.get("content"))
    if not isinstance(parsed, dict):
        raise llm.LlmError("无法解析 LLM 返回的 JSON")
    return parsed


def _lint(
    pairs: list[tuple[str, str]],
    rules: dict[str, list[str]] | None,
    issue_type: str,
) -> list[workflow_audit.Finding]:
    if not rules:
        return [workflow_audit.Finding("error", issue_type, "工作流为空", "empty")]
    return workflow_audit.lint_flow(pairs, rules, issue_type)


def _public_findings(findings: list[workflow_audit.Finding]) -> list[dict[str, Any]]:
    return [
        {
            "level": item.level,
            "code": item.code,
            "message": item.message,
            "statuses": list(item.statuses),
        }
        for item in findings
    ]


def _raw_rules(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    rules: dict[str, list[str]] = {}
    for source, targets in raw.items():
        if not isinstance(source, str) or not isinstance(targets, list):
            continue
        seen: list[str] = []
        for target in targets:
            if isinstance(target, str) and target not in seen:
                seen.append(target)
        if seen:
            rules[source] = seen
    return rules


def status_catalog(project: Project) -> dict[str, tuple[str, str]]:
    """Template status name to category and color, including the built-in styles."""
    from .profiles import STATUS_STYLES, profile_of

    catalog = dict(STATUS_STYLES)
    definition = profile_of(project).definition or {}
    for item in definition.get("statuses") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        fallback = catalog.get(name, ("todo", "#8e8e93"))
        category = item.get("category")
        if category not in {"todo", "doing", "done"}:
            category = fallback[0]
        color = item.get("color")
        if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
            color = fallback[1]
        catalog[name] = (category, color.lower())
    return catalog


def _with_catalog_styles(
    added: list[dict[str, str]], catalog: dict[str, tuple[str, str]],
) -> list[dict[str, str]]:
    """A known template status keeps its category. Other new names stay as declared."""
    styled: list[dict[str, str]] = []
    for item in added:
        style = catalog.get(item["name"])
        if style is None:
            styled.append(item)
            continue
        category, color = style
        styled.append({"name": item["name"], "category": category, "color": color})
    return styled


def _merge_created(
    base: list[dict[str, str]], extra: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen = {item["name"] for item in base}
    merged = list(base)
    for item in extra:
        if item["name"] in seen:
            continue
        merged.append(item)
        seen.add(item["name"])
    return merged


def _issue_counts(db: Session, project: Project, issue_type: str) -> dict[str, int]:
    rows = db.execute(
        select(Issue.status_id, func.count())
        .where(Issue.project_id == project.id, Issue.type == issue_type)
        .group_by(Issue.status_id)
    ).all()
    names = {status.id: status.name for status in workflow.project_statuses(db, project.id)}
    return {names[status_id]: int(count) for status_id, count in rows if status_id in names}


def assist(
    db: Session,
    user: User,
    project: Project,
    issue_type: str,
    transitions: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.time()
    if issue_type not in ISSUE_TYPES:
        raise ValueError(f"未知的问题类型：{issue_type}")
    turns = _clean(messages)
    if not turns or turns[-1]["role"] != "user":
        raise ValueError("请先说明想怎么改这条流程")
    current = normalize_rules(transitions) or {}
    statuses = workflow.project_statuses(db, project.id)
    pairs = [(status.name, status.category) for status in statuses]
    template = workflow.template_transitions(project).get(issue_type) or {}
    system = "\n".join([
        "你在项目设置的工作流编辑器里，按用户的一句话修改当前类型的未保存草稿。",
        f"项目：{project.key}。问题类型：{issue_type}。",
        "可用状态（按顺序，括号内是类别 todo/doing/done）："
        + "、".join(f"{name}（{category}）" for name, category in pairs),
        "当前画布上的草稿：" + json.dumps(current, ensure_ascii=False),
        "当前检查：" + "；".join(
            f"[{item.code}] {item.message}" for item in _lint(pairs, current, issue_type)
        ),
        "该类型的模板默认流程：" + json.dumps(template, ensure_ascii=False),
        "从初始状态必须能到达一个已完成状态，且不能把进行中的状态收成死胡同。",
        "用户要求增加状态时可以新增，不要拒绝；新状态写进 new_statuses。",
        "已有问题不会因为改流程而自动迁移。",
        OUTPUT,
    ])
    prompt: list[dict[str, Any]] = [{"role": "system", "content": system}, *turns]
    instruction = turns[-1]["content"]
    existing = {name for name, _category in pairs}
    catalog = status_catalog(project)

    def reviewed(
        parsed: dict[str, Any],
    ) -> tuple[dict[str, list[str]] | None, list[dict[str, str]], list[workflow_audit.Finding]]:
        draft = normalize_rules(parsed.get("transitions"))
        added = normalize_new_statuses(parsed.get("new_statuses"), existing)
        if draft is not None:
            added = _with_catalog_styles(
                declare_missing_statuses(draft, added, existing), catalog,
            )
        review_pairs = [*pairs, *[(item["name"], item["category"]) for item in added]]
        findings = _lint(review_pairs, draft, issue_type) if draft is not None else []
        return draft, added, findings

    try:
        parsed = _ask(prompt)
        draft, added, findings = reviewed(parsed)
        errors = [item.message for item in findings if item.level == "error"]
        if errors:
            prompt += [
                {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                {"role": "user", "content": (
                    "这个草稿不能用：" + "；".join(errors)
                    + "。请重新输出完整 JSON。需要的新状态写入 new_statuses，"
                    + "已有状态必须用原始名称，不要改名。"
                )},
            ]
            parsed = _ask(prompt)
            draft, added, findings = reviewed(parsed)
    except llm.LlmError as failure:
        audit(db, user, "workflow_draft", instruction, result=str(failure), ok=False,
              started=started)
        raise
    question = parsed.get("question") or None
    explanation = str(parsed.get("explanation") or "").strip()
    valid = draft is not None and not any(item.level == "error" for item in findings)
    if draft is not None and not valid and not question:
        review_pairs = [*pairs, *[(item["name"], item["category"]) for item in added]]
        healed_pairs, healed_rules, healed_creates = workflow_audit.heal_flow(
            review_pairs, draft, issue_type=issue_type, template=template,
            catalog=status_catalog(project),
        )
        healed_findings = _lint(healed_pairs, healed_rules, issue_type)
        if not any(item.level == "error" for item in healed_findings):
            draft = healed_rules
            added = _merge_created(added, healed_creates)
            findings = healed_findings
            valid = True
            note = "结构错误已按现有连线和模板补成可保存的流程。"
            explanation = f"{explanation} {note}".strip() if explanation else note
    issue_count = int(db.scalar(
        select(func.count()).select_from(Issue).where(
            Issue.project_id == project.id, Issue.type == issue_type,
        )
    ) or 0)
    result = {
        "explanation": explanation,
        "question": question,
        "transitions": draft,
        "new_statuses": added,
        "diff": diff_rules(current, draft) if draft is not None else {"add": [], "remove": []},
        "findings": _public_findings(findings),
        "notes": gate_notes(project, issue_type),
        "valid": valid,
        "issue_count": issue_count,
    }
    audit(
        db, user, "workflow_draft", instruction,
        result=json.dumps(
            {"valid": valid, "diff": result["diff"]}, ensure_ascii=False,
        ),
        ok=valid or bool(question),
        started=started,
    )
    return result


def optimize(
    db: Session,
    user: User,
    project: Project,
    issue_type: str,
    transitions: dict[str, Any],
    suggestion_ids: list[str],
) -> dict[str, Any]:
    """Diagnose the current canvas and preview selected repairs. Nothing is saved."""
    started = time.time()
    if issue_type not in ISSUE_TYPES:
        raise ValueError(f"未知的问题类型：{issue_type}")
    current = _raw_rules(transitions)
    statuses = workflow.project_statuses(db, project.id)
    pairs = [(status.name, status.category) for status in statuses]
    counts = _issue_counts(db, project, issue_type)
    template = workflow.template_transitions(project).get(issue_type) or {}
    catalog = status_catalog(project)
    suggestions = workflow_audit.suggest_repairs(
        pairs, current, issue_type=issue_type, template=template,
        issue_counts=counts, catalog=catalog,
    )
    findings = _lint(pairs, normalize_rules(current) or {}, issue_type)
    selected = [item for item in suggestion_ids if item][:40]
    draft = None
    created: list[dict[str, str]] = []
    diff: dict[str, list[dict[str, str]]] = {"add": [], "remove": []}
    valid = False
    explanation = ""
    if selected:
        chosen = set(selected)
        review, applied, created = workflow_audit.materialize_repairs(
            pairs, current, suggestions, chosen,
        )
        required = {
            item["id"] for item in suggestions
            if item["level"] == "required" and (item["add"] or item["remove"] or item["create"])
        }
        if required and required <= chosen:
            errors = [
                item for item in workflow_audit.lint_flow(review, applied, issue_type)
                if item.level == "error"
            ]
            if errors:
                review, applied, more = workflow_audit.heal_flow(
                    review, applied, issue_type=issue_type, template=template,
                    issue_counts=counts, catalog=catalog,
                )
                created = _merge_created(created, more)
        draft = normalize_rules(applied) or {}
        findings = _lint(review, draft, issue_type)
        valid = not any(item.level == "error" for item in findings)
        diff = diff_rules(normalize_rules(current) or {}, draft)
        if created:
            explanation = "补回连线还在使用的状态，并保证从起点能走到完成。"
        audit(
            db, user, "workflow_optimize", " ".join(selected),
            result=json.dumps({"valid": valid, "diff": diff}, ensure_ascii=False),
            ok=valid, started=started,
        )
    return {
        "explanation": explanation,
        "question": None,
        "transitions": draft,
        "new_statuses": created,
        "diff": diff,
        "findings": _public_findings(findings),
        "notes": gate_notes(project, issue_type),
        "valid": valid,
        "issue_count": sum(counts.values()),
        "issues_by_status": counts,
        "suggestions": suggestions,
    }
