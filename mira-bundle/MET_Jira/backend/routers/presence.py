from __future__ import annotations

from anyio import to_thread
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import current_user
from ..models import User
from ..services import presence

router = APIRouter(prefix="/api/presence", tags=["presence"])


class HeartbeatIn(BaseModel):
    page: str | None = Field(default=None, max_length=80)


@router.post("/heartbeat")
async def heartbeat(body: HeartbeatIn, user: User = Depends(current_user)) -> dict:
    await to_thread.run_sync(presence.touch, user, body.page)
    return {"ok": True}


@router.post("/leave")
async def leave(user: User = Depends(current_user)) -> dict:
    await to_thread.run_sync(presence.leave, user)
    return {"ok": True}
