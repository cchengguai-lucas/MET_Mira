from __future__ import annotations

import pytest

from ops_console import auth, ldap_auth
from ops_console.org import (
    LOCAL,
    UNASSIGNED,
    annotate,
    headcount,
    issues_by_dept,
    split_path,
    usage_by_dept,
)

PEOPLE = {
    1: {"name": "Admin", "enabled": True, "ad": False, "dept": LOCAL, "team": LOCAL},
    2: {"name": "A", "enabled": True, "ad": True, "dept": "ETCH", "team": "ETCH / ICP / Product"},
    3: {"name": "B", "enabled": True, "ad": True, "dept": "ETCH", "team": "ETCH / RF"},
    4: {"name": "C", "enabled": False, "ad": True, "dept": "ETCH", "team": "ETCH / RF"},
    5: {"name": "D", "enabled": True, "ad": True, "dept": "Software", "team": "Software / IT"},
}


def test_split_path_uses_first_level_below_root():
    assert split_path("MET / ETCH / ICP / Product") == ("ETCH", "ETCH / ICP / Product")
    assert split_path("MET / Electrical Engineering") == (
        "Electrical Engineering", "Electrical Engineering")
    assert split_path("MET") == ("MET", "MET")
    assert split_path(None) == (UNASSIGNED, UNASSIGNED)


def test_headcount_counts_enabled_ad_accounts_only():
    assert headcount(PEOPLE) == {"ETCH": 2, "Software": 1}


def test_usage_by_dept_aggregates_and_computes_coverage():
    totals = [
        {"user_id": 2, "seconds": 600},
        {"user_id": 5, "seconds": 300},
        {"user_id": 1, "seconds": 100},
        {"user_id": 99, "seconds": 50},
    ]
    rows = {r["dept"]: r for r in usage_by_dept(totals, PEOPLE)}
    assert rows["ETCH"] == {"dept": "ETCH", "users": 1, "seconds": 600, "headcount": 2,
                            "coverage": 0.5}
    assert rows["Software"]["coverage"] == 1.0
    assert rows[LOCAL]["coverage"] is None
    assert rows[UNASSIGNED]["seconds"] == 50
    assert [r["dept"] for r in usage_by_dept(totals, PEOPLE)][0] == "ETCH"


def test_issues_by_dept_and_annotate():
    reporters = [
        {"user_id": 2, "total": 4, "open": 3, "recent": 1},
        {"user_id": 3, "total": 2, "open": 0, "recent": 2},
        {"user_id": 5, "total": 1, "open": 1, "recent": 0},
    ]
    rows = issues_by_dept(reporters, PEOPLE)
    assert rows[0] == {"dept": "ETCH", "total": 6, "open": 3, "recent": 3, "people": 2}
    seats = annotate([{"user_id": 3}, {"user_id": 42}], PEOPLE)
    assert [s["team"] for s in seats] == ["ETCH / RF", None]


def test_ad_allowed_by_allowlist_or_admin():
    admin = {"auth_source": "ldap", "is_admin": True, "enabled": True}
    member = {"auth_source": "ldap", "is_admin": False, "enabled": True}
    allow = frozenset({"houcheng.fan"})
    assert auth.ad_allowed("Houcheng.Fan", None, allow)
    assert auth.ad_allowed("someone", admin, allow)
    assert not auth.ad_allowed("someone", member, allow)
    assert not auth.ad_allowed("someone", {**admin, "enabled": False}, allow)
    assert not auth.ad_allowed("someone", None, allow)


@pytest.fixture
def ldap_on(monkeypatch):
    settings = ldap_auth.LdapSettings(
        enabled=True, label="MET AD", host="ad", port=389, use_tls=False, validate_cert=False,
        app_dn="svc@met.com", app_password="x", search_base="ou=met,dc=met,dc=com",
        attr_username="sAMAccountName", attr_name="displayName", attr_mail="mail",
        upn_suffix="met.com", sources=(),
    )
    monkeypatch.setattr(ldap_auth, "load_settings", lambda: settings)
    calls = []

    def fake_ldap(username, password):
        calls.append(username)
        if password == "good":
            return ldap_auth.LdapUser(username, "Fan Houcheng", None, None)
        return None

    monkeypatch.setattr(ldap_auth, "authenticate", fake_ldap)
    monkeypatch.setattr(auth, "OPS_USERS", frozenset({"houcheng.fan"}))
    return calls


def test_authenticate_ad_allowlisted(monkeypatch, ldap_on):
    monkeypatch.setattr(auth, "_lookup", lambda name: None)
    user = auth.authenticate("MET\\houcheng.fan", "good")
    assert user["via"] == "ldap" and user["display_name"] == "Fan Houcheng"
    with pytest.raises(auth.LoginError, match="Incorrect AD username or password"):
        auth.authenticate("houcheng.fan", "bad")


def test_authenticate_rejects_unlisted_without_binding(monkeypatch, ldap_on):
    member = {"id": 7, "username": "x", "display_name": "X", "password_hash": None,
              "auth_source": "ldap", "is_admin": False, "enabled": True}
    monkeypatch.setattr(auth, "_lookup", lambda name: member)
    with pytest.raises(auth.LoginError, match="not authorized"):
        auth.authenticate("x", "good")
    assert ldap_on == []


def test_authenticate_local_admin_never_uses_ldap(monkeypatch, ldap_on):
    from tests.test_core import _met_jira_hash

    row = {"id": 1, "username": "admin", "display_name": "管理员",
           "password_hash": _met_jira_hash("pw"), "auth_source": "local", "is_admin": True,
           "enabled": True}
    monkeypatch.setattr(auth, "_lookup", lambda name: row)
    assert auth.authenticate("admin", "pw")["via"] == "local"
    with pytest.raises(auth.LoginError, match="Incorrect username or password"):
        auth.authenticate("admin", "wrong")
    assert ldap_on == []
