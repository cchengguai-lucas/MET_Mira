"""Static checks for workflows: reachability, dead ends, reopen paths, gates and board coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Board, Project
from ..models.issue import ISSUE_TYPES
from . import profiles, workflow


@dataclass(frozen=True)
class Finding:
    level: str  # error | warning | info
    scope: str  # issue type, "gates", "board:<name>", "verification"
    message: str
    code: str = ""
    statuses: tuple[str, ...] = ()


def _flow(level: str, scope: str, message: str, code: str,
          statuses: list[str] | tuple[str, ...] = ()) -> Finding:
    return Finding(level, scope, message, code, tuple(statuses))


def _reach(rules: dict[str, list[str]], start: str) -> set[str]:
    seen, queue = {start}, [start]
    while queue:
        for target in rules.get(queue.pop(), []):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def lint_flow(statuses: list[tuple[str, str]], rules: dict[str, list[str]],
              scope: str) -> list[Finding]:
    """``statuses`` are (name, category) in project order; ``rules`` maps status → targets."""
    category = dict(statuses)
    out: list[Finding] = []
    unknown = sorted({s for s in rules if s not in category}
                     | {t for ts in rules.values() for t in ts if t not in category})
    if unknown:
        out.append(_flow("error", scope, f"引用了不存在的状态：{'、'.join(unknown)}",
                         "unknown_status", unknown))
    used = {s for s in rules if s in category} | {
        t for ts in rules.values() for t in ts if t in category}
    if not used:
        return [*out, _flow("error", scope, "工作流为空", "empty")]
    order = [name for name, _ in statuses if name in used]
    first = order[0]
    if category[first] != "todo":
        out.append(_flow("warning", scope, f"初始状态「{first}」不是待办类状态",
                         "initial_not_todo", [first]))
    looped = [source for source, targets in rules.items() if source in targets]
    for source in looped:
        out.append(_flow("warning", scope, f"「{source}」流转到自身", "self_loop", [source]))
    reachable = _reach(rules, first)
    lost = [name for name in order if name not in reachable]
    if lost:
        out.append(_flow("error", scope, f"从初始状态无法到达：{'、'.join(lost)}",
                         "unreachable", lost))
    done = {name for name in used if category[name] == "done"}
    finish = {name for name in done if name != workflow.REJECTED} or done
    if not reachable & finish:
        out.append(_flow("error", scope, "从初始状态无法到达完成状态", "no_completion",
                         sorted(finish)))
    dead = [
        name for name in order
        if category[name] != "done" and name in reachable and not _reach(rules, name) & done
    ]
    for name in dead:
        out.append(_flow("error", scope, f"进入「{name}」后再也无法完成或驳回（死胡同）",
                         "dead_end", [name]))
    for name in sorted(finish):
        exits = rules.get(name, [])
        if not exits:
            out.append(_flow("warning", scope, f"「{name}」之后没有任何流转，完成后无法重新打开",
                             "no_reopen", [name]))
        elif all(category.get(target) == "done" for target in exits):
            out.append(_flow(
                "warning", scope,
                f"「{name}」只能流转到「{'、'.join(exits)}」，完成的工作无法重新打开",
                "reopen_stuck", [name, *exits],
            ))
    main, step = [first], first
    while rules.get(step):
        step = rules[step][0]
        if step in main:
            break
        main.append(step)
    if not finish & set(main):
        out.append(_flow("warning", scope,
                         f"主线（每步第一个目标）未到达完成状态：{' → '.join(main)}",
                         "main_not_done", main))
    stepwise = [name for name in order if category[name] == "doing" and name in rules]
    no_back = [
        name for name in stepwise
        if not any(target != workflow.REJECTED and order.index(target) < order.index(name)
                   for target in rules[name] if target in order)
    ]
    if stepwise and len(no_back) == len(stepwise):
        out.append(_flow("info", scope, "进行中的步骤都不能退回上一步，只能驳回",
                         "no_step_back", no_back))
    return out


def _edge(source: str, target: str, position: str = "append") -> dict[str, str]:
    return {"from": source, "to": target, "position": position}


def _preferred_done(statuses: list[tuple[str, str]]) -> str | None:
    dones = [name for name, category in statuses
             if category == "done" and name != workflow.REJECTED]
    if dones:
        return dones[0]
    return next((name for name, category in statuses if category == "done"), None)


def _reopen_target(statuses: list[tuple[str, str]]) -> str | None:
    names = [name for name, _category in statuses]
    if workflow.REOPENED in names:
        return workflow.REOPENED
    return next((name for name, category in statuses if category == "todo"), None)


def _usable_template(
    statuses: list[tuple[str, str]],
    template: dict[str, list[str]],
    issue_type: str,
) -> dict[str, list[str]] | None:
    names = {name for name, _category in statuses}
    filtered: dict[str, list[str]] = {}
    for source, targets in template.items():
        if source not in names:
            continue
        kept = [target for target in targets if target in names and target != source]
        if kept:
            filtered[source] = kept
    if not filtered:
        return None
    errors = [item for item in lint_flow(statuses, filtered, issue_type) if item.level == "error"]
    return None if errors else filtered


def _fallback_flow(statuses: list[tuple[str, str]]) -> dict[str, list[str]]:
    todos = [name for name, category in statuses if category == "todo"]
    doings = [name for name, category in statuses if category == "doing"]
    done = _preferred_done(statuses)
    chain = [*todos[:1], *doings, *([done] if done else [])]
    rules: dict[str, list[str]] = {}
    for source, target in zip(chain, chain[1:], strict=False):
        rules.setdefault(source, []).append(target)
    if workflow.REJECTED in {name for name, _category in statuses} and chain:
        rules.setdefault(chain[0], []).append(workflow.REJECTED)
        rules[workflow.REJECTED] = [chain[0]]
    reopen = workflow.REOPENED if workflow.REOPENED in {name for name, _ in statuses} else None
    if reopen and done:
        rules.setdefault(done, []).append(reopen)
        rules[reopen] = [doings[0] if doings else chain[0]]
    return rules


def _suggestion(
    code: str,
    level: str,
    statuses: list[str],
    add: list[dict[str, str]],
    remove: list[dict[str, str]] | None = None,
    issue_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    counts = issue_counts or {}
    return {
        "id": code if len(statuses) != 1 else f"{code}:{statuses[0]}",
        "code": code,
        "level": level,
        "statuses": statuses,
        "add": add,
        "remove": remove or [],
        "create": [],
        "issue_count": sum(counts.get(name, 0) for name in statuses),
    }


def _status_spec(styles: dict[str, tuple[str, str]], name: str) -> dict[str, str] | None:
    style = styles.get(name)
    if style is None or len(name) > 64:
        return None
    category, color = style
    if category not in {"todo", "doing", "done"}:
        return None
    if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
        color = "#8e8e93"
    return {"name": name, "category": category, "color": color.lower()}


def _prepare_flow(
    statuses: list[tuple[str, str]],
    rules: dict[str, list[str]],
    styles: dict[str, tuple[str, str]],
    counts: dict[str, int],
) -> tuple[list[tuple[str, str]], dict[str, list[str]], list[dict[str, Any]]]:
    """Keep lines that name a known template status, and drop the rest.

    A deleted template status is the user's intended step. Removing that line
    leaves a flow that still cannot finish. Stray names are not restored.
    """
    names = {name for name, _category in statuses}
    mentioned = (set(rules) | {target for targets in rules.values() for target in targets}) - names
    style_order = list(styles)
    restorable: list[str] = []
    stray: list[str] = []
    def _style_index(item: str) -> int:
        return style_order.index(item) if item in style_order else 10_000

    for name in sorted(mentioned, key=_style_index):
        if _status_spec(styles, name) is not None:
            restorable.append(name)
        else:
            stray.append(name)
    out: list[dict[str, Any]] = []
    nxt = list(statuses)
    if restorable:
        creates = [spec for name in restorable if (spec := _status_spec(styles, name))]
        item = _suggestion("restore_status", "required", restorable, [], None, counts)
        item["id"] = "restore_status"
        item["create"] = creates
        out.append(item)
        nxt.extend((spec["name"], spec["category"]) for spec in creates)
    stray_names = set(stray)
    current: dict[str, list[str]] = {}
    if stray_names:
        removes = [
            _edge(source, target)
            for source, targets in rules.items()
            for target in targets
            if source in stray_names or target in stray_names
        ]
        item = _suggestion("unknown_status", "required", stray, [], removes, counts)
        item["id"] = "unknown_status"
        out.append(item)
    for source, targets in rules.items():
        if source in stray_names:
            continue
        kept: list[str] = []
        for target in targets:
            if target in stray_names or target in kept:
                continue
            kept.append(target)
        if kept:
            current[source] = kept
    known = {name for name, _category in nxt}
    used = {source for source in current if source in known} | {
        target for targets in current.values() for target in targets if target in known
    }
    if used and _preferred_done(nxt) is None:
        done_name = "已完成" if "已完成" not in known else "完成"
        created = {"name": done_name, "category": "done", "color": "#30d158"}
        item = _suggestion("add_completion", "required", [done_name], [], None, counts)
        item["id"] = "add_completion"
        item["create"] = [created]
        out.append(item)
        nxt.append((done_name, "done"))
    return nxt, current, out


def apply_repairs(
    rules: dict[str, list[str]],
    suggestions: list[dict[str, Any]],
    selected: set[str],
) -> dict[str, list[str]]:
    """Apply selected repair edges. Removals happen before additions."""
    nxt = {source: list(targets) for source, targets in rules.items()}
    chosen = [item for item in suggestions if item["id"] in selected]
    for item in chosen:
        for edge in item.get("remove") or []:
            targets = [target for target in nxt.get(edge["from"], []) if target != edge["to"]]
            if targets:
                nxt[edge["from"]] = targets
            else:
                nxt.pop(edge["from"], None)
    for item in chosen:
        for edge in item.get("add") or []:
            source, target = edge["from"], edge["to"]
            if source == target:
                continue
            targets = list(nxt.get(source, []))
            if edge.get("position") == "first":
                nxt[source] = [target, *[kept for kept in targets if kept != target]]
            elif target not in targets:
                targets.append(target)
                nxt[source] = targets
    return nxt


def suggest_repairs(
    statuses: list[tuple[str, str]],
    rules: dict[str, list[str]],
    *,
    issue_type: str,
    template: dict[str, list[str]] | None = None,
    issue_counts: dict[str, int] | None = None,
    catalog: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Concrete repairs for one workflow. Nothing here is saved.

    Lines that still name a removed template status restore that status.
    A flow with no completion status gains one. Other repairs then run on
    that shape, so the required set can be applied together.
    """
    counts = issue_counts or {}
    template = template or {}
    styles = catalog if catalog is not None else profiles.STATUS_STYLES
    statuses, current, out = _prepare_flow(list(statuses), rules, styles, counts)
    names = {name for name, _category in statuses}
    category = dict(statuses)
    order = [name for name, _category in statuses]
    findings = lint_flow(statuses, current, issue_type) if current else [
        _flow("error", issue_type, "工作流为空", "empty"),
    ]
    codes = {item.code for item in findings}
    used = {source for source in current if source in names} | {
        target for targets in current.values() for target in targets if target in names
    }
    flow_order = [name for name in order if name in used]
    first = flow_order[0] if flow_order else ""
    reachable = _reach(current, first) if first else set()
    done = _preferred_done(statuses)
    reopen = _reopen_target(statuses)
    proposed: set[tuple[str, str]] = set()

    def remember(edges: list[dict[str, str]]) -> None:
        proposed.update((edge["from"], edge["to"]) for edge in edges)

    if "empty" in codes or not used:
        work = list(statuses)
        extra: list[dict[str, str]] = []
        filled = _usable_template(work, template, issue_type) or _fallback_flow(work)
        filled_errors = [
            item for item in lint_flow(work, filled, issue_type) if item.level == "error"
        ]
        if filled_errors and _preferred_done(work) is None and work:
            extra = [{"name": "已完成", "category": "done", "color": "#30d158"}]
            work = [*work, ("已完成", "done")]
            filled = _usable_template(work, template, issue_type) or _fallback_flow(work)
        edges = [_edge(source, target) for source, targets in filled.items() for target in targets]
        if edges:
            item = _suggestion("empty", "required", flow_order or order[:1], edges, None, counts)
            item["id"] = "empty"
            item["create"] = extra
            out.append(item)
            remember(edges)
        return out

    loops = [
        _edge(source, source) for source, targets in rules.items() if source in targets
    ]
    if loops:
        item = _suggestion("self_loop", "recommended", [edge["from"] for edge in loops], [],
                           loops, counts)
        item["id"] = "self_loop"
        out.append(item)

    lost = [name for name in flow_order if name not in reachable]
    for name in lost:
        earlier = [
            item for item in flow_order
            if item in reachable and order.index(item) < order.index(name)
        ]
        source = earlier[-1] if earlier else first
        if source and source != name:
            edges = [_edge(source, name)]
            out.append(_suggestion("unreachable", "required", [name], edges, None, counts))
            remember(edges)

    dead = [item.statuses[0] for item in findings if item.code == "dead_end" and item.statuses]
    leaves = [
        name for name in dead
        if not any(other != name and other in _reach(current, name) for other in dead)
    ]
    for name in leaves:
        if done and done != name:
            edges = [_edge(name, done)]
            out.append(_suggestion("dead_end", "required", [name], edges, None, counts))
            remember(edges)

    needs_completion = (
        "no_completion" in codes and done and first
        and not any(item["code"] == "dead_end" for item in out)
    )
    if needs_completion:
        source = next(
            (name for name in reversed(flow_order)
             if name in reachable and category.get(name) != "done"),
            first,
        )
        edges = [_edge(source, done)]
        out.append(_suggestion("no_completion", "required", [source, done], edges, None, counts))
        remember(edges)

    if "main_not_done" in codes and done:
        main_finding = next(item for item in findings if item.code == "main_not_done")
        main = list(main_finding.statuses)
        edges: list[dict[str, str]] = []
        for name in main:
            targets = current.get(name, [])
            finish = [
                target for target in targets
                if category.get(target) == "done" and target != workflow.REJECTED
            ]
            if finish and targets[0] != finish[0]:
                edges = [_edge(name, finish[0], "first")]
                break
        if not edges and main:
            edges = [_edge(main[-1], done, "first")]
        if edges:
            item = _suggestion("main_not_done", "recommended", main, edges, None, counts)
            item["id"] = "main_not_done"
            out.append(item)
            remember(edges)

    finish_names = [
        name for name in flow_order
        if category.get(name) == "done" and name != workflow.REJECTED
    ]
    if not finish_names:
        finish_names = [name for name in flow_order if category.get(name) == "done"]
    if reopen:
        closed = [name for name in finish_names if not current.get(name)]
        if closed:
            edges = [_edge(name, reopen) for name in closed if name != reopen]
            if edges:
                item = _suggestion("no_reopen", "optional", closed, edges, None, counts)
                item["id"] = "no_reopen"
                out.append(item)
                remember(edges)
        stuck = [
            name for name in finish_names
            if current.get(name) and all(category.get(target) == "done" for target in current[name])
        ]
        edges = [_edge(name, reopen) for name in stuck if reopen not in current.get(name, [])]
        if edges:
            item = _suggestion("reopen_stuck", "optional", stuck, edges, None, counts)
            item["id"] = "reopen_stuck"
            out.append(item)
            remember(edges)

    if "no_step_back" in codes:
        finding = next(item for item in findings if item.code == "no_step_back")
        edges = []
        for name in finding.statuses:
            earlier = [
                item for item in flow_order
                if order.index(item) < order.index(name) and item != workflow.REJECTED
            ]
            if earlier:
                edges.append(_edge(name, earlier[-1]))
        if edges:
            item = _suggestion(
                "no_step_back", "optional", list(finding.statuses), edges, None, counts,
            )
            item["id"] = "no_step_back"
            out.append(item)
            remember(edges)

    template_names = set(template) | {target for targets in template.values() for target in targets}
    prefix = [
        name for name in order
        if name not in used and first and order.index(name) < order.index(first)
    ]
    if prefix and first:
        chain = [*prefix, first]
        edges = [
            _edge(source, target) for source, target in zip(chain, chain[1:], strict=False)
        ]
        level = "recommended" if any(
            counts.get(name) or name in template_names for name in prefix
        ) else "optional"
        item = _suggestion("bring_forward", level, prefix, edges, None, counts)
        item["id"] = "bring_forward"
        out.append(item)
        remember(edges)

    rest_edges: list[dict[str, str]] = []
    rest_names: list[str] = []
    for name in order:
        if name in used or name in prefix:
            continue
        source = next(
            (item for item in reversed(flow_order)
             if item in reachable and category.get(item) != "done"),
            first,
        )
        edges = []
        if source and source != name:
            edges.append(_edge(source, name))
        if category.get(name) != "done" and done and done != name:
            edges.append(_edge(name, done))
        if not edges:
            continue
        if counts.get(name, 0) > 0 or name in template_names:
            out.append(_suggestion("unused_status", "recommended", [name], edges, None, counts))
            remember(edges)
        else:
            rest_names.append(name)
            rest_edges.extend(edges)
    if rest_names:
        item = _suggestion("unused_status", "optional", rest_names, rest_edges, None, counts)
        item["id"] = "unused_rest"
        out.append(item)
        remember(rest_edges)

    template_edges = [
        _edge(source, target)
        for source, targets in template.items()
        for target in targets
        if source in names and target in names and source != target
        and target not in current.get(source, [])
        and (source, target) not in proposed
    ]
    if template_edges:
        involved = {edge["from"] for edge in template_edges}
        involved.update(edge["to"] for edge in template_edges)
        item = _suggestion(
            "template_edges", "optional", sorted(involved), template_edges, None, counts,
        )
        item["id"] = "template_edges"
        out.append(item)
    rank = {"required": 0, "recommended": 1, "optional": 2}
    out.sort(key=lambda item: (rank.get(item["level"], 9), item["id"]))
    return out


def materialize_repairs(
    statuses: list[tuple[str, str]],
    rules: dict[str, list[str]],
    suggestions: list[dict[str, Any]],
    selected: set[str],
) -> tuple[list[tuple[str, str]], dict[str, list[str]], list[dict[str, str]]]:
    """Apply selected repairs and collect statuses those repairs create."""
    pairs = list(statuses)
    known = {name for name, _category in pairs}
    creates: list[dict[str, str]] = []
    for item in suggestions:
        if item["id"] not in selected:
            continue
        for created in item.get("create") or []:
            name = str(created["name"])
            if name in known:
                continue
            creates.append(created)
            known.add(name)
            pairs.append((name, created["category"]))
    return pairs, apply_repairs(rules, suggestions, selected), creates


def heal_flow(
    statuses: list[tuple[str, str]],
    rules: dict[str, list[str]],
    *,
    issue_type: str,
    template: dict[str, list[str]] | None = None,
    issue_counts: dict[str, int] | None = None,
    catalog: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, list[str]], list[dict[str, str]]]:
    """Apply required repairs until the flow has no structural error."""
    pairs = list(statuses)
    current = {source: list(targets) for source, targets in rules.items()}
    creates: list[dict[str, str]] = []
    seen: set[str] = set()
    for _ in range(4):
        suggestions = suggest_repairs(
            pairs, current, issue_type=issue_type, template=template,
            issue_counts=issue_counts, catalog=catalog,
        )
        required = [
            item for item in suggestions
            if item["level"] == "required" and item["id"] not in seen
        ]
        if not required:
            break
        seen.update(item["id"] for item in required)
        pairs, current, more = materialize_repairs(
            pairs, current, required, {item["id"] for item in required},
        )
        creates.extend(more)
        errors = [item for item in lint_flow(pairs, current, issue_type) if item.level == "error"]
        if not errors:
            break
    return pairs, current, creates


def validate_transitions(
    statuses: list[tuple[str, str]],
    raw: object,
) -> dict[str, dict[str, list[str]]]:
    """Normalize project workflow overrides and reject structural/audit errors.

    Warnings remain saveable: they describe deliberate trade-offs such as a
    terminal completion state without a reopen path.
    """
    if not isinstance(raw, dict):
        raise ValueError("工作流配置必须是对象")
    unknown_types = sorted(set(raw) - set(ISSUE_TYPES))
    if unknown_types:
        raise ValueError(f"未知的问题类型：{'、'.join(unknown_types)}")
    normalized: dict[str, dict[str, list[str]]] = {}
    for issue_type, rules in raw.items():
        if not isinstance(rules, dict):
            raise ValueError(f"{issue_type} 的工作流必须是对象")
        clean: dict[str, list[str]] = {}
        for source, targets in rules.items():
            if not isinstance(source, str) or not isinstance(targets, list) or not all(
                isinstance(target, str) for target in targets
            ):
                raise ValueError(f"{issue_type} 的工作流格式无效")
            clean[source] = list(dict.fromkeys(targets))
        if clean:
            errors = [
                finding.message
                for finding in lint_flow(statuses, clean, issue_type)
                if finding.level == "error"
            ]
            if errors:
                raise ValueError(f"{issue_type} 工作流无效：{'；'.join(errors)}")
        normalized[issue_type] = clean
    return normalized


def lint_project(db: Session, project: Project) -> list[Finding]:
    statuses = workflow.project_statuses(db, project.id)
    pairs = [(s.name, s.category) for s in statuses]
    names = {s.name for s in statuses}
    settings = project.settings or {}
    out: list[Finding] = []
    used_by_any: set[str] = set()
    for issue_type in ISSUE_TYPES:
        rules = workflow.transition_rules(project, issue_type) or {}
        out.extend(lint_flow(pairs, rules, issue_type))
        used_by_any |= set(rules) | {t for ts in rules.values() for t in ts}

    profile = profiles.profile_of(project)
    definition = profile.definition or {}
    roles = {str(r["key"] if isinstance(r, dict) else r) for r in definition.get("roles") or []}
    for status, gate in (settings.get("transition_gates") or {}).items():
        if status not in names:
            out.append(Finding("error", "gates", f"门禁引用了不存在的状态「{status}」"))
        elif status not in used_by_any:
            out.append(Finding("warning", "gates", f"门禁状态「{status}」不在任何工作流中"))
        for item in (gate or {}).get("approvals") or []:
            if roles and item.get("role") not in roles:
                out.append(Finding("error", "gates",
                                   f"「{status}」的审批角色「{item.get('role')}」不在模板角色中"))
            if int(item.get("count") or 0) < 1:
                out.append(Finding("error", "gates", f"「{status}」的审批人数必须至少为 1"))
        # Fields scoped to some issue types must not silently no-op for others that
        # can enter this status (runtime already skips them; warn so config can be fixed).
        enterers = [
            issue_type for issue_type in ISSUE_TYPES
            if any(status in targets
                   for targets in (workflow.transition_rules(project, issue_type) or {}).values())
        ]
        for field in (gate or {}).get("required_fields") or []:
            blind = [itype for itype in enterers
                     if not profile.field_applies_to_type(str(field), itype)]
            if blind:
                out.append(Finding(
                    "warning", "gates",
                    f"「{status}」必填「{field}」对 {', '.join(blind)} 无表单字段，"
                    f"流转时会跳过该门禁",
                ))

    test = settings.get("test_status")
    if test:
        for issue_type in ISSUE_TYPES:
            rules = workflow.transition_rules(project, issue_type) or {}
            uses = test in rules or any(test in ts for ts in rules.values())
            if uses and workflow.verification_statuses(project, issue_type, statuses) is None:
                out.append(Finding("error", "verification",
                                   f"{issue_type} 的「{test}」无法流转到通过/失败状态"))

    for board in db.scalars(select(Board).where(Board.project_id == project.id)):
        mapped = {sid for column in board.columns or [] for sid in column.get("status_ids") or []}
        missing = [s.name for s in statuses if s.id not in mapped and s.name in used_by_any]
        if missing:
            out.append(Finding("error", f"board:{board.name}",
                               f"看板没有放置这些状态，相关问题在看板上看不到：{'、'.join(missing)}"))
    return out


def lint_definition(definition: dict[str, Any]) -> list[Finding]:
    pairs = [(str(s["name"]), str(s["category"])) for s in definition.get("statuses") or []]
    out: list[Finding] = []
    for issue_type in ISSUE_TYPES:
        rules = (definition.get("workflows") or {}).get(issue_type) or {}
        out.extend(lint_flow(pairs, rules, issue_type))
    return out
