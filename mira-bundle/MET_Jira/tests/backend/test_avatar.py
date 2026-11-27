from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image


def _png(width: int, height: int, color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def test_upload_normalizes_and_serves_avatar(admin, make_user):
    response = admin.post("/api/auth/me/avatar",
                          files={"file": ("me.png", _png(640, 400), "image/png")})
    assert response.status_code == 200, response.text
    url = response.json()["user"]["avatar_url"]
    assert url.startswith("/api/users/1/avatar?v=")

    other, _ = make_user("viewer")
    image = other.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.size == (256, 256)

    # Avatars show up wherever user briefs are serialized.
    people = other.get("/api/users", params={"q": "admin"}).json()
    briefs = people if isinstance(people, list) else people.get("users", people.get("items", []))
    assert any(u.get("avatar_url") == url for u in briefs)

    replaced = admin.post("/api/auth/me/avatar",
                          files={"file": ("me.png", _png(100, 100), "image/png")})
    new_url = replaced.json()["user"]["avatar_url"]
    assert new_url != url
    stored = Path(os.environ["METJIRA_DATA_DIR"]) / "avatars"
    assert [p.name for p in stored.iterdir()] == [f"1-{new_url.rsplit('=', 1)[1]}.webp"]

    cleared = admin.delete("/api/auth/me/avatar").json()["user"]
    assert cleared["avatar_url"] is None
    assert admin.get(new_url).status_code == 404
    assert list(stored.iterdir()) == []


def test_avatar_rejects_non_images_and_requires_login(app, admin):
    bad = admin.post("/api/auth/me/avatar",
                     files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")})
    assert bad.status_code == 400

    from fastapi.testclient import TestClient

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/users/1/avatar").status_code == 401


def test_avatar_color(admin):
    ok = admin.put("/api/auth/me/avatar-color", json={"color": "#5E5CE6"})
    assert ok.json()["user"]["avatar_color"] == "#5e5ce6"
    assert admin.put("/api/auth/me/avatar-color", json={"color": "red;"}).status_code == 400
    reset = admin.put("/api/auth/me/avatar-color", json={"color": None})
    assert reset.json()["user"]["avatar_color"] is None
