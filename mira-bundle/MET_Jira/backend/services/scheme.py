"""Electrical requirement scheme confirmation: who may edit, lock, and review."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Issue, ProjectMember, User

LOGGER = logging.getLogger(__name__)

FIELDS = (
    "scheme_note",
    "electrical_scheme_ok",
    "process_scheme_ok",
    "software_scheme_ok",
)
VOTE_FIELDS = (
    "electrical_scheme_ok",
    "process_scheme_ok",
    "software_scheme_ok",
)
STATUS = "方案讨论"
RESET_STATUSES = frozenset({"待评估", "已驳回"})
DEV_STATUS = "研发中"


@dataclass(frozen=True)
class SchemeReview:
    changed: bool
    kind: str
    reason: str


def applies(issue: Issue) -> bool:
    from .profiles import form_has_field, profile_of

    return form_has_field(profile_of(issue.project), "scheme_note", issue.type)


def _admin(user: User, roles: set[str]) -> bool:
    return bool(user.is_admin or "admin" in roles)


def can_edit_field(user: User, issue: Issue, roles: set[str], field: str) -> bool:
    """Who may change the field when the status window and lock also allow it."""
    if field not in FIELDS:
        return False
    if _admin(user, roles):
        return True
    if field == "scheme_note":
        return "electrical" in roles or user.id in {issue.software_owner_id, issue.assignee_id}
    if field == "electrical_scheme_ok":
        return "electrical" in roles
    if field == "process_scheme_ok":
        return "process" in roles
    if field == "software_scheme_ok":
        return user.id == issue.software_owner_id and issue.software_owner_id is not None
    return False


def can_submit(user: User, issue: Issue, roles: set[str]) -> bool:
    return can_edit_field(user, issue, roles, "scheme_note")


def can_unlock(user: User, issue: Issue, roles: set[str]) -> bool:
    if _admin(user, roles):
        return True
    return (
        "electrical" in roles
        or "process" in roles
        or user.id in {issue.software_owner_id, issue.assignee_id}
    )


def can_participate(user: User, issue: Issue, roles: set[str]) -> bool:
    return can_unlock(user, issue, roles) or any(
        can_edit_field(user, issue, roles, field) for field in FIELDS
    )


def submitted(issue: Issue) -> bool:
    return bool(issue.scheme_locked or (issue.scheme_confirmed_note or "").strip())


def votes_satisfied(
    *,
    note: str | None,
    electrical: bool | None,
    process: bool | None,
    software: bool | None,
    involves: bool | None,
    owner: int | None,
) -> bool:
    if not (note or "").strip():
        return False
    if electrical is not True or process is not True:
        return False
    if involves is True:
        return owner is not None and software is True
    return True


def issue_votes_satisfied(issue: Issue) -> bool:
    return votes_satisfied(
        note=issue.scheme_note,
        electrical=issue.electrical_scheme_ok,
        process=issue.process_scheme_ok,
        software=issue.software_scheme_ok,
        involves=issue.involves_software,
        owner=issue.software_owner_id,
    )


def snapshot(issue: Issue) -> dict[str, Any]:
    return {
        "note": issue.scheme_note or "",
        "locked": bool(issue.scheme_locked),
        "confirmed": issue.scheme_confirmed_note or "",
        "electrical": issue.electrical_scheme_ok,
        "process": issue.process_scheme_ok,
        "software": issue.software_scheme_ok,
        "involves": issue.involves_software,
        "owner": issue.software_owner_id,
        "votes": issue_votes_satisfied(issue),
    }


def validate_edits(issue: Issue, actor: User, roles: set[str], changes: dict[str, Any]) -> None:
    from .issues import IssueError

    touched = [field for field in FIELDS if field in changes]
    if not touched:
        return
    if not applies(issue):
        raise IssueError("当前问题没有方案确认", 409)
    if issue.status is None or issue.status.name != STATUS:
        raise IssueError("方案确认只能在「方案讨论」中填写", 409, "scheme_status")
    for field in touched:
        if not can_edit_field(actor, issue, roles, field):
            raise IssueError("没有权限修改方案确认", 403)
        if field == "scheme_note" and issue.scheme_locked:
            if str(changes.get(field) or "") != (issue.scheme_note or ""):
                raise IssueError("方案说明已锁定，请先解锁", 409, "scheme_locked")
        if field in VOTE_FIELDS:
            if not submitted(issue):
                raise IssueError("请先发起方案确认", 409, "scheme_submit")
            if issue.scheme_locked and issue_votes_satisfied(issue):
                raise IssueError("方案已全部确认，请先解锁", 409, "scheme_locked")
            involves = changes.get("involves_software", issue.involves_software)
            if field == "software_scheme_ok" and involves is not True:
                raise IssueError("未涉及软件时不能确认软件方案", 409)


SIGNERS = (
    ("electrical_scheme_ok", "electrical_scheme_by_id"),
    ("process_scheme_ok", "process_scheme_by_id"),
    ("software_scheme_ok", "software_scheme_by_id"),
)


def _clear_votes(issue: Issue) -> None:
    issue.electrical_scheme_ok = None
    issue.process_scheme_ok = None
    issue.software_scheme_ok = None
    issue.electrical_scheme_by_id = None
    issue.process_scheme_by_id = None
    issue.software_scheme_by_id = None


def remember_signers(issue: Issue, actor: User, changed: list[str]) -> None:
    """The person who clicks 是 signs that confirmation. Any other value removes it."""
    for vote, signer in SIGNERS:
        if vote not in changed:
            continue
        if getattr(issue, vote) is True:
            setattr(issue, signer, actor.id)
        else:
            setattr(issue, signer, None)


def on_status(db: Session, issue: Issue, actor: User, new_name: str) -> None:
    from .issues import log

    if not applies(issue) or new_name not in RESET_STATUSES:
        return
    if (
        issue.electrical_scheme_ok is None
        and issue.process_scheme_ok is None
        and issue.software_scheme_ok is None
        and not issue.scheme_locked
        and not (issue.scheme_confirmed_note or "").strip()
    ):
        return
    log(
        db, issue, actor, "updated", field="scheme_review",
        old="已发起", new="已清空", formatted=True,
        meta={"auto": True, "reason": "status_reset"},
    )
    _clear_votes(issue)
    issue.scheme_locked = False
    issue.scheme_confirmed_note = ""


def compare_notes(old: str, new: str, title: str) -> SchemeReview:
    """Ask DeepSeek whether the scheme text changed in substance. Fail closed."""
    if old.strip() == new.strip():
        return SchemeReview(False, "unchanged", "方案说明没有变化")
    from .llm import LlmError, chat

    system = (
        "你判断一份需求方案说明的修改是否改变了技术方案或实现逻辑。"
        "只输出 JSON："
        '{"changed": true或false, "kind": "wording"|"scheme"|"logic", "reason": "一句中文"}。'
        "标点、错别字、同义改写、语序调整，技术含义不变时 changed 为 false，kind 为 wording。"
        "新增、删除或改变步骤、接口、参数、范围、责任或验收条件时 changed 为 true，"
        "kind 为 scheme 或 logic。"
    )
    user = f"需求标题：{title}\n\n上一版方案说明：\n{old}\n\n新方案说明：\n{new}"
    try:
        message = chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            max_tokens=400,
            json_mode=True,
            retries=0,
        )
        return _parse_review(str(message.get("content") or ""))
    except (LlmError, TypeError, ValueError) as error:
        LOGGER.warning("scheme review failed: %s", error)
        return SchemeReview(True, "failed", "自动比对未完成，请重新确认")


def _parse_review(content: str) -> SchemeReview:
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return SchemeReview(True, "failed", "自动比对未完成，请重新确认")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return SchemeReview(True, "failed", "自动比对未完成，请重新确认")
    if not isinstance(data, dict):
        return SchemeReview(True, "failed", "自动比对未完成，请重新确认")
    kind = str(data.get("kind") or "")
    if kind not in {"wording", "scheme", "logic"}:
        kind = "scheme" if data.get("changed") else "wording"
    changed = kind in {"scheme", "logic"}
    reason = str(data.get("reason") or "").strip()
    if not reason:
        reason = "方案有变更，需要重新确认" if changed else "仅为措辞调整，确认仍然有效"
    return SchemeReview(changed, kind, reason[:200])


def recipient_ids(db: Session, issue: Issue) -> set[int]:
    ids = {issue.reporter_id, issue.assignee_id}
    if issue.involves_software is True and issue.software_owner_id:
        ids.add(issue.software_owner_id)
    ids.update(db.scalars(select(ProjectMember.user_id).where(
        ProjectMember.project_id == issue.project_id,
        ProjectMember.role.in_(("electrical", "process")),
    )))
    return {item for item in ids if item}


def apply_action(
    db: Session, issue: Issue, actor: User, roles: set[str], action: str,
) -> str | None:
    """Run unlock or submit. Returns a review kind when submit compared text."""
    from .issues import IssueError, log, notify

    if not applies(issue):
        raise IssueError("当前问题没有方案确认", 409)
    from ..models import Status

    status = db.get(Status, issue.status_id)
    if status is None or status.name != STATUS:
        raise IssueError("方案确认只能在「方案讨论」中操作", 409, "scheme_status")
    if action == "unlock":
        if not can_unlock(actor, issue, roles):
            raise IssueError("没有权限解锁方案", 403)
        if not issue.scheme_locked:
            raise IssueError("方案说明未锁定", 409)
        issue.scheme_locked = False
        log(db, issue, actor, "updated", field="scheme_locked", old=True, new=False)
        return None
    if action != "submit":
        raise IssueError("未知的方案操作", 400)
    if not can_submit(actor, issue, roles):
        raise IssueError("没有权限发起方案确认", 403)
    note = (issue.scheme_note or "").strip()
    if not note:
        raise IssueError("请先填写方案说明", 409, "scheme_submit", fields=["scheme_note"])
    if issue.involves_software is True and issue.software_owner_id is None:
        raise IssueError(
            "请先指定软件负责人", 409, "scheme_submit", fields=["software_owner_id"],
        )
    previous = (issue.scheme_confirmed_note or "").strip()
    review: SchemeReview | None = None
    if previous and previous != note:
        review = compare_notes(previous, note, issue.title)
        log(
            db, issue, actor, "updated", field="scheme_review",
            old=None, new=review.reason, formatted=True,
            meta={"kind": review.kind, "changed": review.changed},
        )
        if review.changed:
            _clear_votes(issue)
            notify(
                db, recipient_ids(db, issue), actor, issue, "scheme_changed",
                f"{issue.key} 方案已变更（{review.reason}），原确认已重置，请三方重新确认",
            )
    elif not previous:
        notify(
            db, recipient_ids(db, issue), actor, issue, "scheme_review_requested",
            f"{issue.key} 已发起方案确认，请电气、工艺及相关软件负责人确认方案",
        )
    if issue.scheme_note != note:
        issue.scheme_note = note
    issue.scheme_confirmed_note = note
    was_locked = issue.scheme_locked
    issue.scheme_locked = True
    if not was_locked:
        log(db, issue, actor, "updated", field="scheme_locked", old=False, new=True)
    return review.kind if review else None


def notify_after(
    db: Session,
    issue: Issue,
    actor: User,
    before: dict[str, Any],
    *,
    review_kind: str | None,
    status_name: str | None = None,
) -> None:
    from .issues import notify

    if not applies(issue) or status_name in RESET_STATUSES:
        return
    after_votes = issue_votes_satisfied(issue)
    before_votes = bool(before["votes"])
    involves_now = issue.involves_software is True
    involves_was = before["involves"] is True
    owner_now = issue.software_owner_id
    submitted_now = submitted(issue)
    if (
        involves_now
        and owner_now
        and submitted_now
        and issue.software_scheme_ok is not True
        and (not involves_was or before["owner"] != owner_now)
    ):
        notify(
            db, [owner_now], actor, issue, "scheme_review_requested",
            f"{issue.key} 已发起方案确认，请确认软件方案",
        )
    if before_votes and not after_votes:
        turned_on_software = involves_now and not involves_was
        review_reset = review_kind in {"scheme", "logic", "failed"}
        if not turned_on_software and not review_reset:
            notify(
                db, recipient_ids(db, issue), actor, issue, "scheme_withdrawn",
                f"{issue.key} 的方案确认已撤回，进入「研发中」前需要重新确认",
            )
    if (
        not before_votes
        and after_votes
        and issue.scheme_locked
        and review_kind not in {"scheme", "logic", "failed"}
    ):
        notify(
            db, recipient_ids(db, issue), actor, issue, "scheme_confirmed",
            f"{issue.key} 三方方案已确认完毕，可以流转到「研发中」",
        )
