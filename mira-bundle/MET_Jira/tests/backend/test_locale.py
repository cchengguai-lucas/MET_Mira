from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from backend.services import llm


class _Response:
    status_code = 200

    def __init__(self, lines: list[str] | None = None, data: dict | None = None):
        self._lines, self._data = lines or [], data or {}

    def json(self):
        return self._data

    def iter_lines(self):
        yield from self._lines


@pytest.fixture()
def http_llm(monkeypatch):
    """Fake the LLM at the HTTP layer so llm.chat / llm.stream_chat run for real."""
    payloads: list[dict] = []

    def post(_url, json, timeout):  # noqa: A002 - mirrors httpx.post
        payloads.append(json)
        return _Response(data={"choices": [{"message": {"content": '{"answer": "ok"}'}}]})

    @contextmanager
    def stream(_method, _url, json, timeout):  # noqa: A002 - mirrors httpx.stream
        payloads.append(json)
        chunk = {"choices": [{"delta": {"content": "report"}}]}
        yield _Response(lines=[f"data: {json_dumps(chunk)}", "data: [DONE]"])

    json_dumps = json.dumps
    monkeypatch.setattr(llm, "config", lambda: llm.LlmConfig(True, "http://fake/v1", "m", 5))
    monkeypatch.setattr(llm, "probe", lambda force=False: {"reachable": True, "tool_mode": "json"})
    monkeypatch.setattr(llm.httpx, "post", post)
    monkeypatch.setattr(llm.httpx, "stream", stream)
    return payloads


def _system(payload: dict) -> str:
    assert payload["messages"][0]["role"] == "system"
    return payload["messages"][0]["content"]


def test_english_header_reaches_chat_and_stream(admin, http_llm):
    headers = {"X-MetJira-Lang": "en"}
    admin.post("/api/agent/chat", headers=headers,
               json={"messages": [{"role": "user", "content": "hi"}]})
    admin.post("/api/agent/report", headers=headers, json={"project": "ELEC"})
    assert len(http_llm) == 2
    assert all(llm.ENGLISH_REPLY in _system(p) for p in http_llm)
    assert all(sum(m["role"] == "system" for m in p["messages"]) == 1 for p in http_llm)


def test_chinese_is_default(admin, http_llm):
    admin.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "你好"}]})
    admin.post("/api/agent/report", headers={"X-MetJira-Lang": "zh"}, json={"project": "ELEC"})
    assert len(http_llm) == 2
    assert all(llm.ENGLISH_REPLY not in _system(p) for p in http_llm)
