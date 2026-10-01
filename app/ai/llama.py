"""
A client for llama.cpp's `llama-server` (OpenAI-compatible `/v1/chat/completions`).

The engine the app ships (`runtime.py`); `ollama.py` is the same `chat()` for
a machine that has Ollama instead. Both return `ollama.Reply`, so the pipeline
can't tell them apart. Standard library only.

* **Tools** go as OpenAI `tools`; the server needs `--jinja` to apply the
  model's own tool template (runtime starts it so).
* **Structured output** is `response_format: json_schema`, which the server
  compiles to a grammar, as Ollama's `format` does.
* **Qwen3's thinking is off** (`chat_template_kwargs.enable_thinking`): it
  roughly doubles a run and the checks, not the reasoning, make it right.
* A tool result must name the call it answers (`tool_call_id`); the pipeline
  copies each call's `id` onto its result.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.ai.ollama import OllamaError, Reply


class LlamaServer:
    """`chat()` against a running llama-server; `ensure` starts one first."""

    def __init__(self, url: str = "", ensure=None, timeout_s: float = 600.0,
                 label: str = "qwen3:8b"):
        self.url = url.rstrip("/")
        self._ensure = ensure            # () -> base url, starting the server if needed
        self.timeout_s = timeout_s
        self.label = label

    def _base(self) -> str:
        if self._ensure is not None:
            self.url = self._ensure().rstrip("/")
        return self.url

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self._base() + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise OllamaError(f"llama-server {path}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OllamaError(f"llama-server {path}: can't reach {self.url} ({e})") from None

    def chat(self, model: str, messages: list[dict], *, tools: list[dict] | None = None,
             format: dict | None = None, temperature: float = 0.2,
             think: bool | None = False) -> Reply:
        body: dict = {"model": model, "messages": [_message(m) for m in messages],
                      "temperature": temperature, "stream": False,
                      "chat_template_kwargs": {"enable_thinking": bool(think)}}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"],
                "parameters": t["input_schema"]}} for t in tools]
        if format is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "output", "schema": format}}
        t0 = time.monotonic()
        out = self._post("/v1/chat/completions", body)
        msg = ((out.get("choices") or [{}])[0]).get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            a = fn.get("arguments")
            if isinstance(a, str):
                try:
                    a = json.loads(a) if a.strip() else {}
                except json.JSONDecodeError:
                    a = {"_unparsed": a}
            calls.append({"id": c.get("id"), "name": fn.get("name", ""), "arguments": a or {}})
        usage = out.get("usage") or {}
        return Reply(content=msg.get("content") or "", tool_calls=calls,
                     prompt_tokens=int(usage.get("prompt_tokens") or 0),
                     output_tokens=int(usage.get("completion_tokens") or 0),
                     seconds=time.monotonic() - t0,
                     message={"tool_calls": msg.get("tool_calls")} if calls else {})


def _message(m: dict) -> dict:
    """The pipeline's messages, as the OpenAI shape wants them."""
    out = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = m["tool_calls"]
    if m["role"] == "tool":
        out["tool_call_id"] = m.get("tool_call_id") or ""
        if m.get("tool_name"):
            out["name"] = m["tool_name"]
    return out
