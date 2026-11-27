"""Built-in project cover presets (semiconductor-style banners)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..deps import current_user
from ..models import User
from ..services import covers

router = APIRouter(prefix="/api/cover-presets", tags=["covers"])


@router.get("")
def list_cover_presets(user: User = Depends(current_user)) -> dict:
    return {"presets": covers.list_presets()}


@router.get("/{preset_id}")
def get_cover_preset(preset_id: str, user: User = Depends(current_user)) -> FileResponse:
    if preset_id not in covers.PRESETS:
        raise HTTPException(404, "默认封面不存在")
    path = covers.preset_path(preset_id)
    if not path.is_file():
        raise HTTPException(404, "默认封面不存在")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
