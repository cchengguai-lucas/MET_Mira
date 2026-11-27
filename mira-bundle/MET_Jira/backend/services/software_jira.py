"""Read-only client for the software team's Jira relay.

A stored snapshot is a hint. It does not by itself change the issue status.
When its status is exactly 待上机验证, Mira marks software ready and watches
directory users matched from the assignee and tester names. A Chinese name
matches the directory Chinese name synced from LDAP. The scheduler may then
move an issue that is waiting in 研发中 into testing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Issue, Project, Status, User, Watcher

_NOT_CONFIGURED = "尚未连接软件 Jira"
_UNREACHABLE = "软件 Jira 暂时无法连接"
_REFUSED = "软件 Jira 拒绝了访问"
_RELAY_PATH = "/api/jira/relay"
_TIMEOUT = 8.0
REFRESH_LIMIT = 20
REFRESH_INTERVAL = timedelta(minutes=5)
WAIT_KEY = "_software_test_wait"
DUE_CONFLICT_KEY = "_software_due_conflict"
SOFTWARE_DONE_STATUS = "待上机验证"
WAIT_PENDING = "软件 Jira 尚未到「待上机验证」。到达后将自动进入测试中，并提醒测试人。"
WAIT_PENDING_FIELDS = (
    "软件 Jira 尚未到「待上机验证」。到达后将自动进入测试中，并提醒测试人。"
    "自动进入前还需填写：{fields}。"
)
WAIT_READY_FIELDS = "软件 Jira 已是「待上机验证」。补齐{fields}后将自动进入测试中，并提醒测试人。"


class SoftwareJiraError(Exception):
    """The bridge payload cannot be stored."""


class SoftwareJiraNotConfigured(SoftwareJiraError):
    """METJIRA_SOFTWARE_JIRA_URL or METJIRA_SOFTWARE_JIRA_TOKEN is empty."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _due_date(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    day = text[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    return day


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_software_jira(payload: Any) -> dict[str, Any]:
    """Normalize relay JSON. Names stay display strings."""
    if not isinstance(payload, dict):
        raise SoftwareJiraError("软件 Jira 响应无效")
    due = payload.get("due_date")
    if due is None:
        due = payload.get("dueDate")
    return {
        "assignee": _text(payload.get("assignee")),
        "tester": _text(payload.get("tester")),
        "status": _text(payload.get("status")),
        "due_date": _due_date(due),
        "fetched_at": None,
        "error": None,
    }


def relay_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith(_RELAY_PATH):
        root = root[: -len(_RELAY_PATH)]
    return f"{root}{_RELAY_PATH}"


class SoftwareJiraClient(Protocol):
    def fetch(self, key: str) -> dict[str, Any]:
        """Return assignee, tester, status and due_date for one software issue."""


class UnavailableSoftwareJira:
    """Used when the relay URL or token is empty. fetch never writes a snapshot."""

    def fetch(self, key: str) -> dict[str, Any]:
        raise SoftwareJiraNotConfigured(_NOT_CONFIGURED)


class HttpSoftwareJira:
    """GET {base}/api/jira/relay?key= with header X-Api-Key."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def fetch(self, key: str) -> dict[str, Any]:
        issue_key = key.strip()
        if not issue_key:
            raise SoftwareJiraError("缺少软件 Jira 单号")
        if not self.base_url or not self.token:
            raise SoftwareJiraNotConfigured(_NOT_CONFIGURED)
        try:
            response = httpx.get(
                relay_url(self.base_url),
                params={"key": issue_key},
                headers={"X-Api-Key": self.token},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise SoftwareJiraError(_UNREACHABLE) from error
        if response.status_code == 404:
            raise SoftwareJiraError("软件 Jira 没有这张单")
        if response.status_code in {401, 403}:
            raise SoftwareJiraError(_REFUSED)
        try:
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise SoftwareJiraError(_UNREACHABLE) from error
        except ValueError as error:
            raise SoftwareJiraError("软件 Jira 响应无效") from error
        snapshot = parse_software_jira(payload)
        snapshot["fetched_at"] = _now()
        return snapshot


def client_from_settings() -> SoftwareJiraClient:
    settings = get_settings()
    url = (settings.software_jira_url or "").strip()
    token = (settings.software_jira_token or "").strip()
    if not url or not token:
        return UnavailableSoftwareJira()
    return HttpSoftwareJira(url, token)


def apply_snapshot(issue: Issue, payload: Any) -> dict[str, Any]:
    """Store a parsed snapshot only. Status and software_ready stay as they are."""
    snapshot = parse_software_jira(payload)
    issue.software_jira_snapshot = snapshot
    return snapshot


def sync_snapshot(issue: Issue, *, required: bool = False) -> dict[str, Any] | None:
    """Pull the relay for the current key. An empty key clears the snapshot."""
    key = (issue.software_jira_key or "").strip()
    if not key:
        issue.software_jira_snapshot = None
        return None
    try:
        snapshot = client_from_settings().fetch(key)
    except SoftwareJiraNotConfigured:
        if required:
            raise
        return issue.software_jira_snapshot
    except SoftwareJiraError as error:
        snapshot = {
            "assignee": None,
            "tester": None,
            "status": None,
            "due_date": None,
            "fetched_at": _now(),
            "error": str(error),
        }
    issue.software_jira_snapshot = snapshot
    from sqlalchemy.orm import object_session

    session = object_session(issue)
    if session is not None and not snapshot.get("error"):
        absorb_snapshot(session, issue)
    return snapshot


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _error_snapshot(message: str, now: datetime) -> dict[str, Any]:
    return {
        "assignee": None,
        "tester": None,
        "status": None,
        "due_date": None,
        "fetched_at": now.isoformat(timespec="seconds"),
        "error": message,
    }


def snapshot_is_complete(snapshot: dict[str, Any] | None) -> bool:
    """True only when the other Jira status is exactly 待上机验证."""
    status = str((snapshot or {}).get("status") or "").strip().casefold()
    return status == SOFTWARE_DONE_STATUS.casefold()


def match_directory_user(db: Session, name: str | None) -> User | None:
    """Match a Jira display string to one enabled directory user.

    Username wins, including case-insensitive matches. A display name or the
    Chinese directory name is used only when exactly one enabled person has it.
    """
    text = str(name or "").strip()
    if not text:
        return None
    by_username = list(db.scalars(
        select(User).where(User.enabled.is_(True), User.username == text).limit(2)
    ))
    if len(by_username) == 1:
        return by_username[0]
    if not by_username and text.casefold() != text:
        folded = list(db.scalars(
            select(User).where(User.enabled.is_(True), User.username.ilike(text)).limit(2)
        ))
        if len(folded) == 1:
            return folded[0]
    for column in (User.display_name, User.native_name):
        named = list(db.scalars(
            select(User).where(User.enabled.is_(True), column == text).limit(2)
        ))
        if len(named) == 1:
            return named[0]
    return None


def absorb_snapshot(db: Session, issue: Issue) -> bool:
    """Mark software ready and watch directory people named on the snapshot."""
    snapshot = issue.software_jira_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("error"):
        return False
    from .issues import log, notify, watch

    changed = False
    for field in ("assignee", "tester"):
        person = match_directory_user(db, snapshot.get(field))
        if person is None:
            continue
        already = db.scalar(
            select(Watcher.id).where(
                Watcher.issue_id == issue.id, Watcher.user_id == person.id,
            )
        )
        if already is None:
            watch(db, issue.id, person.id)
            changed = True
    if snapshot_is_complete(snapshot) and issue.involves_software is True:
        from ..db import utcnow
        from . import profiles

        project = issue.project
        ready_field = (
            project is not None
            and profiles.form_has_field(
                profiles.profile_of(project), "software_ready", issue.type,
            )
        )
        if ready_field and issue.software_ready is not True:
            log(
                db, issue, None, "updated", field="software_ready",
                old=issue.software_ready, new=True,
                meta={"auto": True, "reason": "software_jira_ready"},
            )
            issue.software_ready = True
            issue.lock_version += 1
            issue.updated_at = utcnow()
            if issue.assignee_id:
                notify(
                    db, [issue.assignee_id], None, issue, "software_ready",
                    f"{issue.key} 的软件 Jira 已到「待上机验证」，软件已标记为准备好",
                )
            changed = True
    if remind_due_conflict(db, issue):
        changed = True
    return changed


def _day(value: Any) -> date | None:
    text = str(value or "")[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def due_conflict(issue: Issue) -> tuple[date, date] | None:
    """Mira and software due dates when software delivery is later."""
    if issue.involves_software is not True:
        return None
    status = issue.status
    if status is not None and status.category == "done":
        return None
    snapshot = issue.software_jira_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("error"):
        return None
    software = _day(snapshot.get("due_date"))
    mira = issue.due_date
    if software is None or mira is None or software <= mira:
        return None
    return mira, software


def _conflict_token(mira: date, software: date, recipients: list[int]) -> str:
    people = ",".join(str(uid) for uid in sorted(set(recipients)))
    return f"{mira.isoformat()}|{software.isoformat()}|{people}"


def _set_conflict_mark(issue: Issue, token: str | None) -> bool:
    extra = dict(issue.extra or {})
    if token is None:
        if DUE_CONFLICT_KEY not in extra:
            return False
        extra.pop(DUE_CONFLICT_KEY, None)
    elif extra.get(DUE_CONFLICT_KEY) == token:
        return False
    else:
        extra[DUE_CONFLICT_KEY] = token
    issue.extra = extra
    return True


def _conflict_recipients(db: Session, issue: Issue) -> list[int]:
    recipients: list[int] = []
    if issue.assignee_id:
        recipients.append(issue.assignee_id)
    elif issue.reporter_id:
        recipients.append(issue.reporter_id)
    raw = issue.software_jira_snapshot
    snapshot = raw if isinstance(raw, dict) else {}
    person = match_directory_user(db, snapshot.get("assignee"))
    if person is not None:
        recipients.append(person.id)
    if issue.software_owner_id:
        recipients.append(issue.software_owner_id)
    return recipients


def remind_due_conflict(db: Session, issue: Issue) -> bool:
    """Email both sides once when the two due dates disagree."""
    snapshot = issue.software_jira_snapshot
    if isinstance(snapshot, dict) and snapshot.get("error"):
        return False
    pair = due_conflict(issue)
    if pair is None:
        return _set_conflict_mark(issue, None)
    mira, software = pair
    recipients = _conflict_recipients(db, issue)
    if not _set_conflict_mark(issue, _conflict_token(mira, software, recipients)):
        return False
    from .issues import notify

    other_key = (issue.software_jira_key or "").strip()
    other = f"软件 Jira {other_key}" if other_key else "软件 Jira"
    notify(
        db, recipients, None, issue, "software_due_conflict",
        (
            f"{issue.key} 的截止日期是 {mira.isoformat()}，"
            f"{other} 的截止日期是 {software.isoformat()}。"
            "软件交付晚于 Mira 最终日期，请双方确认时间，避免延误。"
        ),
    )
    return True


def mark_software_wait(issue: Issue, waiting: bool) -> None:
    extra = dict(issue.extra or {})
    if waiting:
        if extra.get(WAIT_KEY) is True:
            return
        extra[WAIT_KEY] = True
    elif WAIT_KEY not in extra:
        return
    else:
        extra.pop(WAIT_KEY, None)
    issue.extra = extra


def wait_message(complete: bool, gaps: list[str], issue: Issue) -> str:
    if not gaps:
        return WAIT_PENDING
    from .approvals import field_label

    labels = "、".join(field_label(issue, field) for field in gaps)
    template = WAIT_READY_FIELDS if complete else WAIT_PENDING_FIELDS
    return template.format(fields=labels)


def software_test_wait_text(issue: Issue) -> str | None:
    """Notice while 研发中 is waiting for software, or for the test fields after it."""
    if (issue.extra or {}).get(WAIT_KEY) is not True:
        return None
    if issue.involves_software is not True or issue.status is None:
        return None
    if issue.status.name != "研发中":
        return None
    from .approvals import test_entry_gaps

    complete = snapshot_is_complete(issue.software_jira_snapshot)
    gaps = test_entry_gaps(issue, ready=False)
    if complete and not gaps:
        return None
    return wait_message(complete, gaps, issue)


def _move_to_test(db: Session, issue: Issue) -> bool:
    from ..db import utcnow
    from . import profiles, sla, workflow
    from .issues import log, notify

    project = issue.project
    statuses = workflow.project_statuses(db, project.id)
    test_name = (project.settings or {}).get("test_status", "测试中")
    target = workflow.status_by_name(statuses, test_name)
    if target is None:
        return False
    flow = workflow.flow_statuses(project, issue.type, statuses)
    if all(status.id != target.id for status in flow):
        return False
    old = issue.status
    old_id = issue.status_id
    issue.status_id = target.id
    issue.status = target
    result = workflow.test_result_after_move(project, old, target, issue.test_passed)
    if result != issue.test_passed:
        log(
            db, issue, None, "updated", field="test_passed", old=issue.test_passed, new=result,
            meta={"auto": True, "reason": "software_jira_done"},
        )
        issue.test_passed = result
    profile = profiles.profile_of(project)
    if (profiles.form_has_field(profile, "software_ready", issue.type)
            and issue.software_ready is not True):
        log(
            db, issue, None, "updated", field="software_ready",
            old=issue.software_ready, new=True,
            meta={"auto": True, "reason": "software_jira_done"},
        )
        issue.software_ready = True
    mark_software_wait(issue, False)
    log(
        db, issue, None, "transition", field="status_id", old=old_id, new=target.id,
        meta={"auto": True, "reason": "software_jira_done"},
    )
    issue.resolved_at = None
    issue.lock_version += 1
    issue.updated_at = utcnow()
    sla.sync(issue, project, target)
    if issue.tester_id:
        notify(
            db, [issue.tester_id], None, issue, "to_test",
            f"{issue.key}「{issue.title}」已进入测试，请安排验证",
        )
    return True


def advance_waiting(db: Session) -> int:
    """Move flagged 研发中 issues into testing once software Jira is finished."""
    from .approvals import test_entry_gaps

    rows = db.scalars(
        select(Issue)
        .join(Status, Issue.status_id == Status.id)
        .join(Project, Issue.project_id == Project.id)
        .where(Status.name == "研发中", Project.archived.is_(False))
    ).all()
    moved = 0
    for issue in rows:
        if (issue.extra or {}).get(WAIT_KEY) is not True:
            continue
        if issue.involves_software is not True:
            mark_software_wait(issue, False)
            continue
        if not snapshot_is_complete(issue.software_jira_snapshot):
            continue
        if test_entry_gaps(issue, ready=False):
            continue
        if _move_to_test(db, issue):
            moved += 1
    return moved


def refresh_open_snapshots(
    db: Session, *, now: datetime | None = None, limit: int = REFRESH_LIMIT,
) -> dict[str, Any]:
    """Refresh distinct software-Jira keys on issues that are not done.

    One relay call updates every open issue that shares the key. A connection
    or auth failure stops the round and keeps the snapshots already stored.
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if isinstance(client_from_settings(), UnavailableSoftwareJira):
        moved = advance_waiting(db)
        if db.dirty:
            db.commit()
        return {"keys": 0, "updated": 0, "stopped": "not_configured", "moved": moved}
    rows = db.scalars(
        select(Issue)
        .join(Status, Issue.status_id == Status.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.software_jira_key != "",
            Status.category != "done",
            Project.archived.is_(False),
        )
    ).all()
    groups: dict[str, list[Issue]] = {}
    for issue in rows:
        key = (issue.software_jira_key or "").strip()
        if key:
            groups.setdefault(key, []).append(issue)
    cutoff = moment - REFRESH_INTERVAL

    def oldest(items: list[Issue]) -> datetime:
        stamps = [
            _stamp((item.software_jira_snapshot or {}).get("fetched_at")) for item in items
        ]
        known = [stamp for stamp in stamps if stamp is not None]
        if len(known) != len(stamps):
            return datetime.min.replace(tzinfo=timezone.utc)
        return min(known)

    due = sorted(
        (oldest(items), key) for key, items in groups.items() if oldest(items) <= cutoff
    )
    client = client_from_settings()
    updated = 0
    stopped = None
    for _, key in due[:limit]:
        try:
            snapshot = client.fetch(key)
        except SoftwareJiraNotConfigured:
            stopped = "not_configured"
            break
        except SoftwareJiraError as error:
            message = str(error)
            if message in {_UNREACHABLE, _REFUSED}:
                stopped = message
                break
            snapshot = _error_snapshot(message, moment)
        for issue in groups[key]:
            issue.software_jira_snapshot = dict(snapshot)
            if not snapshot.get("error"):
                absorb_snapshot(db, issue)
            updated += 1
    moved = advance_waiting(db)
    if updated or db.dirty:
        db.commit()
    return {
        "keys": min(len(due), limit),
        "updated": updated,
        "stopped": stopped,
        "moved": moved,
    }
