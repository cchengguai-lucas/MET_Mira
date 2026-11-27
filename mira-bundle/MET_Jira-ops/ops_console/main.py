"""MET Jira Ops console (port 8803). Standalone: reads presence files, journals and the DB."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import business, ldap_auth, logs, org, presence, sampler, system
from .auth import LoginError, authenticate, issue_session, parse_session, session_secret
from .config import APP_DIR, ENVS, SESSION_COOKIE, SESSION_MAX_AGE, AppEnv, get_env
from .pg import connect
from .store import Store

LOGGER = logging.getLogger("met_jira_ops")
TEMPLATES = Jinja2Templates(directory=str(APP_DIR / "templates"))
STATIC_DIR = APP_DIR / "static"
SECRET = session_secret()
STATE: dict[str, Any] = {}


def get_store() -> Store:
    if "store" not in STATE:
        STATE["store"] = Store()
    return STATE["store"]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = sampler.start(get_store())
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title="MET Jira Ops", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def current_user(request: Request) -> dict[str, Any] | None:
    return parse_session(request.cookies.get(SESSION_COOKIE), SECRET)


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "login required")
    return user


def _safe(fn, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("%s failed: %r", getattr(fn, "__name__", fn), exc)
        return {"error": str(exc).splitlines()[0][:300]}


def _people(env: AppEnv) -> dict[int, dict[str, Any]]:
    try:
        return org.directory(env)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("org directory failed: %r", exc)
        return {}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "met-logo.png", media_type="image/png")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


def _login_context(**extra: Any) -> dict[str, Any]:
    settings = ldap_auth.load_settings()
    return {"error": None, "ldap_label": settings.label if settings.enabled else None, **extra}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Any:
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse(request, "login.html", _login_context())


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)) -> Any:
    try:
        user = authenticate(username, password)
    except LoginError as exc:
        LOGGER.info("ops login rejected for %s: %s", username.strip()[:64], exc)
        return TEMPLATES.TemplateResponse(
            request, "login.html", _login_context(error=str(exc), username=username),
            status_code=401,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("login failed: %r", exc)
        return TEMPLATES.TemplateResponse(
            request, "login.html",
            _login_context(error="Login service unavailable. "
                                 "Check configs/ops.env and the MET AD settings.",
                           username=username),
            status_code=503,
        )
    LOGGER.info("ops login ok: %s via %s", user["username"], user.get("via"))
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, issue_session(user, SECRET), max_age=SESSION_MAX_AGE,
                        httponly=True, samesite="lax")
    return response


@app.post("/logout")
def logout() -> Any:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> Any:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    envs = [{"key": e.key, "label": e.label, "url": e.public_url} for e in ENVS.values()]
    version = int(max(p.stat().st_mtime for p in STATIC_DIR.iterdir()))
    return TEMPLATES.TemplateResponse(
        request, "dashboard.html", {"user": user, "envs": envs, "v": version}
    )


@app.get("/api/overview")
def api_overview(hours: int = Query(6, ge=1, le=48),
                 _user: dict = Depends(require_user)) -> dict:
    store = get_store()
    envs = {}
    for env in ENVS.values():
        envs[env.key] = {
            "label": env.label,
            "url": env.public_url,
            "online": org.annotate(presence.online(env.key), _people(env)),
            "counts": _safe(_counts, env),
            "requests": _safe(logs.unit_summary, env.unit, hours),
            "uptime_24h": store.uptime(f"web:{env.key}", 24),
            "uptime_7d": store.uptime(f"web:{env.key}", 24 * 7),
        }
    return {
        "services": system.services(),
        "envs": envs,
        "llm": {**store.uptime("llm", 24), "series": store.probe_series("llm", 24)},
        "postgres": store.uptime("postgres", 24),
    }


def _counts(env: AppEnv) -> dict:
    with connect(env) as conn:
        return business.counts(conn)


@app.get("/api/presence")
def api_presence(hours: float = Query(24, ge=1, le=24 * 31),
                 _user: dict = Depends(require_user)) -> dict:
    store = get_store()
    out = {}
    for key, env in ENVS.items():
        people = _people(env)
        history = store.online_history(key, hours)
        ids = {uid for point in history for uid in point[2]}
        labels = store.recent_labels(key, ids - people.keys())
        out[key] = {
            "online": org.annotate(presence.online(key), people),
            "history": history,
            "people": {
                uid: {"name": people[uid]["name"], "team": people[uid]["team"]}
                if uid in people else {"name": labels.get(uid, f"#{uid}"), "team": org.UNASSIGNED}
                for uid in ids
            },
        }
    return out


@app.get("/api/usage")
def api_usage(env: str = "production", days: int = Query(30, ge=1, le=120),
              _user: dict = Depends(require_user)) -> dict:
    store = get_store()
    target = get_env(env)
    page_rows = store.page_dwell(target.key, days)
    for row in page_rows:
        row["label"] = presence.PAGE_LABELS.get(row["page"], row["page"] or "—")
    people = _people(target)
    totals = store.leaderboard(target.key, days, limit=-1)
    return {
        "daily": store.daily(target.key, days),
        "leaderboard": org.annotate(totals[:15], people),
        "departments": org.usage_by_dept(totals, people),
        "pages": page_rows,
        "events": presence.event_summary(target.key, days),
    }


@app.get("/api/business")
def api_business(env: str = "production", days: int = Query(30, ge=1, le=180),
                 _user: dict = Depends(require_user)) -> dict:
    target = get_env(env)
    data = _safe(business.business, target, days)
    if isinstance(data, dict) and "error" not in data:
        people = _people(target)
        data["requests_by_dept"] = org.issues_by_dept(data.pop("reporters"), people)
        data["headcount"] = [
            {"dept": dept, "n": n} for dept, n in org.headcount(people).most_common()
        ]
    return data


@app.get("/api/ai")
def api_ai(env: str = "production", days: int = Query(30, ge=1, le=180),
           _user: dict = Depends(require_user)) -> dict:
    data = _safe(business.ai, get_env(env), days)
    store = get_store()
    if isinstance(data, dict):
        data["llm"] = {
            "uptime_24h": store.uptime("llm", 24),
            "uptime_7d": store.uptime("llm", 24 * 7),
            "series": store.probe_series("llm", 48),
        }
    return data


@app.get("/api/logs")
def api_logs(env: str = "production", hours: int = Query(24, ge=1, le=24 * 14),
             _user: dict = Depends(require_user)) -> dict:
    return logs.unit_summary(get_env(env).unit, hours)


@app.get("/api/system")
def api_system(_user: dict = Depends(require_user)) -> dict:
    return {
        "host": system.host(),
        "ldap": ldap_auth.status(),
        "databases": {key: _safe(business.database, env) for key, env in ENVS.items()},
        "probes": {
            target: get_store().probe_series(target, 24)
            for target in ("web:production", "web:staging", "postgres", "llm")
        },
    }
