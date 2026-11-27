"""Read-only workflow audit of every template version and project in the configured database.

Usage (staging): METJIRA_DEPLOY_ENV=staging python3 scripts/ops/workflow_audit.py
Exit code 1 when any error is found.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from backend.db import session_factory  # noqa: E402
from backend.models import DepartmentTemplate, DepartmentTemplateVersion, Project  # noqa: E402
from backend.services.workflow_audit import Finding, lint_definition, lint_project  # noqa: E402

MARK = {"error": "✗", "warning": "!", "info": "·"}


def report(title: str, findings: list[Finding]) -> int:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f in findings:
        grouped[(f.level, f.message)].append(f.scope)
    print(f"\n{title}")
    if not grouped:
        print("  ✓ 没有问题")
    order = {"error": 0, "warning": 1, "info": 2}
    for (level, message), scopes in sorted(grouped.items(), key=lambda kv: order[kv[0][0]]):
        print(f"  {MARK[level]} [{'/'.join(scopes)}] {message}")
    return sum(1 for f in findings if f.level == "error")


def main() -> int:
    errors = 0
    with session_factory()() as db:
        versions = db.scalars(
            select(DepartmentTemplateVersion)
            .join(DepartmentTemplate,
                  DepartmentTemplate.id == DepartmentTemplateVersion.template_id)
            .where(DepartmentTemplateVersion.status.in_(("published", "draft")))
            .order_by(DepartmentTemplate.key, DepartmentTemplateVersion.version)
        )
        for version in versions:
            template = db.get(DepartmentTemplate, version.template_id)
            current = " (当前)" if template and template.current_version_id == version.id else ""
            errors += report(f"模板 {template.key if template else '?'} v{version.version} "
                             f"[{version.status}]{current}", lint_definition(version.definition))
        for project in db.scalars(select(Project).order_by(Project.key)):
            if project.archived:
                continue
            errors += report(f"项目 {project.key} {project.name}", lint_project(db, project))
        db.rollback()
    print(f"\n共 {errors} 个错误")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
