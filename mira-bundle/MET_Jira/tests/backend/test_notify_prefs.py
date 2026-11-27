"""Unit coverage for per-user notification preference helpers."""

from __future__ import annotations

from types import SimpleNamespace

from backend.services import notify_prefs


def test_wecom_defaults_on_and_can_be_disabled():
    unset = SimpleNamespace(preferences=None)
    assert notify_prefs.wants_wecom(unset) is True

    off = SimpleNamespace(preferences={"notifications": {"wecom": False}})
    assert notify_prefs.wants_wecom(off) is False

    on = SimpleNamespace(preferences={"notifications": {"wecom": True}})
    assert notify_prefs.wants_wecom(on) is True


def test_validate_accepts_wecom_flag():
    prefs = notify_prefs.validate({"wecom": False, "email": True})
    assert prefs["wecom"] is False
    assert prefs["email"] is True
    assert prefs["in_app"] is True
