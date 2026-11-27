from __future__ import annotations

from backend.services import profiles


def test_project_detail_serves_electrical_profile(admin):
    project = admin.get("/api/projects/ELEC").json()
    profile = project["profile"]
    assert profile["key"] == "electrical"
    assert [s["key"] for s in profile["sections"]] == [
        "requester", "scheme", "electrical", "software", "tester",
    ]
    assert "process" in profile["roles"]
    scheme = next(section for section in profile["sections"] if section["key"] == "scheme")
    assert "scheme_note" in scheme["fields"]
    electrical = next(section for section in profile["sections"] if section["key"] == "electrical")
    assert electrical["role"] == "electrical"
    assert set(electrical["issue_users"]) == {"assignee_id", "software_owner_id"}
    assert "verify_method" in electrical["fields"]
    assert "status_id" in profile["general_fields"]
    assert project["roles"] == profile["roles"]
    catalog = admin.get("/api/projects/profile-catalog").json()["profiles"]
    assert [item["key"] for item in catalog] == ["electrical", "mechanical", "production"]


def test_every_field_belongs_to_one_section():
    for profile in profiles.PROFILES.values():
        claimed = [f for s in profile.sections for f in s.fields] + list(profiles.GENERAL_FIELDS)
        assert len(claimed) == len(set(claimed)), profile.key
        assert profile.field_section("title") == "requester"
        assert profile.field_section("not_a_field") == profiles.GENERAL


def test_unknown_profile_is_rejected(admin):
    response = admin.put("/api/projects/ELEC", json={"settings": {"profile": "nope"}})
    assert response.status_code == 400
    assert admin.put("/api/projects/ELEC",
                     json={"settings": {"profile": "electrical"}}).status_code == 200
