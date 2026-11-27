from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image


def _png(width: int, height: int, color=(30, 120, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def test_upload_serves_and_clears_project_cover(admin):
    response = admin.post(
        "/api/projects/ELEC/cover",
        files={"file": ("cover.png", _png(1200, 500), "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    url = body["cover_url"]
    assert url.startswith("/api/projects/ELEC/cover?v=")

    image = admin.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (1600, 480)

    detail = admin.get("/api/projects/ELEC").json()["project"]
    assert detail["cover_url"] == url

    stored = Path(os.environ["METJIRA_DATA_DIR"]) / "covers"
    assert list(stored.iterdir())

    cleared = admin.delete("/api/projects/ELEC/cover").json()
    assert cleared["cover_url"] is None
    assert admin.get(url).status_code == 404
    assert list(stored.iterdir()) == []


def test_cover_requires_project_admin(admin, make_user):
    member, member_id = make_user("cover-member")
    assert admin.post(
        "/api/projects/ELEC/members",
        json={"user_id": member_id, "role": "member"},
    ).status_code == 200
    denied = member.post(
        "/api/projects/ELEC/cover",
        files={"file": ("cover.png", _png(800, 400), "image/png")},
    )
    assert denied.status_code in (403, 404)


def test_cover_preset_list_and_apply(admin):
    listed = admin.get("/api/cover-presets")
    assert listed.status_code == 200, listed.text
    presets = listed.json()["presets"]
    assert {p["id"] for p in presets} >= {"wafer", "traces", "layers", "fab"}

    thumb = admin.get("/api/cover-presets/wafer")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/")

    applied = admin.put("/api/projects/ELEC/cover-preset", json={"preset": "wafer"})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["cover_preset"] == "wafer"
    assert body["cover_url"].startswith("/api/projects/ELEC/cover?v=preset-wafer")

    image = admin.get(body["cover_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (1600, 480)

    bad = admin.put("/api/projects/ELEC/cover-preset", json={"preset": "nope"})
    assert bad.status_code == 400

    custom = admin.post(
        "/api/projects/ELEC/cover",
        files={"file": ("cover.png", _png(1200, 500), "image/png")},
    )
    assert custom.status_code == 200
    assert custom.json()["cover_preset"] is None
