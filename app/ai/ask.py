"""
Ask the pit: a crew member's question about the robot's logs, answered in a
few sentences by the local model through the analysis's own read-only tools.

    "Any faults in the newest log?"   "Which motor ran hottest in qm14?"
    "Was the battery low in the last match?"

The analysis's rule holds here too: **models choose and word, code checks**.
Code fetches the recent logs first, so "the newest log" and "qm14" mean
something; the model calls tools; and every number in the answer must be one
a tool returned (display-rounding allowed, as on boards). A figure that
isn't goes back to the model once; if it's still there, the answer is shown
with that figure marked unverified, never passed off as checked.

No Qt (home can import it, as it does the pipeline). Never raises for a model
or tool problem: a failure is an Answer with status "failed".
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from app.ai import checks
from app.ai.pipeline import FAULT_GUIDE

# Bump when the prompt or the checks change what an answer can say.
ASK_VERSION = 5
_MATCH = re.compile(r"\b((?:qm|qf|sf|f|pm|em)\d{1,3})\b", re.I)

ASK_SYSTEM = """You answer an FRC pit crew's questions about their robot's logs, during an event, \
while they work on the robot. Use the tools to find the answer in the logs.

Rules:
- Every number you write must come from a tool result in this conversation. Copy it; rounding \
for display is fine. Never estimate, never use general knowledge for a figure.
- If the logs don't hold the answer, say so plainly and say what you looked at. Don't guess.
- Say which log the answer comes from (its name or match).
- Two to four short sentences in plain words. No headings, no bullet lists unless asked. Give \
units.
- Name the log by the label you're given (its time, and its match if it has one), never its \
file name. Name devices by their name or type and CAN id (TalonFX 17).
- Faults: a fault's `status` "fault" is serious, "warn" usually isn't (see below). Say which \
devices and what the crew should look at.
- Only say whether the robot was enabled if match_phases says so; it may not be recorded.
- anomalies is a detector comparing this log with the robot's earlier logs. Kind wear is a \
motor getting worse across logs (more current for the same speed, heating or faulting more than \
the other motors): say so whenever the question is about a motor's health or what to maintain. \
The wear tool gives any motor's trend log by log; if it has no warnings, nothing is wearing out \
that the logs can show.
- The overview's vitals don't say which device a figure came from. To say which motor or \
device ("hottest", "highest current"), call series_stats for that signal and use the device it \
names for that figure. Never attach a device to a figure some other way.

""" + FAULT_GUIDE

RETRY = ("These figures in your answer aren't in any tool result: {figures}. Answer again: "
         "find them with a tool, or leave them out.")
RETRY_PAIR = ("Nothing in the tool results ties these figures to the device you named: {pairs}. "
              "Call series_stats for that signal to see which device it was, or don't name one.")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

_THINK = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class Answer:
    question: str
    text: str = ""
    status: str = "answered"        # answered | unverified | failed
    unverified: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)       # [{tool, args}] the model asked for
    seconds: float = 0.0
    model: str = ""
    focus: str = ""                 # the session the answer was grounded in
    error: str = ""
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "question", "text", "status", "unverified", "calls", "seconds", "model",
            "focus", "error", "at")} | {"version": ASK_VERSION}


def _pool(ledger: checks.Ledger, *texts: str) -> list[float]:
    """Every number the answer may use: in tool results, including inside
    their strings (a log named qm14, a date), and in what the crew wrote."""
    pool: list[float] = []
    for c in ledger.calls:
        pool += checks.numbers(c.result, skip_args=False)
        pool += checks.context_numbers(json.dumps(c.result, default=str))
    return pool + checks.context_numbers(*texts)


def focus(question: str, sessions: list[dict], previous: str | None = None) -> dict | None:
    """The log a question is about: one whose match it names (qm14), else the
    log the conversation was already on, else the newest."""
    named = {m.lower() for m in _MATCH.findall(question)}
    for s in sessions:
        key = str(s.get("match_key") or "").lower()
        if named and any(key.endswith(n) for n in named):
            return s
    if previous:
        hit = next((s for s in sessions if s.get("uid") == previous), None)
        if hit:
            return hit
    return sessions[0] if sessions else None


def label(s: dict) -> str:
    """A log as a person names it: when, how long, which match."""
    when = str(s.get("started_at") or "unknown time")[:16]
    mins = (s.get("duration_s") or 0) / 60
    match = f"match {s['match_key']}" if s.get("match_key") else "not tagged with a match"
    return f"the log recorded {when} ({mins:.0f} min, {match})"


def _primer(toolbox, ledger: checks.Ledger, s: dict) -> str:
    """What an 8B model rarely asks for, fetched by code (as the analysis
    does): the focused log's overview, faults, phases and the detector's
    findings (wear included)."""
    parts = [f"The question is most likely about {label(s)}, session_uid {s['uid']}."]
    for name in ("session_overview", "faults", "match_phases", "anomalies"):
        try:
            out = toolbox.call(name, {"session_uid": s["uid"]})
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        if not isinstance(out, dict) or "error" in out:
            continue
        ledger.add(name, out)
        if name == "faults":
            out = {k: out[k] for k in ("summary", "by_device") if k in out}
        if name == "session_overview":
            out = {k: v for k, v in out.items() if k != "session"}
        parts.append(f"{name} result:\n{json.dumps(out, default=str)}")
    return "\n\n".join(parts)


def unsupported(text: str, pool: list[float]) -> list[str]:
    """The figures in `text` that no tool returned, as written."""
    out = []
    for value, places in checks.text_numbers(text):
        if not checks.shown_as(value, places, pool):
            shown = f"{value:.{places}f}" if places else f"{value:g}"
            if shown not in out:
                out.append(shown)
    return out


def device_numbers(ledger: checks.Ledger) -> dict[str, list[float]]:
    """Each device a tool result names, with the numbers in the rows about it."""
    out: dict[str, list[float]] = {}

    def walk(v) -> None:
        if isinstance(v, dict):
            names = {str(v[k]) for k in ("device", "device_label") if v.get(k)}
            if v.get("device_type") and v.get("can_id") is not None:
                names.add(f"{v['device_type']} {v['can_id']}")
            names |= {str(d) for d in v.get("devices") or [] if isinstance(d, str)}
            if names:
                nums = checks.numbers(v, skip_args=False)
                for n in names:
                    out.setdefault(n.lower(), []).extend(nums)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for c in ledger.calls:
        walk(c.result)
    return out


def misattributed(text: str, ledger: checks.Ledger, context: list[float]) -> list[str]:
    """Figures written in the same sentence as a device that no row about
    that device holds: "26.0 → TalonFX 9". The figure check alone passes a
    real number pinned on the wrong motor (2026-10-18, a real run)."""
    rows = device_numbers(ledger)
    names = sorted(rows, key=len, reverse=True)
    out: list[str] = []
    for sentence in _SENTENCE.split(text):
        low, said = sentence.lower(), []
        for n in names:
            # Whole words: "TalonFX 1" isn't in "TalonFX 17".
            pattern = re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])")
            if pattern.search(low):
                said.append(n)
                low = pattern.sub(" ", low)     # its own digits aren't figures
        if not said:
            continue
        for value, places in checks.text_numbers(low):
            if checks.shown_as(value, places, context):
                continue
            if not any(checks.shown_as(value, places, rows[n]) for n in said):
                shown = f"{value:.{places}f}" if places else f"{value:g}"
                out.append(f"{shown} → {', '.join(said)}")
    return out


def ask(toolbox, llm, model: str, question: str, *,
        history: list[tuple[str, str]] | None = None, max_tool_calls: int = 8,
        retries: int = 1, on_step: Callable[[str], None] | None = None,
        previous_focus: str | None = None) -> Answer:
    """Answer one question. `history`: earlier (question, answer) pairs this
    session, so a follow-up ("and the other one?") makes sense;
    `previous_focus`: the log the last answer was about."""
    step = on_step or (lambda _m: None)
    t0 = time.monotonic()
    ans = Answer(question=question.strip(), model=model)
    ledger = checks.Ledger()
    history = list(history or [])[-3:]
    try:
        recent = toolbox.call("list_sessions", {"limit": 8})
        if "error" not in recent:
            ledger.add("list_sessions", recent)
        msgs: list[dict] = [{"role": "system", "content": ASK_SYSTEM + "\n/no_think"}]
        for q, a in history:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        sessions = [] if "error" in recent else list(recent.get("sessions") or [])
        listed = [{"uid": s.get("uid"), "label": label(s)} for s in sessions]
        primer = ""
        on = focus(ans.question, sessions, previous_focus)
        if on is not None:
            ans.focus = on["uid"]
            primer = _primer(toolbox, ledger, on)
        msgs.append({"role": "user", "content":
                     f"Recent logs, newest first:\n{json.dumps(listed)}\n\n{primer}\n\n"
                     f"Question: {ans.question}"})
        specs = toolbox.specs()
        context = [ans.question] + [t for pair in history for t in pair]
        used = 0
        for attempt in range(retries + 1):
            step("thinking" if attempt == 0 else "checking its figures")
            while True:
                reply = llm.chat(model, msgs, tools=specs if used < max_tool_calls else None,
                                 temperature=0.1 if attempt == 0 else 0.4)
                msgs.append({"role": "assistant", "content": reply.content,
                             **({"tool_calls": reply.message.get("tool_calls")}
                                if reply.tool_calls else {})})
                if not reply.tool_calls:
                    break
                for c in reply.tool_calls:
                    if used >= max_tool_calls:
                        out = {"args": c["arguments"], "error": "tool budget spent; answer now"}
                    else:
                        used += 1
                        step(f"looking at {c['name']}")
                        out = toolbox.call(c["name"], c["arguments"])
                        ans.calls.append({"tool": c["name"], "args": c["arguments"]})
                        if "error" not in out:
                            ledger.add(c["name"], out)
                    msgs.append({"role": "tool", "tool_name": c["name"],
                                 "tool_call_id": c.get("id") or "",
                                 "content": json.dumps(out, default=str)})
            ans.text = _THINK.sub("", reply.content or "").strip()
            loose = unsupported(ans.text, _pool(ledger, *context))
            pairs = misattributed(ans.text, ledger, checks.context_numbers(*context))
            ans.unverified = loose + [p for p in pairs if p.split(" → ")[0] not in loose]
            if not ans.unverified or attempt == retries:
                break
            fix = []
            if loose:
                fix.append(RETRY.format(figures=", ".join(loose)))
            if pairs:
                fix.append(RETRY_PAIR.format(pairs="; ".join(pairs)))
            msgs.append({"role": "user", "content": " ".join(fix)})
        if not ans.text:
            ans.status, ans.error = "failed", "the model gave no answer"
        elif ans.unverified:
            ans.status = "unverified"
    except Exception as e:          # an engine that died, a tool that broke
        ans.status, ans.error = "failed", f"{type(e).__name__}: {e}"
    ans.seconds = round(time.monotonic() - t0, 1)
    return ans
