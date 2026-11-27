"""Mira FastAPI application."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.gzip import GZipMiddleware

from .config import get_settings
from .db import init_db, is_postgres, session_factory
from .metrics import observe_http, render, update_outbox
from .ops import seed
from .routers import (
    admin,
    agent,
    assets,
    attachments,
    auth,
    boards,
    comments,
    cover_presets,
    excel,
    issues,
    live,
    notifications,
    org,
    presence,
    projects,
    reports,
    search,
    sprints,
)
from .routers import (
    templates as template_routes,
)
from .services import ratelimit
from .services.issues import IssueError
from .services.locale import LangMiddleware


class ImmutableStaticFiles(StaticFiles):
    """Hashed Vite assets are immutable; index.html remains explicitly uncached."""

    async def get_response(self, path: str, scope: dict) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    settings.imports_dir.mkdir(parents=True, exist_ok=True)
    if not is_postgres():
        init_db()
        seed()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Mira", version="0.1.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=6)
    app.add_middleware(LangMiddleware)
    ratelimit.reset()

    @app.middleware("http")
    async def _request_metrics(request: Request, call_next):
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", "unmatched")
            observe_http(request.method, template, status, time.monotonic() - started)

    @app.exception_handler(IssueError)
    async def _issue_error(_request: Request, exc: IssueError) -> JSONResponse:
        body: dict = {"detail": exc.message}
        if exc.code:
            body["code"] = exc.code
        if exc.fields:
            body["fields"] = exc.fields
        return JSONResponse(body, status_code=exc.status_code)

    for module in (
        auth,
        assets,
        org,
        projects,
        cover_presets,
        issues,
        comments,
        attachments,
        boards,
        sprints,
        search,
        notifications,
        reports,
        excel,
        agent,
        admin,
        template_routes,
        presence,
        live,
    ):
        app.include_router(module.router)

    @app.get("/api/health")
    def health(response: Response) -> dict:
        """Liveness for deploy scripts and the Ops console: 503 unless the database answers and
        the data directory is writable. Backup age is reported but never fails the check."""
        settings = get_settings()
        checks: dict[str, str] = {}
        revision = None
        try:
            with session_factory()() as db:
                db.execute(text("select 1"))
                checks["database"] = "ok"
                try:
                    revision = db.scalar(text("select version_num from alembic_version"))
                except SQLAlchemyError:
                    db.rollback()
        except SQLAlchemyError as error:
            checks["database"] = f"error: {type(error).__name__}"
        data_dir = settings.data_dir
        checks["data_dir"] = ("ok" if data_dir.is_dir() and os.access(data_dir, os.W_OK)
                              else "error: not writable")
        ok = all(value == "ok" for value in checks.values())
        if not ok:
            response.status_code = 503
        body: dict = {"ok": ok, "env": settings.deploy_env, "checks": checks,
                      "revision": revision}
        last_backup = settings.backup_dir / "last-backup.json"
        if settings.deploy_env == "production" and last_backup.is_file():
            age = time.time() - last_backup.stat().st_mtime
            body["backup_age_hours"] = round(age / 3600, 1)
        return body

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1"}:
            raise HTTPException(403, "metrics 仅允许本机访问")
        try:
            from .services.outbox import status_metrics

            with session_factory()() as db:
                outbox = status_metrics(db)
            update_outbox(outbox["status"], outbox["retry_attempts"])
        except SQLAlchemyError:
            pass
        content, content_type = render()
        return Response(content, headers={"Content-Type": content_type})

    dist: Path = get_settings().web_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            # stdlib mimetypes often omits .webp; wrong type breaks animated Guide demos.
            if candidate.suffix.lower() == ".webp":
                return FileResponse(candidate, media_type="image/webp")
            return FileResponse(candidate)
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "web/dist 未构建，请运行 npm run build"}, 404)

    return app


app = create_app()
