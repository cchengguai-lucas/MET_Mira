"""Server-Sent Events for live project / issue updates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..db import session_factory
from ..deps import current_user_id, get_project
from ..models import User
from ..services import live

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/stream")
async def stream(
    request: Request,
    project: str = Query(..., min_length=2, max_length=10),
    user_id: int = Depends(current_user_id),
) -> StreamingResponse:
    key = project.strip().upper()
    with session_factory()() as db:
        user = db.get(User, user_id)
        if user is None or not user.enabled:
            raise HTTPException(401, "账号不可用")
        get_project(db, key, user, "view")

    queue = live.hub.subscribe({key})

    async def events():
        try:
            async for chunk in live.iter_sse(queue, request.is_disconnected):
                yield chunk
        finally:
            live.hub.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
