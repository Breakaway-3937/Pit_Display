"""
The CSA's diagnosis: one issue at a time, the model reasons; code checks.

Brayden, 2026-11-03: "there's a lot of questions of things that are going
wrong but not being reported ... turning the pit into a stationary CSA." The
detectors (`app/robot/events.py`, `incidents.py`) find every moment; the
knowledge base (`csa.py`) says what usually causes each kind and scores the
causes on facts code measured. What's left is judgement, and that's the
model's: weigh the candidates against the evidence, look closer when two are
close (the tools: `moment`, `series_1s`, `faults`, `list_signals`), and pick
a cause, or say plainly that it can't tell.

Why one issue at a time: a whole match in one 16k context made an 8B model
re-ask for what it had (11 of 12 calls on a real run, 2026-11-03) and lose
rules it was given. Here each call holds one issue, its worst moment, and a
handful of candidates: a question it can actually reason about.

**Code decides what passes** (`check`):
* `cause` must be a candidate's id, or "unexplained";
* `confidence` "high" needs two facts for the chosen cause and none against
  it (as code measured them); a guess can't be passed off as sure;
* a cause the facts score lower than the best needs a tool call that looked
  closer (a real run picked "current draw" saying "no direct evidence of
  current draw", 2026-11-03), and when no cause has a fact for it the answer
  is "unexplained" or low;
* every figure in `why` and `check` must be one the evidence or a tool gave
  (display rounding allowed, as on boards), or the knowledge base's own text;
* `ruled_out` names candidates.
One retry with the problems, then code's own answer: the best-supported
cause if the facts single it out, else "unexplained", confidence low, marked
`by: code`. Never a figure changed.

The diagnosis becomes a finding (`to_finding`), its id `csa_<issue>`, its
claim built by code from the model's choice, evidence citing `incidents`
(so the checks can find every figure); the reasoning, the candidates and
what was ruled out go in the run's stats (`stats.incidents`) for the panel.

No Qt; never raises for a model problem.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from app.ai import checks, csa
from app.ai.ollama import LOOP_PENALTY

DIAGNOSE_VERSION = 1
MAX_TOOL_CALLS = 3
ANSWER_TOKENS = 700         # a diagnosis is a few sentences of JSON
TOOLS = ("moment", "series_1s", "faults", "list_signals", "series_stats")
CONFIDENCE = ("high", "medium", "low")

SYSTEM = """\
You are the Control System Advisor (CSA) for FRC Team 3937's pit crew. You diagnose one \
problem the robot's logs show, the way an experienced CSA would: from the evidence, not \
from guesses.

You get: the issue (what happened, how often, when, which devices), the readings at its \
worst moment from every log, and the candidate causes from the CSA's knowledge, each with \
the facts code measured for and against it, and how often the crew confirmed it before.

Reason it through:
- Which candidate do the facts support best? A fact against a cause counts as much as \
one for it.
- If two candidates are close, use a tool to tell them apart (at most 3 calls): moment for \
the readings at another time (at_s in seconds from the recording's start), series_1s or \
series_stats for one signal, faults for the latched faults.
- in_earlier_recordings says whether it keeps happening: in most of the robot's recent recordings, the cause travels with the robot (wiring, code, a mechanism), not one battery or one field; new, something changed since the last match.
- If nothing fits, say "unexplained": a CSA says "I can't tell, look here".
- measured lists what the logs show (which motor, which bus, how much): reason from it and \
name those devices; the crew shouldn't have to go digging.
- You may reach. hunch (optional, one sentence): a cause outside the candidates, or a link \
to the recording's other problems (other_problems), that the evidence hints at, and what \
would confirm it. The crew sees it labelled as a reach, so be bold but say why.
Then answer as JSON:
- cause: one candidate's id exactly, or "unexplained".
- confidence: high only when the facts single it out (a fact for it, none against); \
medium when it's the best fit; low when it's a lean.
- why: 1-2 sentences: the evidence that decided it, with its figures.
- check: what the crew should physically check first, in one sentence.
- ruled_out: ids of candidates the evidence argues against.
- hunch: one sentence, as above; "none" only if truly nothing hints at more.
Every number you write must come from the evidence or a tool result. /no_think"""


@dataclass
class Diagnosis:
    issue: str
    cause: str = "unexplained"
    confidence: str = "low"
    why: str = ""
    check: str = ""
    ruled_out: list = field(default_factory=list)
    by: str = "model"                       # model | code
    candidates: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    seconds: float = 0.0
    measured: list = field(default_factory=list)   # what the logs show, by code
    hunch: str = ""                                # the model's labelled reach

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "issue", "cause", "confidence", "why", "check", "ruled_out", "by", "problems",
            "calls", "seconds", "measured", "hunch")} | {"candidates": [
                {k: c[k] for k in ("id", "title", "cause", "for", "against", "score",
                                   "confirmed")} for c in self.candidates]}


def format_schema(candidates: list[dict]) -> dict:
    ids = [c["id"] for c in candidates] + ["unexplained"]
    return {"type": "object", "required": ["cause", "confidence", "why", "check", "hunch"],
            "properties": {
                "cause": {"type": "string", "enum": ids},
                "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                "why": {"type": "string"}, "check": {"type": "string"},
                "hunch": {"type": "string"},
                "ruled_out": {"type": "array", "items": {"type": "string", "enum": ids}}}}


def _pool(ledger: checks.Ledger, issue: dict, candidates: list[dict]) -> list[float]:
    pool = []
    for c in ledger.calls:
        pool += checks.numbers(c.result, skip_args=False)
        pool += checks.context_numbers(json.dumps(c.result, default=str))
    pool += checks.numbers(issue, skip_args=False)
    pool += checks.context_numbers(json.dumps(issue, default=str))
    for c in candidates:            # the knowledge base's own figures (180 A, 60 Ω…)
        pool += checks.context_numbers(c["check"], c["cause"], " ".join(c["for"] + c["against"]))
        e = csa.BY_ID.get(c["entry"])
        if e:
            pool += checks.context_numbers(e["what"], e["title"])
    return pool + list(checks.PUBLISHED)


def check(answer: dict, candidates: list[dict], pool: list[float],
          looked: bool = False) -> list[str]:
    ids = {c["id"]: c for c in candidates}
    problems = []
    cause = answer.get("cause")
    best = max((c["score"] for c in candidates), default=0)
    if cause in ids and ids[cause]["score"] < best and not looked:
        top = [c["id"] for c in candidates if c["score"] == best]
        problems.append(f"the measured facts favour {', '.join(top)} (score {best}) over "
                        f"{cause} (score {ids[cause]['score']}): pick it, or look closer with "
                        "a tool first, or say unexplained")
    if best <= 0 and cause != "unexplained" and answer.get("confidence") != "low":
        problems.append("no candidate has a measured fact for it: say unexplained, or "
                        "confidence low")
    if cause != "unexplained" and cause not in ids:
        problems.append(f"cause {cause!r} isn't a candidate; use one of "
                        f"{', '.join(ids)} or unexplained")
    if answer.get("confidence") not in CONFIDENCE:
        problems.append("confidence must be high, medium or low")
    if answer.get("confidence") == "high" and cause in ids:
        c = ids[cause]
        if len(c["for"]) < 2 or c["against"]:
            problems.append(f"high confidence needs two facts for {cause} and none against it "
                            f"(for: {c['for'] or 'none'}; against: {c['against'] or 'none'}); "
                            "say medium or low")
    if answer.get("confidence") == "high" and cause == "unexplained":
        problems.append("unexplained can't be high confidence")
    for k in ("why", "check", "hunch"):
        text = str(answer.get(k) or "")
        if not text.strip():
            if k != "hunch":
                problems.append(f"{k} is empty")
            continue
        loose = [f"{v:g}" for v, places in checks.text_numbers(text)
                 if not checks.shown_as(v, places, pool)]
        if loose:
            problems.append(f"{k}: the figure(s) {', '.join(loose)} aren't in the evidence or "
                            "a tool result")
    bad = [r for r in answer.get("ruled_out") or [] if r not in ids]
    if bad:
        problems.append(f"ruled_out names {', '.join(map(str, bad))}, not candidates")
    return problems


def _by_code(issue: dict, candidates: list[dict]) -> dict:
    """Code's answer when the model's didn't pass: the cause the facts single
    out, else unexplained. Low confidence either way; the facts are its why."""
    best = candidates[0] if candidates else None
    second = candidates[1] if len(candidates) > 1 else None
    if best and best["score"] >= 1 and (second is None or best["score"] > second["score"]):
        return {"cause": best["id"], "confidence": "low",
                "why": "From the measured facts: " + "; ".join(best["for"])[:240],
                "check": best["check"][:200], "ruled_out": []}
    facts = "; ".join(f for c in candidates[:2] for f in c["for"])[:240]
    return {"cause": "unexplained", "confidence": "low",
            "why": ("The facts don't single out one cause" + (f": {facts}" if facts else ".")),
            "check": (best["check"][:200] if best else "look at the events at its worst moment"),
            "ruled_out": []}


def _a(v) -> str:
    return f"{v:.0f} A" if isinstance(v, (int, float)) else "?"


def measured(issue: dict) -> list[str]:
    """What the logs show about an issue, in words, by code: which motors,
    which bus, how much. Shown to the model to reason from and to the crew
    as the facts, apart from the model's inference (Brayden, 2026-11-05:
    "provide the relevant motor that spiked so we don't have to go digging")."""
    out: list[str] = []
    r = issue.get("readings") or {}
    base = issue["primary"].split(":")[0]
    if base in ("brownout", "voltage_dip", "device_voltage_dip", "roborio_reboot",
                "device_reset"):
        low = (issue.get("figures") or {}).get("V") or r.get("battery_v") or r.get("ds_battery_v")
        if isinstance(low, (int, float)):
            out.append(f"lowest battery {low:.2f} V")
        if isinstance(r.get("total_current_a"), (int, float)):
            out.append(f"whole robot {_a(r['total_current_a'])} at the worst moment")
        fed = r.get("most_supply_current") or []
        if fed:
            out.append("most supply current then: " + ", ".join(
                f"{m['device']} {_a(m['supply_a_max'])} ({m['bus']})" for m in fed))
        elif r.get("hardest_motors"):
            out.append("hardest-working motors then (stator): " + ", ".join(
                f"{m['device']} {_a(m['stator_a_max'])}" + (f" ({m['bus']})" if m.get("bus") else "")
                for m in r["hardest_motors"]))
        if r.get("pdh_channels"):
            out.append("busiest PDH channels (DS log): " + ", ".join(
                f"channel {c['ch']} {_a(c['a'])}" for c in r["pdh_channels"]))
        if r.get("lowest_controller_supply"):
            lc = r["lowest_controller_supply"]
            out.append(f"lowest controller supply {lc['v']:.2f} V on {lc['device']}")
    buses = {b: d for b, d in (issue.get("buses") or {}).items() if d}
    named = [d for devs in buses.values() for d in devs]
    if buses and named:
        out.append("devices, by bus: " + "; ".join(
            f"{bus}: {', '.join(devs[:8])}" + (f" (+{len(devs) - 8})" if len(devs) > 8 else "")
            for bus, devs in buses.items()))
        real = [b for b in buses if b in ("CANivore", "roboRIO CAN")]
        if len(buses) == 1 and real and len(named) >= 3:
            out.append(f"all {len(named)} on one bus ({real[0]})")
        elif len(real) > 1:
            out.append(f"on {len(buses)} buses: not one bus's wiring alone")
    elif issue.get("devices") and base in _DEVICE_KINDS:
        out.append("devices: " + ", ".join(issue["devices"][:8]))
    phases = [p for p in issue.get("phases") or [] if p != "unknown"]
    out.append(f"{issue['count']} time(s), at {', '.join(issue['at'][:4])}"
               + (f" ({'/'.join(phases)})" if phases else ""))
    return out


def brief_for(issue: dict, candidates: list[dict]) -> dict:
    head = csa.describe(issue)
    return {
        "measured": measured(issue),
        "other_problems": [k for k in issue.get("recording_kinds") or []
                           if k != issue["primary"].split(":")[0]][:12],
        "issue": {"title": head["title"], "what_it_means": head["what"],
                  "kind": issue["primary"], "times": issue["count"], "when": issue["at"][:6],
                  "phases": issue["phases"], "devices": issue["devices"][:10],
                  "with": issue["kinds"], "longest_s": issue.get("longest_s"),
                  **({"in_earlier_recordings": issue["before"]} if issue.get("before") else {})},
        "worst_moment": {"at": issue["worst"]["at"], "at_s": issue["worst"]["start_s"],
                         "phase": issue["worst"]["phase"],
                         "events": issue["worst"]["events"][:10],
                         "readings": issue.get("readings") or {}},
        "candidates": [{"id": c["id"], "cause": c["cause"], "facts_for": c["for"],
                        "facts_against": c["against"], "crew_confirmed_before": c["confirmed"],
                        "check": c["check"]} for c in candidates]}


def diagnose(toolbox, llm, model: str, session_uid: str, issue: dict, ledger: checks.Ledger,
             *, confirmed: dict[str, int] | None = None, brief: str = "",
             on_step=None, retries: int = 1) -> Diagnosis:
    t0 = time.monotonic()
    step = on_step or (lambda _m: None)
    candidates = csa.match(issue, {"readings": issue.get("readings") or {}}, confirmed)
    d = Diagnosis(issue=issue["id"], candidates=candidates)
    if not candidates:
        d.why = "no entry in the CSA knowledge covers this kind yet"
        d.by = "code"
        return d
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": (("About this robot, from the team:\n" + brief.strip()
                                          + "\n\n") if brief else "")
             + f"session_uid for tools: {session_uid}\n"
             + json.dumps(brief_for(issue, candidates), ensure_ascii=False, default=str)}]
    specs = [s for s in toolbox.specs() if s["name"] in TOOLS]
    answer = None
    try:
        used = 0
        while True:
            reply = llm.chat(model, msgs, tools=specs if used < MAX_TOOL_CALLS else None,
                             temperature=0.2, max_tokens=512)
            msgs.append({"role": "assistant", "content": reply.content,
                         **({"tool_calls": reply.message.get("tool_calls")}
                            if reply.tool_calls else {})})
            if not reply.tool_calls:
                break
            for c in reply.tool_calls:
                used += 1
                step(f"diagnosing: {c['name']}")
                out = toolbox.call(c["name"], c["arguments"]) if used <= MAX_TOOL_CALLS \
                    else {"args": c["arguments"], "error": "tool budget spent; answer now"}
                d.calls.append({"tool": c["name"], "args": c["arguments"]})
                if "error" not in out:
                    ledger.add(c["name"], out)
                msgs.append({"role": "tool", "tool_name": c["name"],
                             "tool_call_id": c.get("id") or "",
                             "content": json.dumps(out, default=str)})
            if used >= MAX_TOOL_CALLS:
                break
        pool = _pool(ledger, issue, candidates)
        cut = False
        for attempt in range(retries + 1):
            reply = llm.chat(model, msgs, format=format_schema(candidates),
                             temperature=0.1 if attempt == 0 else 0.4, max_tokens=ANSWER_TOKENS,
                             presence_penalty=LOOP_PENALTY if cut else 0.0)
            msgs.append({"role": "assistant", "content": reply.content})
            cut = reply.truncated
            try:
                if cut:
                    raise ValueError(f"cut off at {ANSWER_TOKENS} tokens: write it shorter")
                got = json.loads(reply.content)
            except ValueError as e:
                got, probs = None, [f"not valid JSON: {e}"]
            else:
                probs = check(got, candidates, pool, looked=bool(d.calls)) \
                    if isinstance(got, dict) else ["not a JSON object"]
            if not probs:
                answer = got
                break
            d.problems = probs
            if attempt < retries:
                msgs.append({"role": "user", "content": "These problems were found:\n- "
                             + "\n- ".join(probs) + "\nAnswer again as JSON, fixing them."})
    except Exception as e:                    # the engine went away: code answers
        d.problems = d.problems + [f"{type(e).__name__}: {e}"]
    if answer is None:
        answer, d.by = _by_code(issue, candidates), "code"
    d.cause, d.confidence = answer["cause"], answer["confidence"]
    d.why = " ".join(str(answer.get("why") or "").split())[:300]
    d.check = " ".join(str(answer.get("check") or "").split())[:220]
    d.ruled_out = [r for r in answer.get("ruled_out") or [] if r != d.cause]
    d.hunch = " ".join(str(answer.get("hunch") or "").split())[:240]
    if d.hunch.lower().strip(" .") in ("none", "no", "n/a", ""):
        d.hunch = ""
    d.measured = measured(issue)
    d.seconds = round(time.monotonic() - t0, 1)
    return d


# ── as a finding ─────────────────────────────────────────────────────────

_FIGURE = {"brownout": ("battery_v", "lowest battery", "V"),
           "voltage_dip": ("battery_v", "lowest battery", "V"),
           "device_voltage_dip": ("lowest_controller_supply", "lowest controller supply", "V"),
           "can_busy": ("can_util_pct", "CAN utilization", "%"),
           "packet_loss": ("packet_loss_pct", "packet loss", "%"),
           "high_trip_time": ("trip_ms", "trip time", "ms"),
           "loop_stall": ("loop_ms", "longest loop", "ms"),
           "console_spam": (None, "times one line was printed", "lines"),
           "low_memory": (None, "lowest free memory", "MB")}
# Kinds whose claim must say what was found, not only its title: the line
# that was printed, how low memory went.
_SAYS_WHAT = ("console_spam", "low_memory")


def _first_clause(text: str) -> str:
    """A check's first instruction: up to its first ';' (the KB writes the most
    important one first), without a trailing full stop."""
    head = re.split(r";\s|\.\s", " ".join(str(text or "").split()), maxsplit=1)[0]
    return head.rstrip(". ")


def _sentence(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:(")
    return cut + "…"


_POWER = ("brownout", "voltage_dip", "device_voltage_dip", "roborio_reboot", "device_reset")
_DEVICE_KINDS = ("device_fault", "can_timeout", "can_errors", "can_bus_off", "can_tx_full",
                 "config_failed", "motor_stall", "device_reset")


def _devices(names: list[str], most: int = 5) -> str:
    """'TalonFX 8, 10, 18 and CANcoder 23': a type said once, ids after it."""
    by: dict[str, list[str]] = {}
    other: list[str] = []
    for n in names:
        m = re.match(r"(.+?) (\d+)$", n)
        (by.setdefault(m.group(1), []).append(m.group(2)) if m else other.append(n))
    parts = [f"{t} {', '.join(ids)}" for t, ids in by.items()] + other
    text = "; ".join(parts)
    return text if len(names) <= most else _sentence(text, 60) + f" ({len(names)} in all)"


def focus(issue: dict) -> str:
    """Where to look, for the claim: the motor that drew the most at a power
    problem, the bus and its devices at a device or CAN problem."""
    r = issue.get("readings") or {}
    base = issue["primary"].split(":")[0]
    if base in _POWER:
        fed = r.get("most_supply_current") or []
        if fed:
            return "most current: " + ", ".join(
                f"{m['device']} {_a(m['supply_a_max'])}" for m in fed[:2]) + (
                f" ({fed[0]['bus']})" if len({m["bus"] for m in fed[:2]}) == 1 else "")
        if r.get("hardest_motors"):
            m = r["hardest_motors"][0]
            return f"hardest motor: {m['device']} {_a(m['stator_a_max'])}"
        if r.get("pdh_channels"):
            c = r["pdh_channels"][0]
            return f"busiest PDH channel {c['ch']} ({_a(c['a'])})"
        return ""
    if base not in _DEVICE_KINDS:
        return ""                      # comms, code: no device to point at
    buses = {b: d for b, d in (issue.get("buses") or {}).items() if d}
    if buses:
        if len(buses) == 1:
            bus, devs = next(iter(buses.items()))
            return f"{bus}: {_devices(devs)}"
        return "; ".join(f"{bus}: {_devices(devs, 3)}" for bus, devs in buses.items())
    return ""


def to_finding(issue: dict, d: Diagnosis, session_uid: str) -> dict:
    head = csa.describe(issue)
    times = f"{issue['count']}×" if issue["count"] > 1 else f"at {issue['worst']['at']}"
    b = issue.get("before") or {}
    if b.get("of"):
        # New or recurring is what a CSA says first (incidents.history).
        times += (f"; new, not in the last {b['of']} recordings" if not b.get("in")
                  else f"; also in {b['in']} of the last {b['of']} recordings")
    where = focus(issue)
    cause = next((c for c in d.candidates if c["id"] == d.cause), None)
    first = lambda text: _first_clause(text)  # noqa: E731
    if issue["primary"].split(":")[0] in _SAYS_WHAT:
        ev = next((e for e in issue["worst"].get("events") or []
                   if e.get("kind") == issue["primary"]), None)
        if ev and ev.get("detail"):
            # Its own words: "printed 1165 times (122 a minute): Indexer setState…"
            head = dict(head, title=("The code " if issue["primary"] == "console_spam"
                                     else "") + _sentence(ev["detail"], 110))
            times = times.split("; ", 1)[1] if "; " in times else ""
    # Where to look comes before the cause: a 200-character cut takes the tail.
    lead = f"{head['title']}{f' ({times})' if times else ''}" + (f"; {where}" if where else "")
    if cause is not None:
        verdict = {"high": "", "medium": "likely ", "low": "possibly "}[d.confidence] + cause["cause"]
        claim = f"{lead}: {verdict}. Check: {first(d.check or cause['check'])}"
    else:
        claim = f"{lead}, cause unclear. Check: {first(d.check)}"
    base = issue["primary"].split(":")[0]
    key, name, unit = _FIGURE.get(base, (None, "times", ""))
    r = issue.get("readings") or {}
    # The issue's extreme over every occurrence first (the lowest battery of
    # all 18 brownouts, not the worst one's moment), then that moment's reading.
    value = (issue.get("figures") or {}).get(unit) if unit else None
    if value is None:
        value = r.get(key) if key else None
    if isinstance(value, dict):
        value = value.get("v")
    if not isinstance(value, (int, float)):
        name, unit, value = "times it happened", "", issue["count"]
    fid = re.sub(r"[^a-z0-9_]+", "_", f"csa_{issue['primary']}".lower()).strip("_")[:40]
    # The card draws the motor that loaded the battery most, when one did.
    fed = (r.get("most_supply_current") or [None])[0] if base in _POWER else None
    series = ({"session_uid": fed["session_uid"], "device_type": fed["device_type"],
               "can_id": fed["can_id"], "signal": "SupplyCurrent", "column": "v_max"}
              if fed and fed.get("session_uid") else None)
    return {"id": fid, "claim": _sentence(claim, 200),
            "metric": {"name": name, "value": value, "unit": unit},
            "severity": "fault" if issue["severity"] == "fault" else "warn",
            "subsystem": None, "series": series,
            "evidence": [{"tool": "incidents", "args": {"session_uid": session_uid},
                          "value": value}]}
