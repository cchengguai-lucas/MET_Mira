"""Startup credentials + attachment inline hardening."""

from __future__ import annotations

import pytest


def test_production_rejects_default_secret(tmp_path, monkeypatch):
    from backend import config

    monkeypatch.setenv("METJIRA_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("METJIRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("METJIRA_DEPLOY_ENV", "production")
    monkeypatch.setenv("METJIRA_SECRET", "met-jira-dev-secret-change-me")
    monkeypatch.setenv("METJIRA_ADMIN_PASSWORD", "unique-admin-pass")
    monkeypatch.setattr(config, "_DOTENV", {})
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="METJIRA_SECRET"):
        config.get_settings()
    config.get_settings.cache_clear()


def test_inline_html_attachment_forced_download(admin):
    created = admin.post("/api/issues", json={
        "project": "ELEC",
        "type": "requirement",
        "title": "附件安全",
        "due_date": "2099-12-31",
    })
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    upload = admin.post(
        f"/api/issues/{key}/attachments",
        files={"file": ("note.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert upload.status_code == 200, upload.text
    att = upload.json()
    response = admin.get(f"{att['url']}?inline=true")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "attachment" in response.headers.get("content-disposition", "").lower()
    assert response.headers.get("x-content-type-options") == "nosniff"

    png = admin.post(
        f"/api/issues/{key}/attachments",
        files={"file": ("ok.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    )
    assert png.status_code == 200, png.text
    inline = admin.get(f"{png.json()['url']}?inline=true")
    assert inline.status_code == 200
    assert inline.headers["content-type"].startswith("image/png")
    assert "inline" in inline.headers.get("content-disposition", "").lower()


def test_office_and_pdf_attachments_upload_and_download(admin):
    created = admin.post("/api/issues", json={
        "project": "ELEC",
        "type": "requirement",
        "title": "办公附件",
        "due_date": "2099-12-31",
    })
    assert created.status_code == 200, created.text
    key = created.json()["key"]
    samples = [
        ("spec.pdf", b"%PDF-1.4 fake", "application/pdf", "application/pdf", True),
        (
            "notes.docx",
            b"PK\x03\x04fake-docx",
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            False,
        ),
        (
            "data.xlsx",
            b"PK\x03\x04fake-xlsx",
            "",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            False,
        ),
    ]
    for name, body, declared, expected_type, can_inline in samples:
        upload = admin.post(
            f"/api/issues/{key}/attachments",
            files={"file": (name, body, declared)},
        )
        assert upload.status_code == 200, upload.text
        att = upload.json()
        assert att["filename"] == name
        assert att["content_type"] == expected_type
        assert att["size"] == len(body)

        downloaded = admin.get(att["url"])
        assert downloaded.status_code == 200
        assert downloaded.content == body
        assert downloaded.headers["content-type"].startswith("application/octet-stream")
        assert "attachment" in downloaded.headers.get("content-disposition", "").lower()

        preview = admin.get(f"{att['url']}?inline=true")
        assert preview.status_code == 200
        if can_inline:
            assert preview.headers["content-type"].startswith("application/pdf")
            assert "inline" in preview.headers.get("content-disposition", "").lower()
        else:
            assert preview.headers["content-type"].startswith("application/octet-stream")
            assert "attachment" in preview.headers.get("content-disposition", "").lower()
