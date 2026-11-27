#!/usr/bin/env python3
"""Create AI department template + ongoing project and import To-dos Work Plan."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path

import openpyxl

BASE = os.environ.get("METJIRA_BASE", "http://127.0.0.1:8800")
XLSX = Path(os.environ.get(
    "METJIRA_TODOS_XLSX",
    "/tmp/mira-todos/To-dos.xlsx",
))
SAP_URL = (
    "https://doc.weixin.qq.com/sheet/e3_AWgAZwa3ABQCNb8iZTgzzQ9ujQUr8"
    "?scode=APkAtAebAFYZSpMJISAWgAZwa3ABQ"
)

OWNERS = {
    "zhiyu wang": "zhiyu.wang",
    "houcheng fan": "houcheng.fan",
    "ryan fang": "ryan.fang",
    "jiatao she": "jiatao.she",
}
PRIORITY = {"高": "high", "中": "medium", "低": "low"}
PROGRESS = {
    "已完成": "已完成",
    "进行中": "进行中",
    "已延期": "已延期",
    "未开展": "未开展",
    "已暂停": "已暂停",
    "": "未开展",
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
        raise RuntimeError(f"{method} {path} -> {exc.code}: {err[:1200]}") from exc


def parse_cn_date(raw) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def build_definition() -> dict:
    from backend.services.ai_template import build_ai_definition

    return build_ai_definition()


def load_rows() -> list[dict]:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["Work Plan"] if "Work Plan" in wb.sheetnames else wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        title = (row[0] or "").strip() if isinstance(row[0], str) else str(row[0] or "").strip()
        if not title:
            continue
        files = (row[8] or "").strip() if row[8] else ""
        ref = SAP_URL if files and "SAP" in files else (files or "")

        def cell(idx: int, values=row) -> str:
            value = values[idx]
            if isinstance(value, str):
                return value.strip()
            return str(value or "").strip()

        rows.append({
            "row": i,
            "title": title,
            "description": cell(1),
            "priority": PRIORITY.get((row[2] or "").strip() if row[2] else "", "medium"),
            "progress": PROGRESS.get((row[3] or "").strip() if row[3] else "", "未开展"),
            "as_of": parse_cn_date(row[4]),
            "start": parse_cn_date(row[5]),
            "etc": parse_cn_date(row[6]),
            "blocker": cell(7),
            "ref_url": ref,
            "owner": (row[9] or "").strip() if row[9] else "",
            "parent_title": cell(10),
        })
    return rows


def resolve_users(opener) -> dict[str, int]:
    data = api(opener, "GET", "/api/admin/users?limit=1000")
    by_username = {u["username"].lower(): u["id"] for u in data["users"]}
    out = {}
    for display, username in OWNERS.items():
        uid = by_username.get(username)
        if not uid:
            raise RuntimeError(f"missing user {username} for owner {display}")
        out[display] = uid
        out[username] = uid
    return out


def ensure_template(opener) -> dict:
    templates = api(opener, "GET", "/api/admin/templates")["templates"]
    existing = next((t for t in templates if t["key"] == "ai"), None)
    if existing is None:
        tpl = api(opener, "POST", "/api/admin/templates", {
            "key": "ai",
            "label": "AI组",
            "label_en": "AI team",
            "description": "AI组持续运维 / 工作计划模板（对应 To-dos Work Plan）",
        })
        print(f"created template id={tpl['id']}")
    else:
        tpl = api(opener, "GET", f"/api/admin/templates/{existing['id']}")
        print(f"template exists id={tpl['id']}")

    draft = next((v for v in tpl["versions"] if v["status"] == "draft"), None)
    definition = build_definition()
    if draft is None:
        current = tpl.get("current_version")
        if current and current.get("definition") == definition:
            print("template already published with exact AI definition; keeping")
            return tpl
        draft = api(opener, "POST", f"/api/admin/templates/{tpl['id']}/versions", {})
        print(f"created draft v{draft['version']}")
    saved = api(opener, "PUT", f"/api/admin/templates/{tpl['id']}/versions/{draft['id']}", {
        "revision": draft["revision"],
        "definition": definition,
    })
    if saved.get("validation_errors"):
        raise RuntimeError(f"invalid definition: {saved['validation_errors']}")
    publish_path = f"/api/admin/templates/{tpl['id']}/versions/{saved['id']}/publish"
    pub = api(opener, "POST", publish_path, {})
    print(f"published ai template v{pub['current_version']['version']}")
    return pub


def ensure_project(opener, lead_id: int) -> dict:
    projects = api(opener, "GET", "/api/projects")["projects"]
    proj = next((p for p in projects if p["key"] == "AI"), None)
    if proj is None:
        proj = api(opener, "POST", "/api/projects", {
            "key": "AI",
            "name": "AI Portfolio",
            "description": (
                "Internal AI-team portfolio: cross-product epics and intake. "
                "Per-service delivery lives in dedicated projects."
            ),
            "color": "#5e5ce6",
            "profile": "ai",
            "visibility": "members",
            "work_mode": "ongoing",
            "with_machines": False,
            "lead_id": lead_id,
        })
        print(f"created project AI id={proj['id']}")
    else:
        print(f"project AI exists id={proj['id']}")
        # Keep project workflows aligned with the AI template definition.
        settings = dict(proj.get("settings") or {})
        workflows = build_definition()["workflows"]
        if settings.get("work_mode") != "ongoing" or settings.get("transitions") != workflows:
            api(opener, "PUT", f"/api/projects/{proj['key']}", {
                "settings": {
                    "work_mode": "ongoing",
                    "transitions": workflows,
                },
            })
            print("synced project AI workflows")
    return api(opener, "GET", f"/api/projects/{proj['key']}")


def add_members(opener, project_key: str, user_ids: list[int]) -> None:
    detail = api(opener, "GET", f"/api/projects/{project_key}")
    rows = detail.get("members") or []
    existing = set()
    for m in rows:
        uid = None
        if isinstance(m.get("user"), dict):
            uid = m["user"].get("id")
        if uid is not None:
            existing.add(uid)
    for uid in user_ids:
        if uid in existing:
            continue
        api(opener, "POST", f"/api/projects/{project_key}/members", {
            "user_id": uid,
            "role": "owner",
        })
        print(f"added member {uid} as owner")


def status_map(detail: dict) -> dict[str, int]:
    statuses = detail.get("statuses") or []
    return {s["name"]: s["id"] for s in statuses}


def list_project_issues(opener, project_key: str) -> list[dict]:
    data = api(opener, "GET", f"/api/search?jql=project={project_key}&limit=500")
    return data.get("issues") or data.get("items") or []


def existing_titles(opener, project_key: str) -> set[str]:
    return {i.get("title") for i in list_project_issues(opener, project_key) if i.get("title")}


def create_issue(opener, payload: dict) -> dict:
    return api(opener, "POST", "/api/issues", payload)


def patch_issue(opener, key: str, changes: dict) -> dict:
    return api(opener, "PATCH", f"/api/issues/{key}", changes)


def main() -> int:
    user = os.environ["METJIRA_ADMIN_USER"]
    password = os.environ["METJIRA_ADMIN_PASSWORD"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    me = api(opener, "POST", "/api/auth/login", {"username": user, "password": password})
    lead_id = me["user"]["id"]
    print("logged in", me["user"]["username"], lead_id)

    ensure_template(opener)
    detail = ensure_project(opener, lead_id)
    proj = detail["project"] if "project" in detail else detail
    project_key = proj["key"]
    print(
        "project", project_key,
        "work_mode", (proj.get("settings") or {}).get("work_mode"),
        "profile", (proj.get("settings") or {}).get("profile"),
    )

    users = resolve_users(opener)
    owner_ids = sorted({
        users[k] for k in ("zhiyu.wang", "houcheng.fan", "ryan.fang", "jiatao.she")
    })
    add_members(opener, project_key, owner_ids)

    detail = api(opener, "GET", f"/api/projects/{project_key}")
    smap = status_map(detail)
    print("statuses", smap)
    if "未开展" not in smap or "已完成" not in smap:
        raise RuntimeError(f"AI statuses missing: {smap}")

    rows = load_rows()
    parent_titles = {r["parent_title"] for r in rows if r["parent_title"]}
    print(f"loaded {len(rows)} rows; parents with children: {sorted(parent_titles)}")

    already = existing_titles(opener, project_key)
    if already & {r["title"] for r in rows}:
        print(
            f"note: {len(already & {r['title'] for r in rows})} titles already in project; "
            "will skip duplicates"
        )

    created: dict[str, dict] = {}

    def current_status_id(issue: dict) -> int | None:
        status = issue.get("status")
        if isinstance(status, dict):
            return status.get("id")
        return issue.get("status_id")

    def move_status(issue: dict, progress: str) -> dict:
        target = smap[progress]
        if current_status_id(issue) == target:
            return issue
        patch = {"status_id": target}
        if issue.get("version") is not None:
            patch["expected_version"] = issue["version"]
        return patch_issue(opener, issue["key"], patch)

    # Phase 1: epics / top-level without parent
    for row in rows:
        if row["parent_title"]:
            continue
        if row["title"] in already:
            print(f"skip existing {row['title']}")
            continue
        issue_type = "epic" if row["title"] in parent_titles else "task"
        due = row["etc"] or row["as_of"] or date(2026, 12, 31)
        assignee = users.get(row["owner"].lower())
        custom = {}
        if row["blocker"]:
            custom["blocker"] = row["blocker"]
        if row["ref_url"]:
            custom["ref_url"] = row["ref_url"]
        payload = {
            "project": project_key,
            "type": issue_type,
            "title": row["title"],
            "description": row["description"] or None,
            "priority": row["priority"],
            "due_date": due.isoformat(),
            "assignee_id": assignee,
            "custom_fields": custom or None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        issue = create_issue(opener, payload)
        issue = move_status(issue, row["progress"])
        created[row["title"]] = issue
        print(f"created {issue['key']} [{issue_type}] {row['title']} -> {row['progress']}")

    for issue in list_project_issues(opener, project_key):
        title = issue.get("title")
        if title in parent_titles and title not in created:
            created[title] = issue

    # Phase 2: children under epics
    for row in rows:
        if not row["parent_title"]:
            continue
        if row["title"] in already:
            print(f"skip existing {row['title']}")
            continue
        parent = created.get(row["parent_title"])
        if parent is None:
            raise RuntimeError(f"missing parent epic for {row['title']}: {row['parent_title']}")
        due = row["etc"] or row["as_of"] or date(2026, 12, 31)
        assignee = users.get(row["owner"].lower())
        custom = {}
        if row["blocker"]:
            custom["blocker"] = row["blocker"]
        if row["ref_url"]:
            custom["ref_url"] = row["ref_url"]
        payload = {
            "project": project_key,
            "type": "task",
            "title": row["title"],
            "description": row["description"] or None,
            "priority": row["priority"],
            "due_date": due.isoformat(),
            "assignee_id": assignee,
            "epic_id": parent.get("id"),
            "custom_fields": custom or None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        issue = create_issue(opener, payload)
        issue = move_status(issue, row["progress"])
        print(
            f"created {issue['key']} [task] {row['title']} under {parent['key']} "
            f"-> {row['progress']}"
        )

    print("DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc, file=sys.stderr)
        raise
