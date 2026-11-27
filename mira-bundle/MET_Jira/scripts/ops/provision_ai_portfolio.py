#!/usr/bin/env python3
"""Provision AI Portfolio + English projects per owner product.

- Redesigns the shared ``ai`` department template (English, requirement-focused).
- Renames project AI → AI Portfolio, lead/admin = houcheng.fan.
- Creates one ongoing project per lucas / Ryan / Zhiyu production product,
  with that owner as project lead/admin and known users as requesters.
- Intended to run with outbox workers stopped so no mail is sent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = os.environ.get("METJIRA_BASE", "http://127.0.0.1:8800")
ADMIN_USER = os.environ["METJIRA_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["METJIRA_ADMIN_PASSWORD"]
LEAD_USERNAME = os.environ.get("METJIRA_AI_LEAD", "houcheng.fan")

# service_code → project key (max 10 chars: [A-Za-z][A-Za-z0-9]{1,9})
# Grouped by product owner (project lead / admin).
OWNER_PROJECTS: dict[str, list[dict]] = {
    "houcheng.fan": [  # lucas products
        {"code": "mira", "key": "MIRA", "name": "Mira", "color": "#5e5ce6"},
        {"code": "filechecker", "key": "FILECHK", "name": "FileChecker", "color": "#0a84ff"},
        {"code": "ai-review", "key": "DRAWREV", "name": "Drawing Review", "color": "#64d2ff"},
        {"code": "interlock-maker", "key": "INTERLOCK",
         "name": "Interlock Maker", "color": "#30b0c7"},
        {"code": "metrology", "key": "METROLOGY", "name": "Metrology Web", "color": "#34c759"},
        {"code": "foup-qr", "key": "FOUPQR", "name": "FOUP QR", "color": "#30d158"},
        {"code": "finance-invoice", "key": "FININV",
         "name": "Finance Invoice", "color": "#ff453a"},
    ],
    "ryan.fang": [
        {"code": "workhour", "key": "WORKHOUR", "name": "Workhour", "color": "#ff9f0a"},
        {"code": "bom-builder", "key": "BOM", "name": "BOM Builder", "color": "#ff9500"},
        {"code": "planning", "key": "PLANNING", "name": "Planning", "color": "#ffcc00"},
        {"code": "critical-parts", "key": "CRITPARTS",
         "name": "Critical Parts", "color": "#ffd60a"},
        {"code": "finance-sap", "key": "FINSAP", "name": "Finance SAP", "color": "#ff6961"},
        {"code": "agent-runtime", "key": "AGENTRT",
         "name": "Agent Runtime", "color": "#ac8e68"},
        {"code": "open-webui", "key": "OPENWEBUI", "name": "Open WebUI", "color": "#8e8e93"},
    ],
    "zhiyu.wang": [
        {"code": "autotem", "key": "AUTOTEM", "name": "AutoTEM", "color": "#a2845e"},
        {"code": "doclab", "key": "DOCLAB", "name": "DocLab", "color": "#bf5af2"},
        {"code": "ocr", "key": "OCR", "name": "OCR Tool", "color": "#da8fff"},
        {"code": "data-analyst", "key": "ANALYST",
         "name": "Data Analyst", "color": "#64d2ff"},
    ],
}

SERVICE_PROJECTS: list[dict] = [
    spec for specs in OWNER_PROJECTS.values() for spec in specs
]

# Not owned by lucas / Ryan / Zhiyu — purge if present.
DELETE_PROJECT_KEYS = {
    "QMSCOA", "OPENWEBSW", "HOMEPAGE", "LANGFUSE", "WFBUILD", "VLLM", "AIHUB",
}

STATUS_MAP_ZH_TO_EN = {
    "未开展": "Backlog",
    "进行中": "In Progress",
    "已延期": "Deferred",
    "已暂停": "Paused",
    "重新打开": "Reopened",
    "已完成": "Done",
    "已取消": "Cancelled",
}


def api(opener, method: str, path: str, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        err = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {err[:1500]}") from exc


def build_ai_definition() -> dict:
    from backend.services.ai_template import build_ai_definition as _build

    return _build()


def publish_template(opener) -> int:
    templates = api(opener, "GET", "/api/admin/templates")["templates"]
    tpl = next((t for t in templates if t["key"] == "ai"), None)
    definition = build_ai_definition()
    if tpl is None:
        tpl = api(opener, "POST", "/api/admin/templates", {
            "key": "ai",
            "label": "AI",
            "description": "Lightweight AI / product intake template",
        })
        print(f"created template ai id={tpl['id']}")
    detail = api(opener, "GET", f"/api/admin/templates/{tpl['id']}")
    current = detail.get("current_version") or {}
    if current.get("definition") == definition and current.get("status") == "published":
        print("ai template already up to date")
        return current["id"]
    draft = None
    for ver in detail.get("versions") or []:
        if ver.get("status") == "draft":
            draft = ver
            break
    if draft is None:
        draft = api(opener, "POST", f"/api/admin/templates/{tpl['id']}/versions", {})
    saved = api(opener, "PUT", f"/api/admin/templates/{tpl['id']}/versions/{draft['id']}", {
        "revision": draft["revision"],
        "definition": definition,
    })
    if saved.get("validation_errors"):
        raise RuntimeError(saved["validation_errors"])
    pub = api(opener, "POST",
              f"/api/admin/templates/{tpl['id']}/versions/{saved['id']}/publish", {})
    version_id = pub["current_version"]["id"]
    print(f"published ai template v{pub['current_version']['version']} id={version_id}")
    return version_id


def migrate_project(opener, key: str, version_id: int) -> None:
    detail = api(opener, "GET", f"/api/projects/{key}")
    project = detail["project"]
    if project.get("template_version_id") == version_id:
        print(f"{key}: already on template version {version_id}")
        return
    preview = api(opener, "POST", f"/api/projects/{key}/template-migration/preview", {
        "target_version_id": version_id,
    })
    status_map = {}
    for row in preview.get("statuses") or []:
        if not row.get("issues"):
            continue
        name = row["name"]
        suggested = STATUS_MAP_ZH_TO_EN.get(name) or row.get("suggested") or name
        # Prefer English target names from template
        targets = {s["name"] for s in preview.get("target_statuses") or []}
        if suggested not in targets and name in targets:
            suggested = name
        if suggested not in targets:
            # fallback first todo
            suggested = next(iter(targets), suggested)
        status_map[str(row["id"])] = suggested
    field_map = {}
    for row in preview.get("fields") or []:
        if not row.get("usage"):
            continue
        keep = {
            "title", "description", "related_services", "ref_url", "blocker",
            "delivery_note", "tester_id", "test_passed", "update_date", "assignee_id",
        }
        field_map[row["key"]] = row.get("suggested") or (
            row["key"] if row["key"] in keep else "orphan"
        )
        if field_map[row["key"]] == "blocker":
            field_map[row["key"]] = "orphan"  # dropped from new template
    role_map = {
        role: (role if role in (preview.get("target_roles") or []) else "member")
        for role in (preview.get("invalid_roles") or {})
    }
    result = api(opener, "POST", f"/api/projects/{key}/template-migration/apply", {
        "target_version_id": version_id,
        "mapping": {
            "status_map": status_map,
            "field_map": field_map,
            "role_map": role_map,
        },
    })
    print(f"{key}: migrated template -> {result.get('status')}")
    prune_extra_statuses(opener, key)


def prune_extra_statuses(opener, key: str) -> None:
    """Drop leftover statuses that are not in the English AI template."""
    keep = {s["name"] for s in build_ai_definition()["statuses"]}
    detail = api(opener, "GET", f"/api/projects/{key}")
    removed = 0
    for status in detail.get("statuses") or []:
        if status["name"] in keep:
            continue
        try:
            api(opener, "DELETE", f"/api/projects/{key}/statuses/{status['id']}")
            removed += 1
        except RuntimeError as exc:
            print(f"  keep status {status['name']!r} on {key}: {exc}")
    if removed:
        print(f"{key}: pruned {removed} leftover statuses")
    # Align workflow transitions to English names
    api(opener, "PUT", f"/api/projects/{key}", {
        "settings": {
            "work_mode": "ongoing",
            "transitions": build_ai_definition()["workflows"],
        },
    })


def ensure_portfolio(opener, lead_id: int, version_id: int) -> dict:
    projects = {p["key"]: p for p in api(opener, "GET", "/api/projects")["projects"]}
    if "AI" not in projects:
        raise RuntimeError("project AI missing — create/import first")
    # Rename + lead + English description
    api(opener, "PUT", "/api/projects/AI", {
        "name": "AI Portfolio",
        "description": (
            "Internal AI-team portfolio: cross-product epics and intake. "
            "Per-service delivery lives in dedicated projects (MIRA, FILECHK, …)."
        ),
        "color": "#5e5ce6",
        "lead_id": lead_id,
        "settings": {
            "work_mode": "ongoing",
            "visibility": "members",
            "with_machines": False,
        },
    })
    # Admin membership for lead
    detail = api(opener, "GET", "/api/projects/AI")
    members = detail.get("members") or []
    admin_ids = {m["user"]["id"] for m in members if m["role"] == "admin"}
    if lead_id not in admin_ids:
        api(opener, "POST", "/api/projects/AI/members", {"user_id": lead_id, "role": "admin"})
        print(f"AI Portfolio: added {LEAD_USERNAME} as admin")
    migrate_project(opener, "AI", version_id)
    # Sync English workflows onto project settings
    workflows = build_ai_definition()["workflows"]
    api(opener, "PUT", "/api/projects/AI", {
        "settings": {"work_mode": "ongoing", "transitions": workflows},
    })
    print("AI Portfolio ready")
    return api(opener, "GET", "/api/projects/AI")


def load_service_users() -> dict[str, set[str]]:
    """Best-effort username sets per service code."""
    users: dict[str, set[str]] = {row["code"]: set() for row in SERVICE_PROJECTS}

    def add(code: str, name: str) -> None:
        name = (name or "").strip().lower().replace(" ", ".")
        name = name.split("@", 1)[0]
        if not name or name in {"developer", "admin", "hub.smoketest", "系统管理员"}:
            return
        if code in users:
            users[code].add(name)

    # Planning allowlist
    for path in (
        Path("/home/hal/docker/planning/config/allowed_users.txt"),
        Path("/home/hal/docker/planning-beta/config/allowed_users.txt"),
    ):
        if path.is_file():
            for line in path.read_text().splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    add("planning", line)

    # Mira ops usage
    ops_db = Path.home() / ".met_jira_ops" / "ops.sqlite3"
    if ops_db.is_file():
        try:
            con = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
            for (label,) in con.execute(
                "SELECT DISTINCT lower(label) FROM usage WHERE label NOT LIKE '%管理%'"
            ):
                add("mira", label)
            con.close()
        except sqlite3.Error:
            pass

    # FileChecker learned fixes + presence
    fc_db = Path("/data/lucas/projects/FileChecker/data/filechecker.sqlite3")
    if fc_db.is_file():
        try:
            con = sqlite3.connect(f"file:{fc_db}?mode=ro", uri=True)
            for (u,) in con.execute(
                "SELECT DISTINCT lower(username) FROM learned_fixes "
                "WHERE username IS NOT NULL AND username != ''"
            ):
                add("filechecker", u)
                add("ai-review", u)
            con.close()
        except sqlite3.Error:
            pass

    # Metrology
    try:
        out = subprocess.check_output(
            [
                "psql", "-h", "127.0.0.1", "-p", "5437", "-U", "metrology", "-d", "metrology",
                "-Atc", "SELECT lower(username) FROM app_users WHERE is_active IS TRUE",
            ],
            env={**os.environ, "PGPASSWORD": "metai2026"},
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for u in out.splitlines():
            add("metrology", u)
            add("foup-qr", u)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Critical parts successful logins
    try:
        out = subprocess.check_output(
            [
                "docker", "exec", "critical-parts-db-prod",
                "psql", "-U", "critical_parts", "-d", "critical_parts", "-Atc",
                "SELECT DISTINCT lower(regexp_replace(trim(username), ' +', '.', 'g')) "
                "FROM login_audit WHERE success IS TRUE",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for u in out.splitlines():
            add("critical-parts", u)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Open WebUI
    webui = Path("/home/hal/docker/open-webui/data/webui.db")
    if webui.is_file():
        try:
            con = sqlite3.connect(f"file:{webui}?mode=ro", uri=True)
            for (u,) in con.execute(
                "SELECT lower(coalesce(nullif(username,''), "
                "replace(email,'@micropolaris.com.cn',''))) FROM user"
            ):
                add("open-webui", u)
                add("agent-runtime", u)
            con.close()
        except sqlite3.Error:
            pass

    # Workhour: only users who already exist in Mira (avoid dumping whole AD)
    # filled later after we have Mira usernames

    # AI portfolio assignees as seeds for matching services via issue links
    return users


def resolve_mira_users(opener) -> dict[str, int]:
    by_name: dict[str, int] = {}
    page = 0
    while True:
        data = api(opener, "GET", f"/api/users?limit=500&offset={page * 500}")
        for u in data.get("users") or []:
            if u.get("enabled", True):
                by_name[u["username"].lower()] = u["id"]
        if len(data.get("users") or []) < 500:
            break
        page += 1
        if page > 20:
            break
    return by_name


def enrich_workhour(users: dict[str, set[str]], mira_names: set[str]) -> None:
    try:
        out = subprocess.check_output(
            [
                "docker", "exec", "workhour-db",
                "psql", "-U", "workhour", "-d", "workhour", "-Atc",
                "SELECT lower(username) FROM users WHERE last_login_at IS NOT NULL "
                "ORDER BY last_login_at DESC NULLS LAST LIMIT 200",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for u in out.splitlines():
            if u in mira_names:
                users["workhour"].add(u)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def add_requesters(opener, key: str, user_ids: list[int]) -> tuple[int, int]:
    detail = api(opener, "GET", f"/api/projects/{key}")
    by_user: dict[int, set[str]] = {}
    for member in detail.get("members") or []:
        by_user.setdefault(member["user"]["id"], set()).add(member["role"])
    added = 0
    for uid in user_ids:
        roles = by_user.get(uid, set())
        if "requester" not in roles:
            try:
                api(opener, "POST", f"/api/projects/{key}/members", {
                    "user_id": uid, "role": "requester",
                })
                roles = set(roles)
                roles.add("requester")
                by_user[uid] = roles
                added += 1
            except RuntimeError as exc:
                print(f"  skip member {uid} on {key}: {exc}")
                continue
        if "tester" not in by_user.get(uid, set()):
            try:
                api(opener, "POST", f"/api/projects/{key}/members", {
                    "user_id": uid, "role": "tester",
                })
                by_user.setdefault(uid, set()).add("tester")
            except RuntimeError as exc:
                print(f"  skip tester {uid} on {key}: {exc}")
    return added, sum(1 for roles in by_user.values() if "requester" in roles)


def ensure_requesters_are_testers(opener, key: str) -> int:
    """Give every requester the tester role so they can self-test."""
    detail = api(opener, "GET", f"/api/projects/{key}")
    by_user: dict[int, set[str]] = {}
    for member in detail.get("members") or []:
        by_user.setdefault(member["user"]["id"], set()).add(member["role"])
    need = [
        uid for uid, roles in by_user.items()
        if "requester" in roles and "tester" not in roles
    ]
    if not need:
        return 0
    api(opener, "PATCH", f"/api/projects/{key}/members/roles", {
        "user_ids": need,
        "add_roles": ["tester"],
    })
    print(f"  {key}: +tester for {len(need)} requester(s)")
    return len(need)


def ensure_service_project(
    opener,
    spec: dict,
    lead_id: int,
    service_ids: dict[str, int],
    usernames: set[str],
    mira_users: dict[str, int],
) -> None:
    key = spec["key"]
    projects = {p["key"]: p for p in api(opener, "GET", "/api/projects")["projects"]}
    if key not in projects:
        created = api(opener, "POST", "/api/projects", {
            "key": key,
            "name": spec["name"],
            "description": (
                f"Product project for {spec['name']} ({spec['code']}). "
                "Intake via requirement description; AI template."
            ),
            "color": spec["color"],
            "profile": "ai",
            "visibility": "members",
            "work_mode": "ongoing",
            "with_machines": False,
            "lead_id": lead_id,
        })
        print(f"created project {key} id={created.get('id')}")
    else:
        api(opener, "PUT", f"/api/projects/{key}", {
            "name": spec["name"],
            "description": (
                f"Product project for {spec['name']} ({spec['code']}). "
                "Intake via requirement description; AI template."
            ),
            "color": spec["color"],
            "lead_id": lead_id,
            "settings": {"work_mode": "ongoing", "visibility": "members"},
        })
        print(f"updated project {key}")

    detail = api(opener, "GET", f"/api/projects/{key}")
    admin_ids = {
        m["user"]["id"] for m in detail.get("members") or [] if m["role"] == "admin"
    }
    if lead_id not in admin_ids:
        api(opener, "POST", f"/api/projects/{key}/members", {
            "user_id": lead_id, "role": "admin",
        })

    # Bind service master asset when supported via project assets (optional)
    asset_id = service_ids.get(spec["code"])
    if asset_id:
        try:
            api(opener, "POST", f"/api/assets/project/{key}", {
                "asset_id": asset_id, "alias": spec["name"],
            })
        except RuntimeError:
            pass

    ids = sorted({mira_users[u] for u in usernames if u in mira_users})
    added, existing = add_requesters(opener, key, ids)
    print(f"  {key}: requesters +{added} (had {existing}), candidates={len(ids)}")


def ensure_analyst_process_requesters(opener) -> None:
    """Add every Process R&D org unit's users as ANALYST requesters."""
    projects = {p["key"] for p in api(opener, "GET", "/api/projects")["projects"]}
    if "ANALYST" not in projects:
        print("skip ANALYST process requesters: project missing")
        return
    tree = api(opener, "GET", "/api/org/tree")
    unit_ids = sorted(
        u["id"] for u in tree.get("units") or []
        if "Process R&D" in (u.get("path") or "")
    )
    if not unit_ids:
        print("skip ANALYST process requesters: no Process R&D units")
        return
    added = skipped = 0
    for unit_id in unit_ids:
        result = api(opener, "POST", "/api/projects/ANALYST/members/bulk", {
            "org_unit_id": unit_id,
            "role": "requester",
            "include_children": True,
        })
        added += int(result.get("added") or 0)
        skipped += int(result.get("skipped") or 0)
    print(
        f"ANALYST Process R&D requesters: +{added} (skipped {skipped}), "
        f"units={unit_ids}"
    )


def purge_unwanted_projects(opener) -> None:
    """Archive + hard-delete projects outside the AI portfolio catalog."""
    keep = {"AI"} | {row["key"] for row in SERVICE_PROJECTS}
    projects = api(opener, "GET", "/api/projects?include_archived=true")["projects"]
    for project in projects:
        key = project["key"]
        if key in keep or key not in DELETE_PROJECT_KEYS:
            continue
        if not project.get("archived"):
            api(opener, "PUT", f"/api/projects/{key}", {"archived": True})
        result = api(opener, "DELETE", f"/api/projects/{key}?confirm={key}")
        print(f"deleted unwanted project {key}: {result.get('deleted')}")


def resolve_user_id(opener, username: str) -> int:
    page = api(opener, "GET", f"/api/users?q={username}&limit=20")
    match = next(
        (u for u in page.get("users") or [] if u["username"].lower() == username.lower()),
        None,
    )
    if match is None:
        raise RuntimeError(f"user {username} not found")
    return match["id"]


def purge_pending_mail() -> None:
    url = (os.environ.get("DATABASE_URL") or os.environ.get("METJIRA_DATABASE_URL") or "")
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    if not url:
        print("no DATABASE_URL; skip outbox purge")
        return
    try:
        import psycopg
    except ImportError:
        print("psycopg missing; skip outbox purge")
        return
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM outbox_events WHERE kind = 'mail' "
                "AND status IN ('pending', 'failed', 'claimed', 'dead')"
            )
            print(f"purged outbox mail rows: {cur.rowcount}")
        conn.commit()


def main() -> int:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    api(opener, "POST", "/api/auth/login", {
        "username": ADMIN_USER, "password": ADMIN_PASSWORD,
    })
    portfolio_lead_id = resolve_user_id(opener, LEAD_USERNAME)
    print(f"portfolio lead {LEAD_USERNAME} id={portfolio_lead_id}")

    version_id = publish_template(opener)
    ensure_portfolio(opener, portfolio_lead_id, version_id)

    assets = {
        a["code"]: a["id"]
        for a in api(opener, "GET", "/api/assets?kind=service")["assets"]
    }
    mira_users = resolve_mira_users(opener)
    service_users = load_service_users()
    enrich_workhour(service_users, set(mira_users))

    # Seed each lead onto their product projects (+ cross-team on Mira / finance).
    for lead_name, specs in OWNER_PROJECTS.items():
        for spec in specs:
            service_users.setdefault(spec["code"], set()).add(lead_name)
    for code, names in {
        "mira": {"houcheng.fan", "ryan.fang", "zhiyu.wang", "jiatao.she"},
        "finance-invoice": {"houcheng.fan", "ryan.fang"},
        "finance-sap": {"ryan.fang", "jiatao.she"},
        "open-webui": {"ryan.fang"},
        "agent-runtime": {"ryan.fang"},
        "doclab": {"zhiyu.wang"},
        "ocr": {"zhiyu.wang"},
        "autotem": {"zhiyu.wang"},
        "data-analyst": {"zhiyu.wang"},
    }.items():
        service_users.setdefault(code, set()).update(names)

    for lead_name, specs in OWNER_PROJECTS.items():
        lead_id = resolve_user_id(opener, lead_name)
        print(f"owner {lead_name} id={lead_id} projects={len(specs)}")
        for spec in specs:
            if spec["code"] not in assets:
                print(f"skip {spec['key']}: service {spec['code']} not in catalog")
                continue
            ensure_service_project(
                opener, spec, lead_id, assets,
                service_users.get(spec["code"], set()),
                mira_users,
            )
            try:
                migrate_project(opener, spec["key"], version_id)
            except RuntimeError as exc:
                print(f"  migrate {spec['key']}: {exc}")

    ensure_analyst_process_requesters(opener)
    ai_keys = ["AI"] + [
        spec["key"] for specs in OWNER_PROJECTS.values() for spec in specs
    ]
    for key in ai_keys:
        try:
            ensure_requesters_are_testers(opener, key)
        except RuntimeError as exc:
            print(f"  requester→tester {key}: {exc}")
    purge_unwanted_projects(opener)
    purge_pending_mail()
    print("DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc, file=sys.stderr)
        raise
