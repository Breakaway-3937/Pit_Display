"""
What a run actually looked at, read back from its transcript.

Brayden, 2026-10-03: "I want the ability to see what exactly qwen has
analyzed." A run's transcript (`analysis_run.transcript`) holds both
conversations whole; this turns the analyst's into steps a person can read:
the data it was handed up front, every tool it called (with the arguments,
logs named instead of uids) and what each call gave back, and where the
checks sent it back to fix something. No Qt, so home can use it too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class Step:
    kind: str                    # "given" | "tool" | "retry" | "answer"
    title: str                   # "faults", "session_overview (given)", …
    args: dict = field(default_factory=dict)
    result: str = ""             # one line: what came back
    error: str = ""


def _summarise(payload) -> str:
    """One line for a tool result: what kind of data, how much of it."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return payload.strip().splitlines()[0][:160] if payload.strip() else "(empty)"
    if not isinstance(payload, dict):
        return str(payload)[:160]
    if "error" in payload:
        return f"error: {str(payload['error'])[:140]}"
    parts = []
    for k, v in payload.items():
        if k == "args":
            continue
        if isinstance(v, list):
            parts.append(f"{len(v)} {k}")
        elif isinstance(v, dict):
            parts.append(f"{k} ({len(v)} fields)")
        elif v is not None and len(parts) < 6:
            parts.append(f"{k} {str(v)[:30]}")
    return ", ".join(parts[:8]) or "(nothing)"


_GIVEN = re.compile(r"^(\w+) result:\s*$", re.M)


def steps(transcript: dict | None) -> list[Step]:
    """The analyst conversation as readable steps, in order."""
    msgs = (transcript or {}).get("analyst") or []
    out: list[Step] = []
    pending: dict[str, Step] = {}
    first_user = True
    for m in msgs:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user" and first_user:
            first_user = False
            # The data handed over before the first question: "<tool> result:\n{json}".
            marks = list(_GIVEN.finditer(content))
            for i, mk in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(content)
                body = content[mk.end():end].strip()
                args = {}
                try:
                    args = json.loads(body).get("args") or {}
                except (ValueError, AttributeError):
                    pass
                out.append(Step("given", f"{mk.group(1)} (given)", args, _summarise(body)))
            continue
        if role == "user":
            text = content.strip().splitlines()[0] if content.strip() else ""
            if text.lower().startswith(("now write", "now design")):
                continue                 # the next stage's instructions, not a correction
            lines = [ln.strip("-• ").strip() for ln in content.splitlines() if ln.strip()]
            why = "; ".join(ln for ln in lines[1:4] if not ln.lower().startswith("fix"))
            out.append(Step("retry", "Sent back by the checks", result=(why or text)[:240]))
            continue
        if role == "assistant":
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"raw": args}
                step = Step("tool", str(fn.get("name", "?")), dict(args))
                out.append(step)
                pending[str(call.get("id", len(out)))] = step
            if content.strip() and not m.get("tool_calls"):
                text = content.strip()
                if text.startswith("{"):
                    try:
                        n = len(json.loads(text).get("findings") or [])
                        out.append(Step("answer", "Wrote its findings", result=f"{n} finding(s)"))
                    except (ValueError, AttributeError):
                        out.append(Step("answer", "Wrote its findings", result="(not valid JSON)"))
                else:
                    out.append(Step("answer", "Answered", result=text.splitlines()[0][:200]))
            continue
        if role == "tool":
            step = pending.pop(str(m.get("tool_call_id", "")), None)
            if step is None and pending:
                step = pending.pop(next(iter(pending)))
            if step is not None:
                step.result = _summarise(content)
                if step.result.startswith("error:"):
                    step.error = step.result
    return out


def logs_looked_at(transcript: dict | None) -> set[str]:
    """Every session uid any step named."""
    uids = set()
    for s in steps(transcript):
        for k in ("session_uid", "session_uids"):
            v = s.args.get(k)
            if isinstance(v, str):
                uids.add(v)
            elif isinstance(v, list):
                uids.update(str(x) for x in v)
    return uids
