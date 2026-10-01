"""
A small client for Ollama's HTTP API: `/api/chat`, `/api/tags`, `/api/version`.

Standard library only, so the home side can import it too. Plain HTTP to a
local server (`PIT_OLLAMA_URL`, default `http://127.0.0.1:11434`); nothing
here reaches the internet, so `app/net.py`'s TLS rules don't apply.

`think` is sent as False by default: Qwen3's thinking mode roughly doubles a
run on pit hardware and does little for "call a tool, report what it said".
A server too old to know the field ignores it; a model that refuses it is
asked again without it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    pass


def base_url() -> str:
    return os.environ.get("PIT_OLLAMA_URL", DEFAULT_URL).rstrip("/")


@dataclass
class Reply:
    """One assistant turn, and what it cost."""

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # [{"name", "arguments"}]
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    message: dict = field(default_factory=dict)            # as returned, for the transcript


class Ollama:
    def __init__(self, url: str | None = None, timeout_s: float = 600.0,
                 num_ctx: int = 16384, keep_alive: str = "15m"):
        self.url = (url or base_url()).rstrip("/")
        self.timeout_s = timeout_s
        # 16k holds a run's largest turn with room to spare (measured: ~7k).
        self.num_ctx = num_ctx
        # How long Ollama keeps the model loaded after a call. The app passes
        # a short one: on a 16 GB pit machine the model's 6.7 GB should be
        # held only while a run is going.
        self.keep_alive = keep_alive

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout_s) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise OllamaError(f"{path}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaError(f"{path}: can't reach Ollama at {self.url} ({e})") from None

    def version(self) -> str | None:
        try:
            return self._request("/api/version", timeout=3).get("version")
        except OllamaError:
            return None

    def models(self) -> list[str]:
        return [m["name"] for m in self._request("/api/tags", timeout=10).get("models", [])]

    def has_model(self, name: str) -> bool:
        names = self.models()
        return name in names or f"{name}:latest" in names

    def chat(self, model: str, messages: list[dict], *, tools: list[dict] | None = None,
             format: dict | None = None, temperature: float = 0.2,
             think: bool | None = False) -> Reply:
        body: dict = {"model": model, "messages": messages, "stream": False,
                      "keep_alive": self.keep_alive,
                      "options": {"temperature": temperature, "num_ctx": self.num_ctx}}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"]}} for t in tools]
        if format is not None:
            body["format"] = format
        if think is not None:
            body["think"] = think
        t0 = time.monotonic()
        try:
            out = self._request("/api/chat", body)
        except OllamaError as e:
            if "think" not in body or "think" not in str(e).lower():
                raise
            body.pop("think")
            out = self._request("/api/chat", body)
        msg = out.get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            a = fn.get("arguments")
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except json.JSONDecodeError:
                    a = {"_unparsed": a}
            calls.append({"name": fn.get("name", ""), "arguments": a or {}})
        return Reply(content=msg.get("content") or "", tool_calls=calls,
                     prompt_tokens=int(out.get("prompt_eval_count") or 0),
                     output_tokens=int(out.get("eval_count") or 0),
                     seconds=time.monotonic() - t0, message=msg)
