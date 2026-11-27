"""OpenAI-compatible client for the DeepSeek V4 Flash deployment on 10.113.17.65:8001."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import get_settings
from .locale import current_lang

LOGGER = logging.getLogger(__name__)
PROBE_TTL = 600
ENGLISH_REPLY = (
    "The user's interface language is English. Write every natural-language part of your reply "
    "in English, overriding any instruction above to answer in Chinese, even though the "
    "instructions and data are in Chinese. Keep issue keys, people names, JSON keys, tool names "
    "and enum values unchanged."
)


class LlmError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool
    base_url: str
    model: str
    timeout: int


def config() -> LlmConfig:
    s = get_settings()
    return LlmConfig(s.llm_enabled and bool(s.llm_base_url), s.llm_base_url.rstrip("/"),
                     s.llm_model, s.llm_timeout)


def _url(base: str) -> str:
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def localize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ask for English replies when the request came from the English UI.

    Merged into the leading system message: some chat templates reject system messages
    mid-conversation.
    """
    if current_lang() != "en":
        return messages
    if messages and messages[0].get("role") == "system":
        first = {**messages[0], "content": f"{messages[0].get('content') or ''}\n\n{ENGLISH_REPLY}"}
        return [first, *messages[1:]]
    return [{"role": "system", "content": ENGLISH_REPLY}, *messages]


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = 2048,
    json_mode: bool = False,
    retries: int = 1,
) -> dict[str, Any]:
    """Return the assistant message dict (content / tool_calls)."""
    cfg = config()
    if not cfg.enabled:
        raise LlmError("LLM 未启用")
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": localize(messages),
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = httpx.post(_url(cfg.base_url), json=payload, timeout=cfg.timeout)
            if response.status_code >= 500 and attempt < retries:
                last = LlmError(f"LLM HTTP {response.status_code}")
                time.sleep(0.8)
                continue
            if response.status_code >= 400:
                raise LlmError(f"LLM HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            return data["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
            last = error
            if attempt < retries:
                time.sleep(0.8)
    raise LlmError(f"LLM 请求失败：{last}")


def stream_chat(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = 3000,
    localized: bool = True,
) -> Iterator[str]:
    """``localized=False`` keeps the reply in the source text's language (for rewriting)."""
    cfg = config()
    if not cfg.enabled:
        raise LlmError("LLM 未启用")
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": localize(messages) if localized else messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    try:
        with httpx.stream("POST", _url(cfg.base_url), json=payload, timeout=cfg.timeout) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise LlmError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError):
                    continue
                text = delta.get("content")
                if text:
                    yield text
    except httpx.HTTPError as error:
        raise LlmError(f"LLM 请求失败：{error}") from error


_probe_lock = threading.Lock()
_probe_cache: dict[str, Any] = {}


def probe(force: bool = False) -> dict[str, Any]:
    """Detect reachability and whether native tool calling works (cached)."""
    cfg = config()
    if not cfg.enabled:
        return {"enabled": False, "reachable": False, "tool_mode": "json", "model": cfg.model}
    with _probe_lock:
        cached = _probe_cache.get("result")
        if cached and not force and time.time() - _probe_cache.get("at", 0) < PROBE_TTL:
            return cached
        result = {"enabled": True, "reachable": False, "tool_mode": "json", "model": cfg.model,
                  "base_url": cfg.base_url}
        tool = {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Return pong",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}},
                               "required": ["x"]},
            },
        }
        try:
            message = chat(
                [{"role": "user", "content": "调用 ping 工具，参数 x=1"}],
                tools=[tool],
                max_tokens=64,
                retries=0,
            )
            result["reachable"] = True
            if message.get("tool_calls"):
                result["tool_mode"] = "native"
        except LlmError as error:
            if "HTTP 4" in str(error):
                try:
                    chat([{"role": "user", "content": "ping"}], max_tokens=8, retries=0)
                    result["reachable"] = True
                except LlmError:
                    pass
            result["error"] = str(error)[:200]
        _probe_cache.update(result=result, at=time.time())
        return result


JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str | None) -> Any:
    if not text:
        raise LlmError("LLM 未返回内容")
    candidates = [m.group(1) for m in JSON_BLOCK.finditer(text)] + [text]
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except ValueError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = candidate.find(opener), candidate.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except ValueError:
                    continue
    raise LlmError("无法解析 LLM 返回的 JSON")
