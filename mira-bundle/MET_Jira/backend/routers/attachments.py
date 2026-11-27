from __future__ import annotations

import mimetypes
import secrets
import time
from pathlib import Path

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, get_issue, issue_by_id, require_writable_issue
from ..models import Attachment, Issue, User
from ..serialize import attachment_out
from ..services import permissions
from ..services.issues import log, notify, watcher_ids
from ..services.storage import atomic_copy_limited, atomic_replace

router = APIRouter(prefix="/api", tags=["attachments"])

# Prefer extension when the browser sends a blank or generic type (common for Office).
_EXT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".xml": "application/xml",
    ".zip": "application/zip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".log": "text/plain",
}


def _guess_content_type(filename: str, declared: str | None) -> str:
    ctype = (declared or "").split(";", 1)[0].strip().lower()
    if ctype and ctype not in {"application/octet-stream", "binary/octet-stream"}:
        return ctype
    suffix = Path(filename).suffix.lower()
    if suffix in _EXT_TYPES:
        return _EXT_TYPES[suffix]
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _path(att: Attachment) -> Path:
    return get_settings().attachments_dir / att.stored_name


def _pending_path(att: Attachment, action: str) -> Path:
    return _path(att).with_name(f".{att.stored_name}.{action}")


def finalize_upload(att: Attachment) -> None:
    """Atomically expose a committed upload."""
    pending = _pending_path(att, "uploading")
    if pending.exists():
        atomic_replace(pending, _path(att))


def discard_upload(att: Attachment) -> None:
    _pending_path(att, "uploading").unlink(missing_ok=True)
    _path(att).unlink(missing_ok=True)


def reconcile_storage(db: Session) -> dict[str, int]:
    """Reconcile abandoned pending files after a grace period."""
    settings = get_settings()
    directory = settings.attachments_dir
    cutoff = time.time() - max(0, settings.attachment_reconcile_grace_seconds)
    stored = set(db.scalars(select(Attachment.stored_name)))
    finalized = removed = restored = skipped = 0
    for pending in directory.glob(".*.uploading"):
        if pending.stat().st_mtime > cutoff:
            skipped += 1
            continue
        name = pending.name[1:-len(".uploading")]
        if name in stored:
            pending.replace(directory / name)
            finalized += 1
        else:
            pending.unlink(missing_ok=True)
            removed += 1
    for pending in directory.glob(".*.deleting"):
        if pending.stat().st_mtime > cutoff:
            skipped += 1
            continue
        name = pending.name[1:-len(".deleting")]
        if name in stored:
            pending.replace(directory / name)
            restored += 1
        else:
            pending.unlink(missing_ok=True)
            removed += 1
    return {
        "finalized": finalized,
        "restored": restored,
        "removed": removed,
        "skipped": skipped,
    }


async def store_upload(db: Session, issue: Issue, user: User, file: UploadFile) -> Attachment:
    """Write ``file`` to disk and stage its row; the caller logs, notifies and commits."""
    settings = get_settings()
    limit = settings.max_upload_mb * 1024 * 1024
    name = Path(file.filename or "file").name[:300]
    stored = f"{issue.id}-{secrets.token_hex(12)}{Path(name).suffix[:16]}"
    target = settings.attachments_dir / f".{stored}.uploading"
    try:
        size = await to_thread.run_sync(atomic_copy_limited, file.file, target, limit)
    except ValueError as error:
        if str(error) == "file_too_large":
            raise HTTPException(413, f"文件超过 {settings.max_upload_mb} MB") from error
        raise
    content_type = _guess_content_type(name, file.content_type)
    att = Attachment(issue_id=issue.id, uploader_id=user.id, filename=name, stored_name=stored,
                     size=size, content_type=content_type)
    db.add(att)
    return att


@router.post("/issues/{key}/attachments")
async def upload(key: str, file: UploadFile, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> dict:
    issue = get_issue(db, key, user)
    require_writable_issue(db, issue, user)
    att = await store_upload(db, issue, user, file)
    try:
        name = att.filename
        log(db, issue, user, "attachment", new=name)
        notify(db, watcher_ids(db, issue), user, issue, "attachment",
               f"{user.display_name} 向 {issue.key} 上传了附件 {name}")
        db.commit()
    except Exception:
        db.rollback()
        await to_thread.run_sync(discard_upload, att)
        raise
    await to_thread.run_sync(finalize_upload, att)
    db.refresh(att)
    return attachment_out(att)


# Only these may render in the browser (Markdown ?inline=true). Everything else
# is forced to download as octet-stream to avoid stored XSS via HTML/SVG/etc.
_INLINE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/bmp",
    "application/pdf",
})


def _safe_download(att: Attachment, *, inline: bool) -> FileResponse:
    ctype = (att.content_type or "").split(";", 1)[0].strip().lower()
    allow_inline = inline and ctype in _INLINE_TYPES
    headers = {"X-Content-Type-Options": "nosniff"}
    return FileResponse(
        _path(att),
        media_type=ctype if allow_inline else "application/octet-stream",
        filename=att.filename,
        content_disposition_type="inline" if allow_inline else "attachment",
        headers=headers,
    )


@router.get("/attachments/{att_id}")
def download(att_id: int, inline: bool = False, user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> FileResponse:
    att = db.get(Attachment, att_id)
    if att is None or not _path(att).is_file():
        raise HTTPException(404, "附件不存在")
    issue_by_id(db, att.issue_id, user)
    return _safe_download(att, inline=inline)


@router.delete("/attachments/{att_id}")
def remove(att_id: int, user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> dict:
    att = db.get(Attachment, att_id)
    if att is None:
        raise HTTPException(404, "附件不存在")
    issue = issue_by_id(db, att.issue_id, user)
    if issue is None:
        raise HTTPException(404, "问题不存在")
    require_writable_issue(db, issue, user)
    roles = permissions.project_roles(db, issue.project_id, user) if issue else set()
    if att.uploader_id != user.id and not permissions.is_project_admin(user, roles):
        raise HTTPException(403, "只能删除自己上传的附件")
    path = _path(att)
    quarantine = _pending_path(att, "deleting")
    if path.exists():
        path.replace(quarantine)
    try:
        if issue is not None:
            log(db, issue, user, "attachment_removed", old=att.filename)
        db.delete(att)
        db.commit()
    except Exception:
        db.rollback()
        if quarantine.exists():
            quarantine.replace(path)
        raise
    quarantine.unlink(missing_ok=True)
    return {"ok": True}
