"""Which org units count as a project's software department (candidates for 软件负责人)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OrgUnit, Project, User

DEFAULT_UNIT_RE = re.compile(r"^(software|软件)", re.IGNORECASE)


def descendant_ids(units: Iterable[OrgUnit], root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = defaultdict(list)
    for unit in units:
        children[unit.parent_id].append(unit.id)
    result, stack = set(), [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def software_roots(db: Session, project: Project) -> list[OrgUnit]:
    """Configured units (`settings.software_units`, by path), else top-most units named Software.

    Paths rather than ids are stored because ids differ between databases synced from the same AD.
    """
    units = list(db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False))))
    configured = (project.settings or {}).get("software_units") or []
    if configured:
        wanted = set(configured)
        return [u for u in units if u.path in wanted]
    matched = {u.id for u in units if DEFAULT_UNIT_RE.match(u.name or "")
               or DEFAULT_UNIT_RE.match(u.display_name or "")}
    return sorted((u for u in units if u.id in matched and u.parent_id not in matched),
                  key=lambda u: u.path)


def software_unit_ids(db: Session, project: Project) -> set[int]:
    roots = software_roots(db, project)
    if not roots:
        return set()
    units = list(db.scalars(select(OrgUnit).where(OrgUnit.removed.is_(False))))
    ids: set[int] = set()
    for root in roots:
        ids |= descendant_ids(units, root.id)
    return ids


def is_software_member(db: Session, project: Project, user: User) -> bool:
    """True when the user sits in the software department, or no department can be resolved."""
    ids = software_unit_ids(db, project)
    return not ids or user.org_unit_id in ids
