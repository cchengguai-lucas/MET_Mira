from __future__ import annotations

import re
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, get_project
from ..models import ImportBatch, Issue, User
from ..serialize import iso, user_brief
from ..services import concurrency, presence
from ..services.excel_io import (
    apply_profile_guesses,
    export_issues,
    import_rows,
    preview_rows,
    read_sheet,
)
from ..services.profiles import profile_of
from ..services.search import JqlError, search_issues, view_conditions
from ..services.storage import atomic_copy_limited
from .search import parse_view

router = APIRouter(prefix="/api", tags=["excel"])
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


class MappingIn(BaseModel):
    token: str
    sheet: str | None = None
    mapping: dict[int, str | None]
    create_missing_machines: bool = True


def _xlsx(content: bytes, name: str) -> Response:
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


def _export_base(request: Request) -> str:
    """Prefer configured public URL so Excel links open the real Mira host."""
    return get_settings().public_url or str(request.base_url).rstrip("/")


@router.get("/export.xlsx")
def export_search(request: Request, jql: str = "", view: str = "",
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    try:
        extra = view_conditions(parse_view(view), user_id=user.id)
        _, rows = search_issues(db, jql, user_id=user.id, limit=5000, extra=extra)
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error
    presence.record_event(user, "export_excel", page="search", kind="search")
    return _xlsx(
        export_issues(rows, base_url=_export_base(request)),
        f"issues-{date.today().isoformat()}.xlsx",
    )


@router.get("/projects/{key}/export.xlsx")
def export_project(key: str, request: Request, jql: str = "",
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    project = get_project(db, key, user)
    try:
        _, rows = search_issues(db, jql, user_id=user.id, limit=5000,
                                extra=[Issue.project_id == project.id])
    except JqlError as error:
        raise HTTPException(400, f"查询语法错误：{error}") from error
    content = export_issues(rows, project.name, project, base_url=_export_base(request))
    presence.record_event(user, "export_excel", page="list", kind=project.key)
    return _xlsx(content, f"{project.key}-{date.today().isoformat()}.xlsx")


def _token_path(token: str) -> Path:
    if not TOKEN_RE.match(token):
        raise HTTPException(400, "无效的导入令牌")
    path = get_settings().imports_dir / f"{token}.xlsx"
    if not path.is_file():
        raise HTTPException(404, "导入文件已过期，请重新上传")
    return path


@router.post("/projects/{key}/import/preview")
async def import_preview(key: str, file: UploadFile, sheet: str | None = Form(None),
                         user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "仅支持 .xlsx / .xlsm 文件")
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    token = secrets.token_hex(16)
    path = settings.imports_dir / f"{token}.xlsx"
    try:
        await to_thread.run_sync(atomic_copy_limited, file.file, path, limit)
        parsed = await to_thread.run_sync(read_sheet, path, sheet)
    except ValueError as error:
        await to_thread.run_sync(path.unlink, True)
        if str(error) == "file_too_large":
            raise HTTPException(413, f"文件超过 {settings.max_upload_mb} MB") from error
        raise
    except HTTPException:
        await to_thread.run_sync(path.unlink, True)
        raise
    except Exception as error:  # noqa: BLE001 - malformed workbook
        await to_thread.run_sync(path.unlink, True)
        raise HTTPException(400, f"无法读取 Excel：{error}") from error
    apply_profile_guesses(parsed, profile_of(project))
    mapping = {c["index"]: c["guess"] for c in parsed["columns"] if c["guess"]}
    preview = preview_rows(db, project, user, parsed, mapping)
    db.rollback()
    return {
        "token": token,
        "filename": file.filename,
        "sheets": parsed["sheets"],
        "sheet": parsed["sheet"],
        "header_row": parsed["header_row"],
        "columns": parsed["columns"],
        "mapping": mapping,
        **preview,
    }


@router.post("/projects/{key}/import/remap")
def import_remap(key: str, body: MappingIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    parsed = read_sheet(_token_path(body.token), body.sheet)
    apply_profile_guesses(parsed, profile_of(project))
    mapping = {int(k): v for k, v in body.mapping.items() if v}
    preview = preview_rows(db, project, user, parsed, mapping)
    db.rollback()
    return {"columns": parsed["columns"], "sheet": parsed["sheet"], "mapping": mapping, **preview}


@router.post("/projects/{key}/import/commit")
def import_commit(key: str, body: MappingIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user, "admin")
    concurrency.lock_project(db, project.id)
    existing = db.scalar(select(ImportBatch).where(ImportBatch.token == body.token))
    if existing is not None:
        return dict(existing.result or {
            "batch_id": existing.id,
            "created": [],
            "warnings": [],
        })
    path = _token_path(body.token)
    parsed = read_sheet(path, body.sheet)
    apply_profile_guesses(parsed, profile_of(project))
    mapping = {int(k): v for k, v in body.mapping.items() if v}
    batch = ImportBatch(
        project_id=project.id,
        user_id=user.id,
        filename=path.name,
        token=body.token,
    )
    db.add(batch)
    db.flush()
    created, warnings = import_rows(db, project, user, parsed, mapping, batch_id=batch.id,
                                    create_missing_machines=body.create_missing_machines)
    batch.count = len(created)
    result = {"batch_id": batch.id, "created": created, "warnings": warnings}
    batch.result = result
    db.commit()
    path.unlink(missing_ok=True)
    return result


@router.get("/projects/{key}/imports")
def list_imports(key: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    project = get_project(db, key, user)
    rows = db.scalars(
        select(ImportBatch).where(ImportBatch.project_id == project.id)
        .order_by(ImportBatch.id.desc()).limit(30)
    )
    result = []
    for row in rows:
        owner = db.get(User, row.user_id) if row.user_id else None
        result.append({"id": row.id, "filename": row.filename, "count": row.count,
                       "undone": row.undone, "user": user_brief(owner),
                       "created_at": iso(row.created_at)})
    return {"imports": result}


@router.post("/imports/{batch_id}/undo")
def undo_import(batch_id: int, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "导入批次不存在")
    get_project(db, batch.project_id, user, "admin")
    if batch.undone:
        raise HTTPException(409, "该批次已撤销")
    ids = list(db.scalars(select(Issue.id).where(Issue.import_batch_id == batch.id)))
    if ids:
        db.execute(update(Issue).where(Issue.parent_id.in_(ids))
                   .values(parent_id=None, type="task"))
        db.execute(update(Issue).where(Issue.epic_id.in_(ids)).values(epic_id=None))
        db.execute(delete(Issue).where(Issue.id.in_(ids)))
    batch.undone = True
    db.commit()
    return {"deleted": len(ids)}
