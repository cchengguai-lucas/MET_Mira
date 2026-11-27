from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import require_admin
from ..models import (
    DepartmentTemplate,
    DepartmentTemplateAudit,
    DepartmentTemplateVersion,
    OrgUnit,
    OrgUnitTemplateRecommendation,
    Project,
    User,
)
from ..services import profiles, templates

router = APIRouter(prefix="/api/admin/templates", tags=["templates"])


def version_out(row: DepartmentTemplateVersion, *, definition: bool = True) -> dict:
    return {
        "id": row.id,
        "template_id": row.template_id,
        "version": row.version,
        "status": row.status,
        "revision": row.revision,
        "definition": row.definition if definition else None,
        "created_from_version_id": row.created_from_version_id,
        "created_at": row.created_at,
        "published_at": row.published_at,
        "validation_errors": templates.definition_errors(dict(row.definition)),
    }


def record_audit(db: Session, template: DepartmentTemplate, actor: User, action: str,
                 version: DepartmentTemplateVersion | None = None, **detail) -> None:
    db.add(DepartmentTemplateAudit(
        template_id=template.id, version_id=version.id if version else None,
        actor_id=actor.id, action=action, detail=detail,
    ))


def audits_out(db: Session, template_id: int) -> list[dict]:
    rows = db.execute(
        select(DepartmentTemplateAudit, User.display_name)
        .outerjoin(User, User.id == DepartmentTemplateAudit.actor_id)
        .where(DepartmentTemplateAudit.template_id == template_id)
        .order_by(DepartmentTemplateAudit.created_at.desc(), DepartmentTemplateAudit.id.desc())
        .limit(100)
    )
    return [{
        "id": row.id, "action": row.action, "version_id": row.version_id,
        "actor_id": row.actor_id, "actor_name": actor_name, "detail": row.detail or {},
        "created_at": row.created_at,
    } for row, actor_name in rows]


def template_out(row: DepartmentTemplate, *, versions: bool = False) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "label": row.label,
        "description": row.description,
        "enabled": row.enabled,
        "is_system": row.is_system,
        "current_version_id": row.current_version_id,
        "current_version": version_out(row.current_version) if row.current_version else None,
        **({"versions": [version_out(v) for v in row.versions]}
           if versions else {}),
    }


def get_template(db: Session, template_id: int) -> DepartmentTemplate:
    row = db.scalar(
        select(DepartmentTemplate)
        .options(selectinload(DepartmentTemplate.versions),
                 selectinload(DepartmentTemplate.current_version))
        .where(DepartmentTemplate.id == template_id)
    )
    if row is None:
        raise HTTPException(404, "模板不存在")
    return row


@router.get("/packs")
def list_field_packs(_admin: User = Depends(require_admin)) -> dict:
    """Reusable field/section packs templates may include via ``include_packs``."""
    packs = []
    for key, pack in templates.FIELD_PACKS.items():
        packs.append({
            "key": key,
            "roles": [
                r.get("key") if isinstance(r, dict) else r
                for r in (pack.get("roles") or [])
            ],
            "sections": [s.get("key") for s in (pack.get("sections") or [])],
            "fields": [f.get("key") for f in (pack.get("fields") or [])],
        })
    return {"packs": packs}


@router.get("")
def list_templates(_admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(DepartmentTemplate)
        .options(selectinload(DepartmentTemplate.current_version),
                 selectinload(DepartmentTemplate.versions))
        .order_by(DepartmentTemplate.label)
    )
    rows = list(rows)
    usage: dict[int, int] = dict(db.execute(
        select(DepartmentTemplateVersion.template_id, func.count(Project.id))
        .join(Project, Project.template_version_id == DepartmentTemplateVersion.id)
        .group_by(DepartmentTemplateVersion.template_id)
    ).all())
    by_key = {row.key: row.id for row in rows}
    unpinned = select(Project.settings).where(Project.template_version_id.is_(None))
    for settings in db.scalars(unpinned):
        key = (settings or {}).get("profile") or profiles.DEFAULT_PROFILE
        if key in by_key:
            usage[by_key[key]] = usage.get(by_key[key], 0) + 1
    return {"templates": [{
        **template_out(row),
        "draft_version": next((v.version for v in row.versions if v.status == "draft"), None),
        "project_count": usage.get(row.id, 0),
    } for row in rows]}


@router.post("")
def create_template(
    body: dict = Body(...), admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    key = str(body.get("key") or "").strip().lower()
    label = str(body.get("label") or "").strip()
    if not key or not label:
        raise HTTPException(400, "模板 key 和名称不能为空")
    if db.scalar(select(DepartmentTemplate.id).where(DepartmentTemplate.key == key)):
        raise HTTPException(409, "模板 key 已存在")
    source_id = body.get("source_version_id")
    source = db.get(DepartmentTemplateVersion, int(source_id)) if source_id else None
    if source is not None:
        definition = deepcopy(source.definition)
        definition["key"] = key
        definition["labels"] = {**(definition.get("labels") or {}), "zh": label}
    else:
        definition = deepcopy(profiles.builtin_definition(profiles.ELECTRICAL))
        definition["key"] = key
        definition["labels"] = {"zh": label, "en": str(body.get("label_en") or label)}
    try:
        definition = templates.validate_definition(definition)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    now = utcnow()
    row = DepartmentTemplate(
        key=key, label=label, description=str(body.get("description") or ""),
        enabled=True, is_system=False, created_at=now, updated_at=now,
    )
    db.add(row)
    db.flush()
    version = DepartmentTemplateVersion(
        template_id=row.id, version=1, status="draft", definition=definition,
        created_from_version_id=source.id if source else None, created_by_id=admin.id,
    )
    db.add(version)
    db.flush()
    if source is not None:
        record_audit(db, row, admin, "copy", version, source_template_id=source.template_id,
                     source_version_id=source.id, source_version=source.version)
    else:
        record_audit(db, row, admin, "create", version)
    db.commit()
    return template_detail(db, row.id)


def template_detail(db: Session, template_id: int) -> dict:
    return {**template_out(get_template(db, template_id), versions=True),
            "audits": audits_out(db, template_id)}


@router.get("/{template_id}")
def detail(template_id: int, _admin: User = Depends(require_admin),
           db: Session = Depends(get_db)) -> dict:
    return template_detail(db, template_id)


@router.put("/{template_id}")
def update_template(
    template_id: int, body: dict = Body(...), admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = get_template(db, template_id)
    changed = {}
    for field in ("label", "description"):
        if field in body:
            value = str(body[field]).strip()
            if value != getattr(row, field):
                changed[field] = {"from": getattr(row, field), "to": value}
            setattr(row, field, value)
    if "enabled" in body:
        enabled = bool(body["enabled"])
        if enabled != row.enabled:
            record_audit(db, row, admin, "enable" if enabled else "disable")
        row.enabled = enabled
    if changed:
        record_audit(db, row, admin, "update", changes=changed)
    row.updated_at = utcnow()
    db.commit()
    return template_detail(db, row.id)


@router.post("/{template_id}/versions")
def create_draft(
    template_id: int, body: dict = Body(default={}), admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    row = get_template(db, template_id)
    existing = next((v for v in row.versions if v.status == "draft"), None)
    if existing:
        return version_out(existing)
    source = row.current_version
    if body.get("source_version_id"):
        source = db.get(DepartmentTemplateVersion, int(body["source_version_id"]))
    if source is None or source.template_id != row.id:
        raise HTTPException(400, "没有可复制的模板版本")
    draft = DepartmentTemplateVersion(
        template_id=row.id, version=templates.next_version(db, row.id), status="draft",
        definition=deepcopy(source.definition), created_from_version_id=source.id,
        created_by_id=admin.id,
    )
    db.add(draft)
    db.flush()
    record_audit(db, row, admin, "draft", draft, source_version_id=source.id,
                 source_version=source.version)
    db.commit()
    db.refresh(draft)
    return version_out(draft)


@router.put("/{template_id}/versions/{version_id}")
def save_draft(
    template_id: int, version_id: int, body: dict = Body(...),
    _admin: User = Depends(require_admin), db: Session = Depends(get_db),
) -> dict:
    row = db.get(DepartmentTemplateVersion, version_id)
    if row is None or row.template_id != template_id:
        raise HTTPException(404, "模板版本不存在")
    if row.status != "draft":
        raise HTTPException(409, "已发布版本不可修改")
    expected = int(body.get("revision") or 0)
    if expected != row.revision:
        raise HTTPException(409, "模板草稿已被其他管理员更新，请刷新后重试")
    definition = body.get("definition")
    if not isinstance(definition, dict):
        raise HTTPException(400, "模板 definition 必须是对象")
    # Drafts are intentionally allowed to be incomplete so step-by-step editing can autosave.
    # The exact same definition is strongly validated by the publish endpoint.
    row.definition = deepcopy(definition)
    row.revision += 1
    db.commit()
    db.refresh(row)
    return version_out(row)


@router.delete("/{template_id}/versions/{version_id}")
def discard_draft(
    template_id: int, version_id: int, admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    template = get_template(db, template_id)
    row = db.get(DepartmentTemplateVersion, version_id)
    if row is None or row.template_id != template.id:
        raise HTTPException(404, "模板版本不存在")
    if row.status != "draft":
        raise HTTPException(409, "只能放弃草稿")
    if template.current_version_id is None:
        raise HTTPException(409, "模板尚未发布过，不能放弃唯一的草稿")
    record_audit(db, template, admin, "discard", None, version_number=row.version)
    template.versions.remove(row)
    db.commit()
    return template_detail(db, template.id)


@router.post("/{template_id}/versions/{version_id}/publish")
def publish(
    template_id: int, version_id: int, admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    template = get_template(db, template_id)
    row = db.get(DepartmentTemplateVersion, version_id)
    if row is None or row.template_id != template.id:
        raise HTTPException(404, "模板版本不存在")
    if row.status != "draft":
        raise HTTPException(409, "仅草稿可以发布")
    if row.definition.get("key") != template.key:
        raise HTTPException(400, "模板 definition key 与模板 key 不一致")
    try:
        row.definition = templates.validate_definition(dict(row.definition))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    previous_id = template.current_version_id
    row.status = "published"
    row.published_by_id = admin.id
    row.published_at = utcnow()
    template.current_version = row
    template.label = str((row.definition.get("labels") or {}).get("zh") or template.label)
    template.updated_at = utcnow()
    record_audit(db, template, admin, "publish", row, version_number=row.version,
                 previous_version_id=previous_id)
    db.commit()
    return template_detail(db, template.id)


@router.get("/recommendations/{org_unit_id}")
def get_recommendations(
    org_unit_id: int, _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(OrgUnit, org_unit_id) is None:
        raise HTTPException(404, "部门不存在")
    inherited = templates.recommendations(db, org_unit_id)
    direct = set(db.scalars(select(OrgUnitTemplateRecommendation.template_id).where(
        OrgUnitTemplateRecommendation.org_unit_id == org_unit_id
    )))
    return {"templates": [
        {**template_out(row), "direct": row.id in direct} for row in inherited
    ]}


@router.put("/recommendations/{org_unit_id}")
def set_recommendations(
    org_unit_id: int, body: dict = Body(...), _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(OrgUnit, org_unit_id) is None:
        raise HTTPException(404, "部门不存在")
    ids = [int(v) for v in body.get("template_ids") or []]
    rows = list(db.scalars(select(DepartmentTemplate).where(
        DepartmentTemplate.id.in_(ids), DepartmentTemplate.enabled.is_(True)
    ))) if ids else []
    if len(rows) != len(set(ids)):
        raise HTTPException(400, "模板不存在或已停用")
    db.query(OrgUnitTemplateRecommendation).filter_by(org_unit_id=org_unit_id).delete()
    for order, template_id_value in enumerate(ids):
        db.add(OrgUnitTemplateRecommendation(
            org_unit_id=org_unit_id, template_id=template_id_value, sort_order=order
        ))
    db.commit()
    return get_recommendations(org_unit_id, _admin, db)
