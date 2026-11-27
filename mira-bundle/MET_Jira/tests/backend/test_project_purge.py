from __future__ import annotations


def _setup(admin) -> dict:
    assert admin.post("/api/projects", json={
        "key": "GONE", "name": "待删除", "profile": "electrical",
    }).status_code == 200
    parent = admin.post("/api/issues", json={
        "project": "GONE", "title": "父任务", "type": "task", "due_date": "2099-01-01",
    }).json()
    assert admin.post(f"/api/issues/{parent['key']}/comments",
                      json={"body": "评论"}).status_code == 200
    upload = admin.post(f"/api/issues/{parent['key']}/attachments",
                        files={"file": ("a.txt", b"hello", "text/plain")})
    assert upload.status_code == 200, upload.text
    outside = admin.post("/api/issues", json={
        "project": "ELEC", "title": "外部问题", "due_date": "2099-01-01",
    }).json()
    return {"parent": parent, "outside": outside}


def test_archived_list_and_restore(admin, make_user):
    _setup(admin)
    assert admin.put("/api/projects/GONE", json={"archived": True}).status_code == 200
    active = admin.get("/api/projects").json()["projects"]
    assert "GONE" not in {p["key"] for p in active}
    everything = admin.get("/api/projects?include_archived=true").json()["projects"]
    gone = next(p for p in everything if p["key"] == "GONE")
    assert gone["archived"] and gone["can_admin"]
    assert gone["settings"]["archived_by"] and gone["settings"]["archived_at"]
    member, _ = make_user("viewer", "普通用户")
    listed = member.get("/api/projects?include_archived=true").json()["projects"]
    assert next(p for p in listed if p["key"] == "GONE")["can_admin"] is False
    assert member.put("/api/projects/GONE", json={"archived": False}).status_code == 403
    restored = admin.put("/api/projects/GONE", json={"archived": False}).json()
    assert restored["archived"] is False and restored["settings"]["archived_at"] is None


def test_permanent_delete_rules_and_cleanup(admin, make_user, app):
    from backend.config import get_settings

    data = _setup(admin)
    files = list(get_settings().attachments_dir.iterdir())
    assert files
    member, _ = make_user("someone", "某人")

    assert admin.delete("/api/projects/GONE?confirm=GONE").status_code == 409
    assert admin.put("/api/projects/GONE", json={"archived": True}).status_code == 200
    assert member.delete("/api/projects/GONE?confirm=GONE").status_code == 403
    assert admin.delete("/api/projects/GONE?confirm=ELEC").status_code == 400
    counts = admin.get("/api/projects/GONE/purge-preview").json()["counts"]
    assert (counts["issues"], counts["comments"], counts["attachments"]) == (1, 1, 1)
    assert counts["boards"] >= 1

    deleted = admin.delete("/api/projects/GONE?confirm=gone")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["issues"] == 1
    assert deleted.json()["deleted"]["attachments"] == 1
    assert admin.get("/api/projects/GONE").status_code == 404
    assert admin.get(f"/api/issues/{data['parent']['key']}").status_code == 404
    assert admin.get(f"/api/issues/{data['outside']['key']}").status_code == 200
    assert not any(f.exists() for f in files)
    everything = admin.get("/api/projects?include_archived=true").json()["projects"]
    assert "GONE" not in {p["key"] for p in everything}
