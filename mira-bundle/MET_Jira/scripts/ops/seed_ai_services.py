#!/usr/bin/env python3
"""Seed 65/63 service master data, refresh AI template, link AI issues."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = os.environ.get("METJIRA_BASE", "http://127.0.0.1:8800")

# Product apps on host 65 only (63 edges / analysts are deleted — see DELETE_CODES).
SERVICES: list[dict] = [
    # Mira
    {"code": "mira", "name": "Mira", "host": "65", "port": 8800,
     "url": "http://10.113.17.65:8800/", "group": "mira"},
    # Hub / AI portal (ai-hub dropped — not a lucas production product project)
    {"code": "open-webui", "name": "Open WebUI", "host": "65", "port": 3000,
     "url": "http://10.113.17.65:3000/", "group": "ai"},
    {"code": "open-webui-sw", "name": "Open WebUI SW", "host": "65", "port": 8888,
     "url": "http://10.113.17.65:8888/", "group": "ai"},
    {"code": "workflow-builder", "name": "Workflow Builder", "host": "65", "port": 8530,
     "url": "http://10.113.17.65:8530/", "group": "ai"},
    {"code": "langfuse", "name": "Langfuse", "host": "65", "port": 3031,
     "url": "http://10.113.17.65:3031/", "group": "ai"},
    {"code": "agent-runtime", "name": "Agent Runtime", "host": "65", "port": None,
     "url": "", "group": "ai"},
    {"code": "vllm-deepseek", "name": "vLLM DeepSeek", "host": "65", "port": 18080,
     "url": "http://127.0.0.1:18080/", "group": "ai"},
    # Planning / office
    {"code": "planning", "name": "Planning", "host": "65", "port": 8515,
     "url": "http://10.113.17.65:8515/", "group": "planning"},
    {"code": "planning-beta", "name": "Planning Beta", "host": "65", "port": 8514,
     "url": "http://10.113.17.65:8514/", "group": "planning"},
    {"code": "critical-parts", "name": "Critical Parts", "host": "65", "port": 8600,
     "url": "http://10.113.17.65:8600/", "group": "planning"},
    {"code": "critical-parts-beta", "name": "Critical Parts Beta", "host": "65", "port": 8601,
     "url": "http://10.113.17.65:8601/", "group": "planning"},
    {"code": "workhour", "name": "工时填报 Workhour", "host": "65", "port": 8700,
     "url": "http://10.113.17.65:8700/", "group": "office"},
    {"code": "workhour-beta", "name": "工时填报 Beta", "host": "65", "port": 8701,
     "url": "http://10.113.17.65:8701/", "group": "office"},
    {"code": "bom-builder", "name": "BOM 搭建", "host": "65", "port": 8801,
     "url": "http://10.113.17.65:8801/", "group": "engineering"},
    {"code": "bom-builder-beta", "name": "BOM Builder Beta", "host": "65", "port": 8511,
     "url": "http://10.113.17.65:8511/", "group": "engineering"},
    # Drawing / metrology
    {"code": "filechecker", "name": "FileChecker", "host": "65", "port": 18000,
     "url": "http://10.113.17.65:18000/", "group": "engineering"},
    {"code": "ai-review", "name": "AI 审图 / Drawing Review", "host": "65", "port": 8100,
     "url": "http://10.113.17.65:8100/", "group": "engineering"},
    {"code": "interlock-maker", "name": "Interlock Maker", "host": "65", "port": 8787,
     "url": "http://10.113.17.65:8787/", "group": "engineering"},
    {"code": "metrology", "name": "Metrology Web", "host": "65", "port": 8120,
     "url": "http://10.113.17.65:8120/", "group": "metrology"},
    {"code": "foup-qr", "name": "FOUP QR / Metrology QR", "host": "65", "port": 5173,
     "url": "http://10.113.17.65:5173/", "group": "metrology"},
    # Finance / docs / quality
    {"code": "finance-invoice", "name": "Finance Invoice", "host": "65", "port": 5179,
     "url": "http://10.113.17.65:5179/", "group": "finance"},
    {"code": "finance-sap", "name": "Finance SAP / AI Hub DB", "host": "65", "port": None,
     "url": "", "group": "finance"},
    {"code": "doclab", "name": "DocLab / RAGFlow", "host": "65", "port": 9382,
     "url": "http://10.113.17.65:9382/", "group": "docs"},
    {"code": "ocr", "name": "OCR Tool", "host": "65", "port": 8020,
     "url": "http://10.113.17.65:8020/", "group": "docs"},
    {"code": "quality-coa", "name": "QMS / Quality COA", "host": "65", "port": 8022,
     "url": "http://10.113.17.65:8022/", "group": "quality",
     "note": "micropolaris-quality-tool; COA → QMS template"},
    {"code": "homepage", "name": "Homepage", "host": "65", "port": None,
     "url": "", "group": "office"},
    {"code": "autotem", "name": "AutoTEM", "host": "65", "port": 8089,
     "url": "http://10.113.17.65:8089/", "group": "ai"},
    # 65 host-network beta of the analyst stack (anomaly / recipe analytics).
    {"code": "data-analyst", "name": "Data Analyst", "host": "65", "port": 8501,
     "url": "http://10.113.17.65:8501/", "group": "analytics",
     "note": "data-analyst-beta on 65; anomaly detection / recipe analytics"},
]

# Deleted outright: staging/ops sidecars + host-63 edges/analysts (65 data-analyst kept).
DELETE_CODES = {
    "mira-staging",
    "mira-ops",
    "filechecker-staging",
    "metrology-staging",
    "metrology-ops",
    "ai-hub",
    "ai-hub-proxy",
    "filechecker-63",
    "metrology-63",
    "analyst-gamma",
    "analyst-cvd",
    "analyst-etch",
    "analyst-pvd",
}

# Title substring / exact → service codes (prefer primary prod apps, not betas).
ISSUE_LINKS: list[tuple[str, list[str]]] = [
    ("AutoTEM", ["autotem"]),
    ("Mira", ["mira"]),
    ("工时填报", ["workhour"]),
    ("BOM", ["bom-builder"]),
    ("Agent Runtime", ["agent-runtime", "open-webui"]),
    ("DocLab", ["doclab", "ocr"]),
    ("Finance AI Hub", ["finance-sap", "finance-invoice"]),
    ("SAP数据库打通", ["finance-sap"]),
    ("财务报告", ["finance-sap", "finance-invoice"]),
    ("机械审图", ["ai-review", "filechecker"]),
    ("QR Code", ["foup-qr", "metrology"]),
    ("QMS问题平台", ["quality-coa"]),
    ("QMS", ["quality-coa"]),
    ("IQC", ["quality-coa"]),
    ("异常检测", ["data-analyst"]),
    ("System Architecture", ["agent-runtime"]),
    ("Budget Planning", ["finance-sap", "planning"]),
    ("PDM", ["doclab"]),
]


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


def seed_assets(opener) -> dict[str, int]:
    existing = {
        a["code"]: a
        for a in api(opener, "GET", "/api/assets?kind=service&include_inactive=true")["assets"]
    }
    ids: dict[str, int] = {}
    for row in SERVICES:
        attrs = {
            "host": row["host"],
            "port": row.get("port"),
            "url": row.get("url") or "",
            "group": row.get("group") or "",
        }
        if row.get("note"):
            attrs["note"] = row["note"]
        payload = {
            "kind": "service",
            "code": row["code"],
            "name": row["name"],
            "active": True,
            "attributes": attrs,
        }
        if row["code"] in existing:
            asset = existing[row["code"]]
            api(opener, "PUT", f"/api/assets/{asset['id']}", payload)
            ids[row["code"]] = asset["id"]
            print(f"updated service {row['code']}")
        else:
            created = api(opener, "POST", "/api/assets", payload)
            ids[row["code"]] = created["id"]
            print(f"created service {row['code']} id={created['id']}")
    return ids


def delete_sidecars(opener) -> set[int]:
    """Remove staging/ops/host-63 services from the catalog and scrub issue references."""
    existing = {
        a["code"]: a
        for a in api(opener, "GET", "/api/assets?kind=service&include_inactive=true")["assets"]
    }
    removed_ids: set[int] = set()
    removed_codes: set[str] = set()
    for code, asset in existing.items():
        host = str((asset.get("attributes") or {}).get("host") or "")
        if code in DELETE_CODES or host == "63":
            removed_ids.add(int(asset["id"]))
            removed_codes.add(code)
    if not removed_ids:
        return removed_ids

    # Scrub AI issue custom field values that still point at deleted ids.
    data = api(opener, "GET", "/api/search?jql=project=AI&limit=500")
    for issue in data.get("issues") or []:
        current = list((issue.get("custom_fields") or {}).get("related_services") or [])
        cleaned = [i for i in current if i not in removed_ids]
        if cleaned == current:
            continue
        try:
            _patch_services(opener, issue, cleaned)
            print(f"scrubbed {issue['key']} related_services")
        except RuntimeError as exc:
            print(f"skip scrub {issue['key']}: {exc}")

    # Hard-delete via SQL helper endpoint is unavailable — use local DB when configured.
    deleted = _delete_assets_sql(removed_ids)
    if deleted:
        print(f"deleted services from db: {sorted(removed_codes)}")
    else:
        # Fallback: leave inactive only if SQL unavailable (should not happen on 65).
        for code in removed_codes:
            asset = existing.get(code)
            if not asset:
                continue
            api(opener, "PUT", f"/api/assets/{asset['id']}", {
                "kind": "service",
                "code": asset["code"],
                "name": asset["name"],
                "active": False,
                "attributes": asset.get("attributes") or {},
            })
            print(f"WARN could not SQL-delete, deactivated {code}")
    return removed_ids


def _delete_assets_sql(asset_ids: set[int]) -> bool:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("METJIRA_DATABASE_URL") or ""
    if not raw or not asset_ids:
        return False
    url = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        import psycopg
    except ImportError:
        return False
    ids = sorted(asset_ids)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM project_assets WHERE asset_id = ANY(%s)", (ids,))
            cur.execute("DELETE FROM master_assets WHERE id = ANY(%s)", (ids,))
        conn.commit()
    return True


def refresh_ai_template(opener) -> int:
    """Return published AI template version id; do not republish (use provision script)."""
    templates = api(opener, "GET", "/api/admin/templates")["templates"]
    tpl = next(t for t in templates if t["key"] == "ai")
    detail = api(opener, "GET", f"/api/admin/templates/{tpl['id']}")
    version_id = detail["current_version"]["id"]
    fields = {f["key"] for f in detail["current_version"]["definition"].get("fields", [])}
    statuses = [s["name"] for s in detail["current_version"]["definition"].get("statuses", [])]
    print(
        f"ai template v{detail['current_version']['version']} "
        f"fields={sorted(fields)} statuses={statuses}"
    )
    if "related_services" not in fields:
        raise RuntimeError(
            "AI template missing related_services — run provision_ai_portfolio.py first"
        )
    return version_id


def migrate_ai_project(opener, version_id: int) -> None:
    detail = api(opener, "GET", "/api/projects/AI")
    project = detail["project"]
    current = project.get("template_version_id")
    if current == version_id:
        print("AI project already on latest template version")
        return
    print(
        f"AI project template_version_id={current} != {version_id}; "
        "skip migrate (use provision_ai_portfolio.py)"
    )

def _patch_services(opener, issue: dict, ids: list[int]) -> None:
    current = (issue.get("custom_fields") or {}).get("related_services")
    if current == ids:
        return
    patch: dict = {"custom_fields": {"related_services": ids}}
    if issue.get("version") is not None:
        patch["expected_version"] = issue["version"]
    api(opener, "PATCH", f"/api/issues/{issue['key']}", patch)


def link_issues(opener, service_ids: dict[str, int]) -> None:
    data = api(opener, "GET", "/api/search?jql=project=AI&limit=500")
    issues = data.get("issues") or []
    for issue in issues:
        title = issue.get("title") or ""
        codes: list[str] = []
        for needle, matched in ISSUE_LINKS:
            if needle.lower() in title.lower():
                codes.extend(matched)
        codes = list(dict.fromkeys(c for c in codes if c not in DELETE_CODES))
        current = list((issue.get("custom_fields") or {}).get("related_services") or [])
        if not codes:
            continue
        ids = [service_ids[c] for c in codes if c in service_ids]
        if ids == current:
            continue
        try:
            _patch_services(opener, issue, ids)
            print(f"linked {issue['key']} -> {codes}")
        except RuntimeError as exc:
            print(f"skip link {issue['key']}: {exc}")


def main() -> int:
    user = os.environ["METJIRA_ADMIN_USER"]
    password = os.environ["METJIRA_ADMIN_PASSWORD"]
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    api(opener, "POST", "/api/auth/login", {"username": user, "password": password})
    sys.path.insert(0, str(__file__).rsplit("/", 1)[0])
    ids = seed_assets(opener)
    delete_sidecars(opener)
    version_id = refresh_ai_template(opener)
    migrate_ai_project(opener, version_id)
    link_issues(opener, ids)
    print(f"DONE active_services={len(SERVICES)} deleted={len(DELETE_CODES)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", exc, file=sys.stderr)
        raise
