from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db, utcnow
from ..deps import current_user, get_project, require_admin
from ..models import Issue, Machine, MasterAsset, Project, ProjectAsset, User
from ..schemas import AssetBulkIn, AssetIn, ProjectAssetIn
from ..services import profiles, templates

router = APIRouter(prefix="/api/assets", tags=["assets"])
MASTER_KINDS = ("material", "line", "equipment", "service")


def asset_out(asset: MasterAsset) -> dict:
    return {
        "id": asset.id, "kind": asset.kind, "code": asset.code, "name": asset.name,
        "active": asset.active, "attributes": asset.attributes or {},
    }


@router.get("")
def list_assets(kind: str | None = None, q: str = "", include_inactive: bool = False,
                _user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    stmt = select(MasterAsset).order_by(MasterAsset.kind, MasterAsset.code)
    if kind:
        stmt = stmt.where(MasterAsset.kind == kind)
    if not include_inactive:
        stmt = stmt.where(MasterAsset.active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(MasterAsset.code.ilike(like), MasterAsset.name.ilike(like)))
    return {"assets": [asset_out(asset) for asset in db.scalars(stmt)]}


@router.get("/overview")
def overview(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Per kind: entry counts, the template fields that use it and how often each entry is used."""
    kinds = {kind: {"active": 0, "inactive": 0, "fields": []} for kind in MASTER_KINDS}
    for kind, active, count in db.execute(
        select(MasterAsset.kind, MasterAsset.active, func.count(MasterAsset.id))
        .group_by(MasterAsset.kind, MasterAsset.active)
    ):
        if kind in kinds:
            kinds[kind]["active" if active else "inactive"] += count
    for template in templates.catalog(db):
        version = template.current_version
        for field in (version.definition.get("fields") or []) if version else []:
            kind = field.get("master_kind")
            if field.get("kind") == "master_ref" and kind in kinds:
                labels = field.get("labels") or {}
                kinds[kind]["fields"].append({
                    "template_id": template.id, "template": template.label,
                    "field": field.get("key"), "label": labels.get("zh") or field.get("key"),
                })
    issues: Counter[int] = Counter()
    projects = db.scalars(select(Project).options(selectinload(Project.template_version)))
    for project in projects:
        keys = [f.key for f in profiles.profile_of(project).custom_fields if f.kind == "master_ref"]
        if not keys:
            continue
        for (extra,) in db.execute(select(Issue.extra).where(Issue.project_id == project.id)):
            for key in keys:
                value = (extra or {}).get(key)
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, int) and not isinstance(item, bool):
                        issues[item] += 1
    bindings = Counter(dict(db.execute(
        select(ProjectAsset.asset_id, func.count(ProjectAsset.id)).group_by(ProjectAsset.asset_id)
    ).all()))
    usage = {
        asset_id: {"issues": issues.get(asset_id, 0), "projects": bindings.get(asset_id, 0)}
        for asset_id in set(issues) | set(bindings)
    }
    return {"kinds": kinds, "usage": usage}


@router.post("/bulk")
def bulk_create(body: AssetBulkIn, _admin: User = Depends(require_admin),
                db: Session = Depends(get_db)) -> dict:
    existing = set(db.scalars(select(MasterAsset.code).where(MasterAsset.kind == body.kind)))
    created, skipped = 0, []
    for item in body.items:
        code, name = item.code.strip(), item.name.strip()
        if not code or not name or code in existing:
            skipped.append(code or name)
            continue
        db.add(MasterAsset(kind=body.kind, code=code, name=name, active=True, attributes={}))
        existing.add(code)
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


@router.post("")
def create_asset(body: AssetIn, _admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(MasterAsset.id).where(
        MasterAsset.kind == body.kind, MasterAsset.code == body.code
    )):
        raise HTTPException(409, "该类型下资产编码已存在")
    asset = MasterAsset(**body.model_dump())
    db.add(asset)
    db.commit()
    return asset_out(asset)


@router.put("/{asset_id}")
def update_asset(asset_id: int, body: AssetIn, _admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)) -> dict:
    asset = db.get(MasterAsset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    if db.scalar(select(MasterAsset.id).where(
        MasterAsset.kind == body.kind, MasterAsset.code == body.code, MasterAsset.id != asset_id
    )):
        raise HTTPException(409, "该类型下资产编码已存在")
    for key, value in body.model_dump().items():
        setattr(asset, key, value)
    asset.updated_at = utcnow()
    db.commit()
    return asset_out(asset)


@router.get("/project/{key}")
def project_assets(key: str, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    rows = db.scalars(select(ProjectAsset).options(selectinload(ProjectAsset.asset))
                      .where(ProjectAsset.project_id == project.id).order_by(ProjectAsset.id))
    return {"assets": [
        {**asset_out(row.asset), "binding_id": row.id, "alias": row.alias,
         "local_machine_id": row.local_machine_id}
        for row in rows
    ]}


@router.post("/project/{key}")
def bind_asset(key: str, body: ProjectAssetIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    if db.get(MasterAsset, body.asset_id) is None:
        raise HTTPException(404, "资产不存在")
    if body.local_machine_id:
        machine = db.get(Machine, body.local_machine_id)
        if machine is None or machine.group.project_id != project.id:
            raise HTTPException(400, "机台不属于该项目")
    row = db.scalar(select(ProjectAsset).where(
        ProjectAsset.project_id == project.id, ProjectAsset.asset_id == body.asset_id
    ))
    if row is None:
        row = ProjectAsset(project_id=project.id, **body.model_dump())
        db.add(row)
    else:
        row.local_machine_id, row.alias = body.local_machine_id, body.alias
    db.commit()
    return {"ok": True}


@router.post("/project/{key}/map-machines")
def map_machines(key: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    """Non-destructively create equipment catalog entries and bind existing project machines."""
    project = get_project(db, key, user, "admin")
    machines = list(db.scalars(
        select(Machine).where(Machine.group.has(project_id=project.id)).order_by(Machine.id)
    ))
    created = bound = 0
    for machine in machines:
        code = f"{machine.group.name}/{machine.name}"
        asset = db.scalar(select(MasterAsset).where(
            MasterAsset.kind == "equipment", MasterAsset.code == code
        ))
        if asset is None:
            asset = MasterAsset(kind="equipment", code=code, name=machine.name,
                                attributes={"group": machine.group.name})
            db.add(asset)
            db.flush()
            created += 1
        link = db.scalar(select(ProjectAsset).where(
            ProjectAsset.project_id == project.id, ProjectAsset.asset_id == asset.id
        ))
        if link is None:
            db.add(ProjectAsset(project_id=project.id, asset_id=asset.id,
                                local_machine_id=machine.id))
            bound += 1
    db.commit()
    return {"created": created, "bound": bound, "machines": len(machines)}
