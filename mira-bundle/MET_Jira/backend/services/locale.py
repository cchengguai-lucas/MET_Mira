"""Per-request UI language, taken from the ``X-MetJira-Lang`` header sent by the web client."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_lang: ContextVar[str] = ContextVar("metjira_lang", default="zh")


def current_lang() -> str:
    return _lang.get()


def parse_lang(value: str | None) -> str:
    return "en" if (value or "").strip().lower().startswith("en") else "zh"


class LangMiddleware:
    """Pure ASGI so the value is visible to streaming bodies and threadpool endpoints."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = next((v for k, v in scope.get("headers", []) if k == b"x-metjira-lang"), b"")
        token = _lang.set(parse_lang(header.decode("latin-1")))
        try:
            await self.app(scope, receive, send)
        finally:
            _lang.reset(token)
