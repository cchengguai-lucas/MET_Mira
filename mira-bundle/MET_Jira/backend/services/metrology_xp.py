"""Award Metrology Web XP when a METROLOGY issue is accepted (Done).

XP is granted only on transition into a completed status (done category,
not rejected). Create / reject / reopen do not award.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

from ..config import get_settings
from ..models import Issue, Project, Status, User
from . import workflow

LOGGER = logging.getLogger(__name__)

# Status names in category="done" that represent rejection/cancellation rather
# than accepted completion (workflow.is_rejected only checks Chinese "已驳回",
# while English AI-template projects such as METROLOGY use "Cancelled").
_REJECTED_STATUS_NAMES: frozenset[str] = frozenset(
    {"已驳回", "已取消", "作废", "cancelled", "canceled", "rejected"}
)


def is_accepted_done(status: Status | None) -> bool:
    """True only for accepted completion (done category, excluding reject/cancel)."""
    if not workflow.is_completed(status):
        return False
    name = (getattr(status, "name", None) or "").strip().lower()
    return name not in _REJECTED_STATUS_NAMES


def notify_metrology_requirement(
    *,
    project: Project,
    issue: Issue,
    actor: User,
    reporter: User | None = None,
    previous_status: Status | None = None,
) -> None:
    """Fire-and-forget XP award when a METROLOGY issue becomes Done/accepted."""
    try:
        if (project.key or "").upper() != "METROLOGY":
            return
        if getattr(issue, "import_batch_id", None):
            return
        # Only accepted completion — 已驳回 / Cancelled-style rejects skip.
        if not is_accepted_done(issue.status):
            return
        if previous_status is not None and is_accepted_done(previous_status):
            return
        settings = get_settings()
        url = (getattr(settings, "metrology_xp_url", None) or "").strip()
        token = (getattr(settings, "metrology_xp_token", None) or "").strip()
        if not url or not token:
            return
        who = reporter or actor
        username = (getattr(who, "username", None) or "").strip()
        if not username:
            return
        payload: dict[str, Any] = {
            "username": username,
            "issue_key": str(issue.key or "").strip(),
            "title": str(issue.title or "").strip()[:500],
        }
        if not payload["issue_key"]:
            return

        def _post() -> None:
            try:
                resp = httpx.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=2.5,
                )
                if resp.status_code >= 400:
                    LOGGER.warning(
                        "metrology xp notify returned %s for %s / %s: %s",
                        resp.status_code,
                        payload.get("issue_key"),
                        username,
                        resp.text[:200],
                    )
            except Exception:  # noqa: BLE001
                LOGGER.warning(
                    "metrology xp notify failed for %s / %s",
                    payload.get("issue_key"),
                    username,
                    exc_info=True,
                )

        threading.Thread(target=_post, name="metrology-xp", daemon=True).start()
    except Exception:  # noqa: BLE001
        LOGGER.warning("metrology xp notify setup failed", exc_info=True)
