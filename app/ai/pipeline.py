"""
One analysis run: analyst → check → designer → check → publish.

1. **Analyst** (a model with tool calling). Code first runs `session_overview`
   for each session and hands it over, which saves a turn and grounds the
   run. The model calls tools (at most `max_tool_calls`), then writes its
   findings as `insight.schema.json`, enforced with Ollama's `format`.
2. **Check the findings** (`checks.check_findings`, plus each `series` ref
   must draw). Problems go back to the model (`retries`, twice), as a list
   saying where each figure really is; still failing rejects the run. The model may redo its work; code never
   edits a figure.
3. **Designer** (no tools): sees only the passed findings, the session's
   name and the **chart options** (the signals those findings stand on), and
   writes the board less the code-filled fields. It decides which graph tells
   each finding best (`line` across the match, `bars` across devices,
   `sessions` across matches) and names its source; it never writes data.
4. **Publisher** (code): adds `schema`, `session_uid`, `generated_at`,
   `models`, `evidence`, each card's `shape` and each chart's `data` (through
   the same toolbox), checks the whole board against `board.schema.json` and
   `checks.check_board`, and hands it to the sink.

Every run is logged by the sink, rejected and failed ones too: that log is how
models get judged. The toolbox and sink are the only things that differ
between a pit (`local.py`) and home.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol

from app.ai import checks, schema
from app.ai.ollama import LOOP_PENALTY, Ollama, OllamaError

DEFAULT_QUESTION = "What should the pit crew look at before the next match?"

# Bump whenever a prompt, a check or a tool result changes what a run can say,
# so the scoreboard compares like with like (feedback.scoreboard()). The
# number is recorded in every run's stats.
#   1 — first trials (2026-09-30)
#   2 — faults summary/by_device, code sets colours, chart options from vitals
#   7 — the playbook (playbook.py), filler findings dropped by code, a context
#       budget, empty up-front results stated, repeated calls answered short
#   8 — the detector's loop_overrun and partner_idle / partner_mismatch, each a
#       finding (must_cover), and the guide says what they mean
#   9 — the CSA: incidents across every log of the recording (the DS log too),
#       each issue diagnosed on its own (diagnose.py) before the analyst writes
PROMPT_VERSION = 11
MAX_DIAGNOSE = 5          # issues diagnosed per run, worst first (each is a model call or two)

# The context budget (measured on the two runs that overflowed 16k,
# 2026-10-21: 2.8-3.1 characters a token with the tool specs; 2.5 leaves a
# margin). Output room is kept for the findings JSON, the longest reply.
CHARS_PER_TOKEN = 2.5
OUTPUT_TOKENS = 3500         # the findings' cap, and what every request leaves free
TOOL_TURN_TOKENS = 1024      # a turn that asks for tools (a few calls, a line of prose)
DESIGNER_TOKENS = 2048
CONDENSE_TO = 0.7         # of the budget, once a conversation outgrows it (keeps the cache)
PRIMER_SHARE = 0.45       # of the budget, for the data handed over up front
GROUP_MISSING = 3          # missing controllers on one bus that become one finding
CODE_WRITES_OVER = 3       # detector items past which code writes them, not the model
MAX_REPEATS = 3           # asks for data it was already given before the tool loop ends
_DETECTOR_GAPS = ("the detector flagged", "the finding about")

# What Phoenix 6 faults mean for a pit crew (CTRE's fault definitions, with
# the pit's practical weighting). A small model can't know these, and the
# crew's first ratings were exactly these mistakes (2026-10-03): a
# BridgeBrownout latched on every controller was "not useful" (the robot was
# power-cycled), StickyFaultField was "wrong" (it's a bitfield, not a fault).
FAULT_GUIDE = """\
What the faults mean (sticky = latched since the controller last booted or was cleared):
- BridgeBrownout: the motor controller's supply dipped. On every controller at once \
with a healthy battery minimum, it's almost always the robot being powered on/off or \
a breaker reset, not a match problem: low priority. On one or two devices, or with \
a low battery minimum, check that device's power wiring and breaker.
- BootDuringEnable: the controller rebooted while the robot was enabled: a loose \
power or CAN connector, or a breaker tripping. High priority.
- DeviceTemp / ProcTemp: the motor or controller got too hot and protected itself. \
High priority: check the mechanism for binding, the duty it ran, and cooling.
- Hardware: the controller reports a hardware fault: inspect or replace it.
- StatorCurrLimit / SupplyCurrLimit: a configured current limit engaged. Usually by \
design (a drive pushing, an intake stalling on a game piece). Only worth a finding \
with high temperature or on a mechanism that shouldn't stall.
- UnderVoltage / OverSupplyV: supply voltage out of range: battery or wiring.
- FusedSensorOutOfSync / RemoteSensorDataInvalid / MissingRemoteSensor: the \
CANcoder or remote sensor the controller relies on isn't agreeing or reporting: \
check its CAN wiring, ID and configuration.
- Anything named ...Field is a bitfield summary of the others, never a fault itself.
- loop_overrun (the detector): the robot code's loop ran past its 20 ms period, and a \
stall past 100 ms is long enough for motor safety to cut outputs and the Driver Station \
to report a watchdog. Name the slow parts it lists (Shooter.periodic, \
SmartDashboard.updateValues…) and say what to check in them: logging or publishing a lot \
of data each loop, PDH or CAN reads, vision. It's the robot's code, not its wiring.
- partner_idle / partner_mismatch (the detector): two motors that turn together (same \
shaft, belt or gears) where one isn't sharing the work: turned by its partner but drawing \
almost no current (dead, unplugged, or a follower never set), or out of the pair's usual \
ratio (a belt slipping, a motor failing). Name both motors and the mechanism.
Leave these out unless their condition holds (the crew rated them not useful): \
StatorCurrLimit / SupplyCurrLimit without high temperature; a BridgeBrownout \
latched on most controllers when the battery minimum stayed above 7.5 V; a \
reading that's normal ("monitor", "within limits"). Say all clear instead.
Relate a device's fault to its mechanism (device labels, subsystems) when the data \
lets you, and say which mechanism the crew should look at."""
SHAPE_POINTS = 52       # diagnostics.SHAPE_POINTS: what a card's trace carries


class Toolbox(Protocol):
    def specs(self) -> list[dict]: ...            # [{name, description, input_schema}]
    def call(self, name: str, arguments: dict) -> dict: ...


class Sink(Protocol):
    def start_run(self, session_uids: list[str], question: str, analyst: str) -> int: ...
    def finish_run(self, run_id: int, **fields) -> None: ...
    def board_uid(self, session_uids: list[str], run_id: int) -> str: ...
    def publish(self, uid: str, board: dict) -> None: ...


@dataclass
class Stats:
    prompt_version: int = PROMPT_VERSION
    turns: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    analyst_s: float = 0.0
    designer_s: float = 0.0
    dropped: list = field(default_factory=list)     # findings that never passed, left out
    playbook: list = field(default_factory=list)    # lesson ids the analyst was given
    filler: list = field(default_factory=list)      # "nothing to see" findings dropped
    enforced: list = field(default_factory=list)    # [{finding, lesson}] dropped by a lesson
    repeats: int = 0                                # calls answered "already given"
    condensed: int = 0                              # messages condensed to fit the context
    recovered: list = field(default_factory=list)   # engine errors retried, not failed
    incidents: list = field(default_factory=list)   # the CSA's diagnoses (diagnose.Diagnosis)
    diagnose_s: float = 0.0
    code_wrote: list = field(default_factory=list)  # detector findings code wrote up front
    truncated: int = 0              # replies that ran into their token cap
    early_s: float | None = None   # seconds into the run the early board went up

    def count(self, reply, stage: str) -> None:
        self.turns += 1
        self.prompt_tokens += reply.prompt_tokens
        self.output_tokens += reply.output_tokens
        if stage == "analyst":
            self.analyst_s += reply.seconds
        else:
            self.designer_s += reply.seconds


@dataclass
class RunResult:
    run_id: int
    status: str                     # published | rejected | failed
    reason: str = ""
    insights: dict | None = None
    board: dict | None = None
    board_uid: str | None = None
    stats: Stats = field(default_factory=Stats)
    transcript: dict = field(default_factory=dict)
    t0: float = field(default_factory=time.monotonic, repr=False)


ANALYST_SYSTEM = """\
You are the analyst for FRC Team 3937's pit crew. You read robot logs through \
tools and report what the crew should act on before the next match.

Rules:
- Every number you report must be copied exactly from a tool result. Never \
compute, convert, round or estimate a figure; if you need one, call a tool that \
returns it.
- Status comes from the robot's own fault flags and the `status` fields the tools \
give. "fault" means a latched fault the tools reported. Don't invent thresholds.
- Name devices by `device_label` where the tools give one.
- Never count, add up or average rows yourself: that is computing a figure. The \
tools give counts and totals (faults → summary, session_overview → \
sticky_fault_count, series_stats → min/max/mean).
- You're given the overview, faults, subsystems and match for each log up front. \
Use the tools to look closer before you conclude: a fault's device over time \
(series_1s), a signal across devices (series_stats), what a log recorded \
(list_signals). Usually 2 to 6 calls; then stop calling tools and say you're done. \
Keep your notes between calls short.
- One run may cover several logs recorded together on the same robot (the \
AdvantageKit log and the CAN logs): read them as one match.
- anomalies is a detector that compared this log with this robot's own earlier logs. \
Each one it lists is a real difference from normal: lead with the faults among them \
(a device missing from the bus, a motor never driven while enabled: often a code or \
wiring problem), say what it likely means and what to check, and use its figures \
and normal ranges exactly. Don't explain away what it flags.
- Wear: anomalies of kind wear (and the wear tool) are motors getting worse across \
this robot's logs: needing more current for the same speed (friction, binding), or \
heating or faulting more than the other motors, more each log. It's maintenance \
before it's a failure, and often the most useful thing on the board: give each wear \
warning its own finding, severity warn (never fault), naming the motor or motors, \
with the recent figure as the metric and the earlier one as compare_to, exactly as \
the detector gives them, and what to check. Call wear for a motor's per-log values \
when the crew should see the trend.
- match_phases says when the robot was disabled, in autonomous or in teleop. If \
never_enabled is true, this was a pit or bench recording: say so first, and don't \
present currents, temperatures or power-on faults as match problems. When it was \
enabled, say which phase a problem happened in.

""" + FAULT_GUIDE

FINDINGS_PROMPT = """\
Now write your findings as JSON: 1 to 6 findings, worst first.
- id: lowercase_with_underscores.
- claim: one sentence the crew can act on.
- metric: name, value (copied exactly from a tool result), unit; compare_to only \
if a tool returned that number too.
- severity: ok, warn, fault or idle. fault only for a latched fault the tools reported.
- subsystem: a name, or null.
- series: the session_uid, device_type, can_id and signal whose per-second history \
draws this metric, or null.
- evidence: every tool call a number came from, each {tool, args, value}, with args \
exactly as that result echoed them and value the number you used.
session_uids must be %s. question: %s"""

DESIGNER_SYSTEM = """\
You design one screen for the pit crew from an analyst's findings. You choose and \
word; you never add a figure.

Rules:
- Use only numbers that appear in the findings. Rounding for display is fine \
("7.01" for 7.0123); a number from anywhere else is not.
- Every card names the finding it shows (finding = its id); one card per \
finding, and only findings worth the crew's attention. Colours are set for you \
from each finding's severity.
- vitals: up to 8 cards, worst first. value is a short formatted figure, unit \
separate, label short. Copy a finding's series onto its card when it has one.
- faults: cards for latched faults. Put every severity-fault finding's card in \
the same one of vitals or faults.
- The headline is about headline_finding, the worst one: title names its \
mechanism in a few words, sentence says what to do, plainly. Say nothing a \
finding doesn't say.
- title is a short picker label: the session's match key if it has one, then \
the headline's subject in a word or two.
- charts: up to 4 graphs, the most telling first, each from chart_options (copy \
its kind and source exactly). If chart_options is empty, write no charts. Choose the kind that answers \
the finding: "line" for how one device's value moved across the match (a sag, a \
temperature climb, a current spike); "bars" to compare devices on one signal \
(which motor ran hottest); "sessions" for match-to-match change, and for a wear \
finding the motor's trend across its logs (the strongest picture of wear). Set finding to \
the finding's id and say in why, briefly, why this graph. You never write the \
data: it is fetched for you. No chart is better than one that doesn't help.
/no_think"""


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _detector_findings(ledger, insights: dict) -> list[dict]:
    """A finding per detector fault the model left out, in the detector's own
    words and figures (`anomalies` is code, so they're exact by construction)."""
    text = checks._norm(" ".join(
        " ".join([f.get("claim", ""), (f.get("metric") or {}).get("name", ""), f.get("id", "")])
        for f in insights.get("findings") or []))
    out, seen = [], set()
    gone: dict[str, tuple[dict, list]] = {}
    for c in ledger.calls:
        if c.tool != "anomalies":
            continue
        for a in c.result.get("anomalies") or []:
            if (a.get("severity") != "fault" and a.get("kind") != "wear"
                    and not a.get("must_cover")):
                continue
            device = str(a.get("device") or "")
            key = (device, a.get("kind"), a.get("measure"))
            if key in seen:
                continue
            if (a.get("kind") == "wear"
                    and any(checks.about_wear(f, a) for f in insights.get("findings") or [])):
                continue
            if a.get("kind") != "wear" and checks.covered(a, text):
                continue
            seen.add(key)
            kind = a.get("kind")
            # A number and a unit for every kind (the contract wants both).
            name, value, unit = {
                "not_driven": ("enabled time with no output", a.get("enabled_s"), "s"),
                "missing": ("earlier logs it was on the bus", a.get("present_in"), "logs"),
                "config_changed": (f"earlier logs with {a.get('signal')} = {a.get('usual')}",
                                   a.get("usual_in"), "logs"),
                "new_fault": ("earlier logs without it", a.get("history_logs"), "logs"),
            }.get(kind, (str(a.get("signal") or kind), a.get("value"), a.get("unit") or ""))
            if not isinstance(value, (int, float)):
                continue                     # nothing exact to show: the run stays rejected
            if kind == "wear":
                # Wear is a heads-up, never red: the recent figure against the earlier.
                before = (a.get("normal") or {}).get("median")
                claim = f"{device}: {a.get('detail', '')}"
                if len(claim) > 200:
                    claim = claim[:199].rsplit(" ", 1)[0].rstrip(",;:(") + "…"
                metric = {"name": f"{a.get('measure')} (last logs)"[:80], "value": value,
                          "unit": a.get("unit") or ""}
                if isinstance(before, (int, float)):
                    metric |= {"compare_to": before, "compare_label": "the logs before"}
                out.append({
                    "id": f"detector_wear_{a.get('measure')}_{device}", "claim": claim,
                    "metric": metric, "severity": "warn", "subsystem": a.get("subsystem"),
                    "series": None,
                    "evidence": [{"tool": "anomalies", "args": dict(c.args), "value": value}]})
                continue
            if kind == "missing":
                gone.setdefault(_dumps(c.args), (c.args, []))[1].append((a, value))
                continue
            claim = _short_claim(a) or f"{device}: {a.get('detail', '')}"
            if len(claim) > 200:
                claim = claim[:199].rsplit(" ", 1)[0].rstrip(",;:(") + "…"
            out.append({
                "id": f"detector_{kind}_{device}",
                "claim": claim,
                "metric": {"name": name[:80], "value": value, "unit": unit},
                "severity": "fault" if a.get("severity") == "fault" else "warn",
                "subsystem": a.get("subsystem"), "series": None,
                # Its id starts "detector_": that's how a reader tells it from the model's.
                "evidence": [{"tool": "anomalies", "args": dict(c.args), "value": value}]})
    # Missing controllers: one finding per bus once there are several. Eleven
    # red cards for one unplugged bus (a real six-log run, 2026-11-03) hide
    # the point; a CSA says "11 controllers gone from this bus: its power or
    # its CAN chain". The figure is the detector's (logs it was there before).
    for args, items in gone.values():
        if len(items) < GROUP_MISSING:
            for a, value in items:
                device = str(a.get("device") or "")
                claim = _short_claim(a) or f"{device}: {a.get('detail', '')}"
                out.append({
                    "id": f"detector_missing_{device}", "claim": _cut_claim(claim),
                    "metric": {"name": "earlier logs it was on the bus", "value": value,
                               "unit": "logs"},
                    "severity": "fault" if a.get("severity") == "fault" else "warn",
                    "subsystem": a.get("subsystem"), "series": None,
                    "evidence": [{"tool": "anomalies", "args": dict(args), "value": value}]})
            continue
        names = [str(a.get("device") or "") for a, _ in items]
        value = max(v for _, v in items)
        claim = (f"{len(names)} controllers gone from this bus that were on it before "
                 f"({', '.join(names)}): check that bus's power and its CAN chain first "
                 "(a CANivore or connector off), then their CAN ids")
        out.append({
            "id": f"detector_missing_{len(names)}_controllers", "claim": _cut_claim(claim, keep=names),
            "metric": {"name": "earlier logs they were on the bus (most)", "value": value,
                       "unit": "logs"},
            "severity": "fault" if any(a.get("severity") == "fault" for a, _ in items) else "warn",
            "subsystem": None, "series": None,
            "evidence": [{"tool": "anomalies", "args": dict(args), "value": value}]})
    _tidy_ids({"findings": out})
    return out


def _code_wrote(pre: list[dict]) -> str:
    """The findings prompt's tail when code wrote the detector's findings."""
    return ("\nLeave out StatorCurrLimit / SupplyCurrLimit (by design) and a BridgeBrownout "
            "latched on most controllers (power-on) unless the fault guide's conditions hold."
            "\nALREADY ON THE BOARD, written by code from the anomaly detector (don't write "
            "these again):\n" + "\n".join(f"- {f['claim']}" for f in pre[:10])
            + "\nWrite 1 to 4 findings they don't cover: a shared cause behind several of "
            "them if the data shows one (one finding, citing the call that shows it), or "
            "anything else the crew should act on.")


def _cut_claim(claim: str, keep: list[str] | None = None) -> str:
    """≤ 200 characters at a word; with `keep`, the advice gives way first so
    every device named stays named (coverage reads the claim)."""
    if len(claim) <= 200:
        return claim
    if keep:
        listed = f"{len(keep)} controllers gone from this bus ({', '.join(keep)})"
        if len(listed) <= 160:
            return listed + ": its power or CAN chain"
    return claim[:199].rsplit(" ", 1)[0].rstrip(",;:(") + "…"


def _flagged(ledger) -> list[dict]:
    """The detector items that must be on the board: faults and must_cover."""
    return [a for c in ledger.calls if c.tool == "anomalies"
            for a in c.result.get("anomalies") or []
            if a.get("severity") == "fault" or a.get("must_cover")]


def _carries_flag(f: dict, ledger) -> bool:
    """Is finding `f` about something the detector requires on the board (a
    fault, or a must_cover item)? Then no lesson may drop it."""
    text = checks._norm(" ".join([f.get("claim", ""), (f.get("metric") or {}).get("name", ""),
                                  f.get("id", "")]))
    return any(checks.covered(a, text) for c in ledger.calls if c.tool == "anomalies"
               for a in c.result.get("anomalies") or []
               if a.get("severity") == "fault" or a.get("must_cover"))


def _short_claim(a: dict) -> str:
    """The detector's finding in one sentence, what matters first, for the kinds
    whose detail is too long for a claim's 200 characters."""
    kind = a.get("kind")
    if kind == "loop_overrun":
        parts = ", ".join(p["part"] for p in (a.get("slow_parts") or [])[:2])
        return (f"The robot loop stalled past 100 ms in {a.get('stall_s')} of "
                f"{a.get('enabled_s')} enabled s (longest {a.get('worst_ms'):g} ms)"
                + (f"; slowest: {parts}" if parts else "")
                + ": check what they do each loop.")
    if kind in ("partner_idle", "partner_mismatch"):
        return (f"{a.get('device')} turns with {a.get('partner')} but drew "
                f"{a.get('value'):g} A to its {a.get('partner_value'):g} A: "
                + ("check its power, leads and follower setup." if kind == "partner_idle"
                   else "check the belt or gears between them and the motor."))
    return ""


def _wear_cards(board: dict, insights: dict, ledger, toolbox: Toolbox | None = None) -> None:
    """Every wear finding on the board (Brayden, 2026-10-18: wear through the
    whole analysis). The designer chooses cards and charts; a wear finding it
    left out gets its card from the finding's own figures (exact by
    construction; colour from `assign_status` like any card), and, while
    there's room for one, its motor's trend chart, fetched by code (the
    designer drew none on the first real run)."""
    items = checks.wear_items(ledger)
    worn = [f for f in insights.get("findings") or []
            if any(checks.about_wear(f, a) for a in items)]
    shown = {c.get("finding") for r in ("vitals", "faults") for c in board.get(r) or []}
    for f in worn:
        if f.get("id") in shown:
            continue
        m = f.get("metric") or {}
        a = next(a for a in items if checks.about_wear(f, a))
        label = {"load_per_speed": "load", "heat_vs_peers": "heat",
                 "faults_vs_peers": "faults"}.get(a.get("measure"), "wear")
        card = {"label": f"{a.get('device')} {label}"[:28], "value": f"{m.get('value'):g}"[:12],
                "unit": str(m.get("unit") or "")[:8], "status": "warn", "finding": f.get("id"),
                "series": None}
        if isinstance(m.get("compare_to"), (int, float)):
            card["detail"] = f"{m['compare_to']:g} {m.get('unit') or ''} before".strip()[:80]
        vitals, faults = board.setdefault("vitals", []), board.setdefault("faults", [])
        if len(vitals) < 8:
            vitals.append(card)
        elif len(faults) < 6:
            faults.append(card)
        else:
            calm = [i for i, c in enumerate(vitals) if c.get("status") in ("ok", "idle")]
            if calm:
                vitals[calm[-1]] = card
        shown.add(f.get("id"))
    if toolbox is None:
        return
    charts = board.setdefault("charts", [])
    drawn = {c.get("finding") for c in charts}
    options = [o for o in checks.chart_options(insights, ledger)
               if str(o["source"].get("signal", "")).startswith("wear:")]
    for o in options:
        if len(charts) >= 4 or o["finding"] in drawn:
            continue
        a = next((a for a in items if a.get("signal") == o["source"]["signal"]
                  and a.get("can_id") == o["source"].get("can_id")), {})
        chart = {"kind": "sessions", "finding": o["finding"], "source": dict(o["source"]),
                 "title": f"{a.get('device', 'Motor')} {a.get('measure', 'wear')} by log"[:48],
                 "unit": str(a.get("unit") or "")[:8], "why": "wear trend, added by code"}
        chart["data"] = _chart_data(toolbox, chart)
        if chart["data"].get("sessions"):
            charts.append(chart)
            drawn.add(o["finding"])


def _drop_unwanted(insights: dict, ledger) -> None:
    """
    Leave out what the crew rated not useful, by code (an 8B model kept
    writing them despite the guide): current-limit findings without heat,
    and a BridgeBrownout latched on most controllers while the battery stayed
    healthy (power-on). Only drops: never changes a figure, and never leaves
    a run with no findings.
    """
    lows = [v.get("value") for c in ledger.calls if c.tool == "session_overview"
            for v in c.result.get("vitals") or []
            if "sag" in str(v.get("label", "")).lower() and isinstance(v.get("value"), (int, float))]
    healthy = bool(lows) and min(lows) >= 7.5
    def unwanted(f: dict) -> bool:
        words = checks._norm(" ".join([f.get("claim", ""), (f.get("metric") or {}).get("name", ""),
                                       f.get("id", "")]))
        if ("statorcurrlimit" in words or "supplycurrlimit" in words) and "temp" not in words:
            return True
        n = (f.get("metric") or {}).get("value")
        return ("bridgebrownout" in words and healthy and isinstance(n, (int, float)) and n >= 3)
    kept = [f for f in insights.get("findings") or [] if not unwanted(f)]
    if kept:
        insights["findings"] = kept


def _must_cover(ledger) -> str:
    """
    The rules an 8B model forgot by the time it wrote (real logs, 2026-10-05):
    restated where it writes, naming the detector's faults it must explain.
    """
    flags = [a for c in ledger.calls if c.tool == "anomalies"
             for a in c.result.get("anomalies") or []
             if a.get("severity") == "fault" or (a.get("must_cover") and a.get("kind") != "wear")]
    worn = [a for c in ledger.calls if c.tool == "anomalies"
            for a in c.result.get("anomalies") or [] if a.get("kind") == "wear"]
    text = ("\nLeave out StatorCurrLimit / SupplyCurrLimit (by design) and a "
            "BridgeBrownout latched on most controllers (power-on) unless the fault "
            "guide's conditions hold.")
    if flags:
        seen, lines = set(), []
        for a in flags:
            key = (a.get("device"), a.get("kind"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {a.get('device')}: {a.get('kind')} (severity "
                         f"{a.get('severity')}, metric {a.get('value')} {a.get('unit') or ''}), "
                         f"{str(a.get('detail', ''))[:160]}")
        text += ("\nFIRST, one finding for each of these (the anomaly detector flagged them; "
                 "the severity it gives; evidence {\"tool\": \"anomalies\", \"args\": "
                 "{\"session_uid\": ...}}), then anything else:\n" + "\n".join(lines[:8]))
    if worn:
        lines = [f"- {a.get('device')}: {a.get('measure')}, {a.get('value')} {a.get('unit')} "
                 f"against {(a.get('normal') or {}).get('median')} before; "
                 f"{str(a.get('detail', ''))[:120]}" for a in worn[:6]]
        text += "\nName every motor a warning lists, and the slow parts a loop_overrun lists."
        text += ("\nAlso one finding for each wear warning (a motor getting worse across "
                 "logs; severity warn; metric value the recent figure, compare_to the earlier "
                 "one; evidence {\"tool\": \"anomalies\", \"args\": {\"session_uid\": "
                 "...}}):\n" + "\n".join(lines))
    return text


def _tidy_ids(insights: dict) -> None:
    """A finding's id is a label, not a figure: make it fit the schema
    (lowercase, underscores, ≤ 40, unique) rather than reject a run over it."""
    import re
    seen: set[str] = set()
    for n, f in enumerate(insights.get("findings") or [], 1):
        if not isinstance(f, dict):
            continue
        fid = re.sub(r"[^a-z0-9_]+", "_", str(f.get("id") or "").lower()).strip("_")[:40]
        fid = fid or f"finding_{n}"
        while fid in seen:
            fid = (fid[:36] + f"_{n}")[:40]
        seen.add(fid)
        f["id"] = fid


def _shrink(obj, max_chars: int):
    """`obj` as JSON within `max_chars`: halve its longest list until it fits,
    saying so (`truncated`, `shown_of`). The ledger keeps the whole result."""
    import copy
    obj = copy.deepcopy(obj)
    for _ in range(40):
        if len(_dumps(obj)) <= max_chars:
            return obj
        lists: list[tuple[int, dict, str]] = []

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if isinstance(x, list) and len(x) > 1:
                        lists.append((len(_dumps(x)), v, k))
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        walk(obj)
        if not lists:
            break
        _size, parent, key = max(lists, key=lambda t: t[0])
        full = parent.get(f"{key}_shown_of") or len(parent[key])
        parent[key] = parent[key][:max(1, len(parent[key]) // 2)]
        parent[f"{key}_shown_of"] = full
        parent["truncated"] = True
    return obj


def _fit_primer(parts: list[tuple[str, dict]], max_chars: int) -> list[str]:
    """The up-front results as text, shrunk together to fit `max_chars`
    (six logs recorded together made a 28.5k-character primer, 2026-10-21)."""
    total = sum(len(_dumps(o)) for _n, o in parts)
    if total > max_chars:
        scale = max_chars / total
        parts = [(n, _shrink(o, max(400, int(len(_dumps(o)) * scale)))) for n, o in parts]
    return [f"{n} result:\n{_dumps(o)}" for n, o in parts]


def _size(msgs: list[dict], extra_chars: int = 0) -> int:
    chars = extra_chars + sum(len(m.get("content") or "") + 40 for m in msgs)
    chars += sum(len(_dumps(m["tool_calls"])) for m in msgs if m.get("tool_calls"))
    return int(chars / CHARS_PER_TOKEN)


def _condensed(content: str) -> str:
    try:
        obj = json.loads(content)
    except (TypeError, ValueError):
        return content[:200]
    if not isinstance(obj, dict) or obj.get("condensed"):
        return content
    keep = {k: v for k, v in obj.items() if not isinstance(v, (list, dict)) or k == "args"}
    return _dumps(keep | {"condensed": "shown earlier, condensed to fit the context; call "
                                       "again with narrower arguments if you need it"})


def _fit(msgs: list[dict], budget: int, extra_chars: int = 0) -> int:
    """
    Keep a conversation inside `budget` tokens before it's sent: condense
    tool results oldest first (their scalars and args stay), then earlier
    rejected JSON attempts. The ledger keeps every result whole, so the
    checks are unchanged; only what the model re-reads gets shorter.
    """
    if _size(msgs, extra_chars) <= budget:
        return 0
    # Over: condense down to well under it, not just under. Every edit to an
    # earlier message throws away the engine's cached prompt from there on,
    # so trimming one result a turn re-read ~14k tokens every turn (a real
    # six-log run: condensed 13 times, 948 s). One deep cut keeps the prefix
    # for the turns after it.
    budget = int(budget * CONDENSE_TO)
    n = 0
    tools_at = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    for i in tools_at:
        if _size(msgs, extra_chars) <= budget:
            return n
        before = msgs[i]["content"]
        msgs[i]["content"] = _condensed(before)
        n += msgs[i]["content"] != before
    attempts = [i for i, m in enumerate(msgs) if m["role"] == "assistant"
                and str(m.get("content") or "").lstrip().startswith("{")][:-1]
    for i in attempts:
        if _size(msgs, extra_chars) <= budget:
            break
        msgs[i]["content"] = "(an earlier attempt, rejected for the problems below)"
        n += 1
    return n


_RECOVERABLE = ("exceeds the available context", "context size", "repeat limit",
                "context length", "too long")


def _chat(llm, model: str, msgs: list[dict], budget: int, stats: "Stats", extra_chars: int,
          **kw):
    """`llm.chat` inside the budget, with one more try (condensed harder) when
    the engine says the request was too long or the model looped."""
    stats.condensed += _fit(msgs, budget, extra_chars)
    try:
        return llm.chat(model, msgs, **kw)
    except OllamaError as e:
        if not any(s in str(e).lower() for s in _RECOVERABLE):
            raise
        stats.recovered.append(str(e)[:160])
        stats.condensed += _fit(msgs, int(budget * 0.7), extra_chars)
        kw["temperature"] = max(0.5, float(kw.get("temperature") or 0))
        kw["presence_penalty"] = LOOP_PENALTY
        return llm.chat(model, msgs, **kw)


def _cut_off(reply, cap: int) -> str:
    """The retry's words for an answer that ran into its token cap: a loop or
    too much, either way the JSON is unfinished (a real run's findings ran to
    line 280 and were rejected as invalid JSON, 2026-10-21)."""
    return (f"your answer was cut off at {cap} tokens before it finished. Write it again, "
            "shorter: at most 8 findings, each claim one sentence, and don't repeat yourself")


def _parse(content: str) -> tuple[dict | None, str]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        return None, f"not valid JSON: {e}"
    return (value, "") if isinstance(value, dict) else (None, "not a JSON object")


def analyse(toolbox: Toolbox, sink: Sink, llm: Ollama, session_uids: list[str], *,
            analyst: str, designer: str | None = None, question: str = DEFAULT_QUESTION,
            max_tool_calls: int = 12, retries: int = 2,
            on_step: Callable[[str], None] | None = None,
            grounding: str = "full", lessons: list[str] | None = None,
            think_tools: bool = False, brief: str = "",
            playbook_lessons: list[dict] | None = None,
            ctx_tokens: int = 16384, csa_confirmed: dict[str, int] | None = None,
            diagnose_issues: bool = False, publish_early: bool = False,
            on_board: Callable[[str], None] | None = None) -> RunResult:
    """
    Run the whole pipeline once. Never raises for a model or tool problem.

    `grounding` "full" hands the analyst each log's overview, fault summary,
    subsystems and match up front (run by code, in the ledger like any tool
    call); "overview" is the v2 behaviour, for comparison (`tools/ai_eval.py`).
    `lessons` are the crew's verdicts on earlier findings (`feedback.lessons`).
    `think_tools` lets Qwen3 think while it chooses tools (slower).
    `brief` is the crew's robot brief (`brief.for_model()`): the season, the
    mechanisms and what normal looks like, read before any data.
    `playbook_lessons` is `playbook.active()`: the lessons relevant to this
    log's data are given, and enforced ones applied by code (`playbook.py`).
    `ctx_tokens` is the engine's context; every request is kept inside it.
    `diagnose_issues`: the CSA step (`diagnose.py`): every issue in the
    recording (`incidents`), worst first, diagnosed on its own;
    `csa_confirmed` is how often the crew confirmed each cause before.
    `publish_early`: the CSA's diagnoses (and the detector's flags) go on a
    board laid out by code as soon as they're made (`layout.py`); the full
    board replaces it under the same uid. `on_board(uid)` is told of every
    board published, the early one included.
    """
    from app.ai import playbook
    budget = max(4096, int(ctx_tokens) - OUTPUT_TOKENS)
    designer = designer or analyst
    step = on_step or (lambda _msg: None)
    run_id = sink.start_run(session_uids, question, analyst)
    res = RunResult(run_id, "failed")
    ledger = checks.Ledger()
    analyst_msgs: list[dict] = []
    designer_msgs: list[dict] = []
    csa_findings: list[dict] = []
    res.transcript = {"analyst": analyst_msgs, "designer": designer_msgs}

    def finish(status: str, reason: str = "") -> RunResult:
        res.status, res.reason = status, reason
        sink.finish_run(run_id, status=status, reject_reason=reason or None,
                        designer=designer if res.board else None,
                        insights=res.insights, board=res.board, board_uid=res.board_uid,
                        transcript=res.transcript, stats=res.stats.__dict__)
        step(f"{status}" + (f": {reason}" if reason else ""))
        return res

    try:
        # ── grounding: the overview of each session, run by code ─────────
        context_text: list[str] = []
        parts: list[tuple[str, dict]] = []
        given: set[str] = set()           # (tool, args) handed over up front
        for uid in session_uids:
            ov = toolbox.call("session_overview", {"session_uid": uid})
            if "error" in ov:
                return finish("failed", ov["error"])
            ledger.add("session_overview", ov)
            meta = ov.get("session") or {}
            context_text += [str(meta.get("name") or ""), str(meta.get("match_key") or "")]
            parts.append(("session_overview", ov))
            given.add(_dumps(["session_overview", ov.get("args")]))
            if grounding != "full":
                continue
            # What a small model rarely thinks to ask for, fetched by code.
            # Each is optional: a toolbox without one (home's MCP before it
            # adds match_phases) just doesn't contribute it.
            def optional(name: str) -> dict:
                try:
                    out = toolbox.call(name, {"session_uid": uid})
                except Exception as e:          # an unknown tool must not fail the run
                    return {"error": f"{type(e).__name__}: {e}"}
                return out if isinstance(out, dict) else {"error": "not a result"}

            # The detector first: what isn't normal against this robot's own
            # history (app/robot/anomaly.py). The analyst's main job is to
            # explain these to the crew.
            # An empty result is said, not left out: left out, the model asked
            # for it again (13 repeat `faults` calls in 62 runs, 2026-10-21).
            an = optional("anomalies")
            if "error" not in an:
                ledger.add("anomalies", an)
                given.add(_dumps(["anomalies", an.get("args")]))
                parts.append(("anomalies", an if (an.get("anomalies") or an.get("note"))
                              else {"args": an.get("args"), "anomalies": [],
                                    "note": "nothing unusual against this robot's earlier logs"}))
            ph = optional("match_phases")
            if "error" not in ph and ph.get("never_enabled") is not None:
                ledger.add("match_phases", ph)
                given.add(_dumps(["match_phases", ph.get("args")]))
                parts.append(("match_phases", ph))
            fl = optional("faults")
            if "error" not in fl:
                ledger.add("faults", fl)
                given.add(_dumps(["faults", fl.get("args")]))
                compact = {k: fl[k] for k in ("args", "summary", "by_device") if k in fl}
                parts.append(("faults", compact if fl.get("summary") else
                              {"args": fl.get("args"), "summary": [],
                               "note": "no faults latched in this log"}))
            sub = optional("subsystems")
            if "error" not in sub:
                ledger.add("subsystems", sub)
                given.add(_dumps(["subsystems", sub.get("args")]))
                if sub.get("subsystems"):
                    parts.append(("subsystems", sub))
            mc = optional("match_context")
            if "error" not in mc and mc.get("match"):
                ledger.add("match_context", mc)
                parts.append(("match_context", mc))
        context = checks.context_numbers(*context_text)

        # ── the CSA: every issue in the recording, diagnosed on its own ────
        if diagnose_issues and grounding == "full":
            csa_findings = _diagnose(toolbox, llm, analyst, session_uids[0], ledger, res,
                                     step, csa_confirmed, brief)
            if csa_findings and publish_early:
                _publish_early(toolbox, sink, ledger, res, run_id, csa_findings, session_uids,
                               question, analyst, context, context_text, step, on_board)
            if csa_findings:
                parts.append(("csa_diagnoses", {
                    "note": "already diagnosed and on the board: don't repeat these; add "
                            "what they don't cover",
                    "diagnoses": [{"id": f["id"], "claim": f["claim"]} for f in csa_findings]}))
        specs = toolbox.specs()
        spec_chars = len(_dumps(specs))
        primer = _fit_primer(parts, int(budget * PRIMER_SHARE * CHARS_PER_TOKEN))

        # ── 1. analyst ────────────────────────────────────────────────────
        step(f"analyst ({analyst}) reading the log")
        taught = ""
        # The one fact that changes how everything else reads, stated first and
        # plainly: a rule buried in the system prompt wasn't enough for an 8B
        # model (it called 80 °F "near the limit" in a disabled log).
        idle = [u for u in session_uids
                if any(c.tool == "match_phases" and c.args.get("session_uid") == u
                       and c.result.get("never_enabled") for c in ledger.calls)]
        if idle and len(idle) == len(session_uids):
            taught += ("IMPORTANT: the robot was never enabled in this log (match_phases: "
                       "never_enabled). It's a pit or bench recording, not a match. Lead "
                       "with that, report only what matters on a robot sitting in the pit "
                       "(latched faults, a device that's missing or misbehaving), and don't "
                       "call normal readings a problem.\n\n")
        if brief:
            taught += ("About this robot and season, from the team (trust it over "
                       "general assumptions):\n" + brief.strip() + "\n\n")
        # The playbook: only the lessons this log's data touches (playbook.select).
        chosen = playbook.select(playbook_lessons or [], "\n".join(primer))
        res.stats.playbook = [x["id"] for x in chosen]
        taught += playbook.as_prompt(chosen)
        if lessons:
            taught += ("What this crew said about earlier findings (learn from it; "
                      "don't repeat what they called wrong or not useful):\n- "
                      + "\n- ".join(lessons) + "\n\n")
        analyst_msgs += [
            {"role": "system", "content": ANALYST_SYSTEM
                + ("" if think_tools else "\n/no_think")},
            {"role": "user", "content": f"Sessions: {', '.join(session_uids)}\n"
                                        f"Question: {question}\n\n" + taught
                                        + "\n\n".join(primer)},
        ]
        while True:
            reply = _chat(llm, analyst, analyst_msgs, budget, res.stats, spec_chars,
                          tools=specs, max_tokens=TOOL_TURN_TOKENS,
                          **({"think": True} if think_tools else {}))
            res.stats.count(reply, "analyst")
            analyst_msgs.append({"role": "assistant", "content": reply.content,
                                 **({"tool_calls": reply.message.get("tool_calls")}
                                    if reply.tool_calls else {})})
            if not reply.tool_calls:
                break
            for c in reply.tool_calls:
                if res.stats.tool_calls >= max_tool_calls:
                    out = {"args": c["arguments"], "error": "tool budget spent; write findings"}
                elif _dumps([c["name"], c["arguments"]]) in given:
                    # Already in front of it: say so in a line, not the whole
                    # result again (the context is the budget).
                    res.stats.tool_calls += 1
                    res.stats.repeats += 1
                    out = {"args": c["arguments"], "note": f"the {c['name']} result for "
                           "this is already given above; use it"}
                else:
                    res.stats.tool_calls += 1
                    step(f"tool {c['name']}")
                    out = toolbox.call(c["name"], c["arguments"])
                    if "error" not in out:
                        ledger.add(c["name"], out)
                        given.add(_dumps([c["name"], out.get("args")]))
                analyst_msgs.append({"role": "tool", "tool_name": c["name"],
                                     "tool_call_id": c.get("id") or "",
                                     "content": _dumps(out)})
            if res.stats.tool_calls >= max_tool_calls:
                break
            if res.stats.repeats >= MAX_REPEATS:
                # It keeps asking for what it was given: it has what it needs
                # (11 of 12 calls on a real six-log run, 7.5 minutes).
                break

        step("analyst writing findings")
        # Many detector items: code writes them (exact by construction) and
        # the model adds what they don't say. Asked to restate fifteen, an 8B
        # model wrote 9k characters of JSON three times, failed the figure
        # checks each time, and code put the detector's in anyway (a real
        # six-log run, 2026-11-03: 677 s, most of it those three attempts).
        items = {(a.get("device"), a.get("kind"), a.get("measure"))
                 for a in _flagged(ledger) + checks.wear_items(ledger)}
        pre = _detector_findings(ledger, {"findings": []}) \
            if len(items) > CODE_WRITES_OVER else []
        res.stats.code_wrote = [f["id"] for f in pre]

        def cover(found: dict) -> list[str]:
            return checks.anomaly_coverage(_merge_csa(found, pre) if pre else found, ledger)

        analyst_msgs.append({"role": "user",
                             "content": FINDINGS_PROMPT % (_dumps(session_uids), question)
                             + (_code_wrote(pre) if pre else _must_cover(ledger))})
        problems: list[str] = []
        cut = False
        for attempt in range(retries + 1):
            reply = _chat(llm, analyst, analyst_msgs, budget, res.stats, 0,
                          format=schema.insight_format(), max_tokens=OUTPUT_TOKENS,
                          temperature=0.1 if attempt == 0 else 0.5,
                          presence_penalty=LOOP_PENALTY if cut else 0.0)
            res.stats.count(reply, "analyst")
            analyst_msgs.append({"role": "assistant", "content": reply.content})
            insights, err = _parse(reply.content)
            cut = reply.truncated
            if cut:
                res.stats.truncated += 1
                insights, err = None, _cut_off(reply, OUTPUT_TOKENS)
            if insights is not None:
                _tidy_ids(insights)
                _drop_unwanted(insights, ledger)
                # What the crew rated not useful, left out by code (playbook.py).
                res.stats.filler = playbook.drop_filler(insights)
                res.stats.enforced = playbook.enforce(
                    insights, playbook_lessons or [], protect=lambda f: _carries_flag(f, ledger))
            problems = [err] if insights is None else (
                schema.validate(insights, schema.load("insight")))
            if insights is not None and not problems:
                problems = checks.check_findings(insights, ledger, session_uids, context)
                problems += cover(insights)
                _drop_bad_series(toolbox, insights, session_uids)
            res.insights = insights
            if not problems:
                break
            if attempt < retries:
                step(f"findings rejected ({len(problems)}); asking once more")
                analyst_msgs.append({"role": "user", "content":
                    "These problems were found:\n- " + "\n- ".join(problems[:12])
                    + "\n\nWrite the whole JSON again. For each problem: use a figure "
                    "exactly as a tool result above gives it and cite that call, or drop "
                    "the finding. Don't repeat a rejected figure."})
        # The detector finds, the model explains when it can. Findings that are
        # sound but leave out a detector fault aren't thrown away with the run:
        # the detector's own finding goes on the board (an 8B model ignored
        # TalonFX 17 through every retry on real logs, 2026-10-05).
        # Out of retries: drop only the findings that still fail (each problem
        # names its finding), keep the sound ones. Dropping changes no figure.
        # One mistyped tool name sank ten good findings on a real run.
        if insights is not None and problems:
            bad = {int(m.group(1)) for p in problems
                   for m in [re.match(r"finding (\d+) \(", p)] if m}
            other = [p for p in problems if not re.match(r"finding \d+ \(", p)
                     and not p.startswith(_DETECTOR_GAPS)]
            if bad and not other:
                kept = [f for i, f in enumerate(insights.get("findings") or [], 1) if i not in bad]
                if kept:
                    insights["findings"] = kept
                    res.stats.dropped = sorted(bad)
                    problems = (checks.check_findings(insights, ledger, session_uids, context)
                                + cover(insights))
                elif pre:
                    insights["findings"] = []
                    res.stats.dropped = sorted(bad)
                    problems = cover(insights)
        if insights is not None and pre:
            res.insights = insights = _merge_csa(insights, pre)
        coverage = [p for p in problems if p.startswith(_DETECTOR_GAPS)]
        if insights is not None and problems and len(coverage) == len(problems):
            # A finding that names a flagged item but not what was found gives
            # way to the detector's own, which says it all.
            for a in _flagged(ledger):
                if checks._missing_specifics(a, insights):
                    insights["findings"] = [
                        f for f in insights.get("findings") or []
                        if not checks.covered(a, checks._finding_text(f))]
            added = _detector_findings(ledger, insights)
            # The model's own finding with the detector's exact figure is the
            # same thing said less completely (a real run had both,
            # 2026-11-03): the detector's replaces it. Only drops.
            figures = {(f["metric"].get("value"), f["metric"].get("unit") or "") for f in added}
            insights["findings"] = added + [
                f for f in insights.get("findings") or []
                if ((f.get("metric") or {}).get("value"),
                    (f.get("metric") or {}).get("unit") or "") not in figures]
            res.insights = insights
            problems = checks.anomaly_coverage(insights, ledger)
        if problems and csa_findings:
            # The CSA's findings are sound by construction: a board of them (and
            # the detector's own) beats no board when the analyst's didn't pass.
            insights = {"schema": 1, "session_uids": session_uids,
                        "question": question, "findings": []}
            insights["findings"] = _detector_findings(ledger, insights)
            problems = [p for p in checks.anomaly_coverage(insights, ledger)]
            res.stats.recovered.append("the analyst's findings didn't pass; the CSA's and "
                                       "the detector's published")
        if problems:
            return finish("rejected", "findings: " + "; ".join(problems[:6]))
        if csa_findings:
            res.insights = insights = _merge_csa(insights, csa_findings)

        # ── 3. designer ───────────────────────────────────────────────────
        step(f"designer ({designer}) laying out the board")
        options = checks.chart_options(
            res.insights, ledger, lambda u, sig, col: _extreme(toolbox, u, sig, col))
        worst = min(res.insights["findings"],
                    key=lambda f: checks.SEVERITY_ORDER.get(f.get("severity"), 9))
        designer_msgs += [
            {"role": "system", "content": DESIGNER_SYSTEM},
            {"role": "user", "content": _dumps({
                "session": {"name": context_text[0], "match_key": context_text[1] or None},
                "headline_finding": {k: worst.get(k) for k in ("id", "claim", "severity")},
                "findings": res.insights, "chart_options": options})},
        ]
        board: dict | None = None
        for attempt in range(retries + 1):
            reply = _chat(llm, designer, designer_msgs, budget, res.stats, 0,
                          format=schema.designer_format(), max_tokens=DESIGNER_TOKENS,
                          temperature=0.2 if attempt == 0 else 0.6)
            res.stats.count(reply, "designer")
            designer_msgs.append({"role": "assistant", "content": reply.content})
            draft, err = _parse(reply.content)
            if reply.truncated:
                res.stats.truncated += 1
                draft, err = None, _cut_off(reply, DESIGNER_TOKENS)
            if draft is None:
                problems = [err]
            else:
                board, problems = _lay_out(toolbox, draft, res.insights, ledger, options,
                                           session_uids, analyst, designer, context)
            if not problems:
                break
            board = None
            if attempt < retries:
                step(f"board rejected ({len(problems)}); asking once more")
                designer_msgs.append({"role": "user", "content":
                    "These problems were found:\n- " + "\n- ".join(problems[:12])
                    + "\n\nWrite the whole JSON again, changing what they name. Use only "
                    "figures from the findings; leave out anything you can't."})
        if board is None:
            # The findings passed; only the wording of the board didn't. Code
            # lays it out (layout.py) rather than throw the findings away.
            board, more = _code_board(toolbox, res.insights, ledger, options, session_uids,
                                      analyst, context, context_text)
            if board is None:
                return finish("rejected", "board: " + "; ".join((problems + more)[:6]))
            res.stats.recovered.append("the designer's board didn't pass; laid out by code: "
                                       + "; ".join(problems[:2])[:200])

        # ── 4. publish ────────────────────────────────────────────────────
        res.board = board
        res.board_uid = sink.board_uid(session_uids, run_id)
        sink.publish(res.board_uid, board)
        if on_board is not None:
            on_board(res.board_uid)
        return finish("published")
    except OllamaError as e:
        return finish("failed", str(e))
    except Exception as e:         # a bug must not leave the run 'running' forever
        return finish("failed", f"{type(e).__name__}: {e}")


def _diagnose(toolbox, llm, model, session_uid, ledger, res, step, confirmed, brief
              ) -> list[dict]:
    """The CSA step: each issue of the recording, worst first, diagnosed on its
    own (`diagnose.py`); their findings, and their reasoning in the stats."""
    from app.ai import diagnose as dg
    import time as _t
    try:
        inc = toolbox.call("incidents", {"session_uid": session_uid})
    except Exception as e:                     # a toolbox without it (home, before R20)
        inc = {"error": f"{type(e).__name__}: {e}"}
    if not isinstance(inc, dict) or "error" in inc:
        # Said, not silent: a real run lost the whole CSA step to a bug here
        # and the board looked like any other (2026-11-03).
        res.stats.recovered.append("CSA step skipped: " + str(
            inc.get("error") if isinstance(inc, dict) else inc)[:160])
        return []
    ledger.add("incidents", inc)
    out = []
    t0 = _t.monotonic()
    issues = [dict(i, recording_kinds=inc.get("recording_kinds") or [])
              for i in inc.get("issues") or [] if i.get("severity") in ("fault", "warn")]
    # Loop stalls have the detector's loop finding, with its slow parts named
    # (loop_health.py): diagnosing them again would only say it less well.
    if any(a.get("kind") == "loop_overrun" for c in ledger.calls if c.tool == "anomalies"
           for a in c.result.get("anomalies") or []):
        issues = [i for i in issues if i["primary"] not in ("loop_stall", "gc_pause")]
    for n, issue in enumerate(issues[:MAX_DIAGNOSE], 1):
        step(f"CSA: diagnosing {issue['primary']} ({n} of {min(len(issues), MAX_DIAGNOSE)})")
        d = dg.diagnose(toolbox, llm, model, session_uid, issue, ledger,
                        confirmed=confirmed, brief=brief, on_step=step)
        f = dg.to_finding(issue, d, session_uid)
        out.append(f)
        res.stats.incidents.append(d.as_dict() | {"finding": f["id"]})
    res.stats.diagnose_s = round(_t.monotonic() - t0, 1)
    _tidy_ids({"findings": out})
    for f, d in zip(out, res.stats.incidents):
        d["finding"] = f["id"]
    return out


def _merge_csa(insights: dict, csa_findings: list[dict]) -> dict:
    """The CSA's findings with the analyst's: worst first, the analyst's that
    repeat a CSA figure left out, within the contract's 12."""
    figures = {(f["metric"]["value"], f["metric"].get("unit") or "") for f in csa_findings}
    rest = [f for f in insights.get("findings") or []
            if ((f.get("metric") or {}).get("value"), (f.get("metric") or {}).get("unit") or "")
            not in figures]
    taken = {f["id"] for f in csa_findings}
    rest = [dict(f, id=(f["id"] if f["id"] not in taken else f["id"][:36] + "_a")) for f in rest]
    both = csa_findings + rest
    order = checks.SEVERITY_ORDER
    both = sorted(both, key=lambda f: order.get(f.get("severity"), 9))[:12]
    return dict(insights, findings=both)


def _publish_early(toolbox, sink, ledger, res, run_id, csa_findings, session_uids, question,
                   analyst, context, context_text, step, on_board) -> None:
    """The CSA's diagnoses on screen now, laid out by code; the run goes on.
    Anything wrong here only skips the early board."""
    try:
        early = {"schema": 1, "session_uids": session_uids, "question": question,
                 "findings": list(csa_findings)}
        early["findings"] += _detector_findings(ledger, early)
        early = _merge_csa(dict(early, findings=early["findings"][len(csa_findings):]),
                           csa_findings)
        # Code wrote all of them (diagnose.to_finding, _detector_findings), as
        # on the full board, where they skip the analyst's figure checks too;
        # the board check below still holds every figure to them.
        problems = schema.validate(early, schema.load("insight"))
        board = None
        if not problems:
            board, problems = _code_board(toolbox, early, ledger, None, session_uids, analyst,
                                          context, context_text)
        if board is None:
            res.stats.recovered.append("early board skipped: " + "; ".join(problems[:2])[:200])
            return
        board["footer"] = {"left": "First look: the CSA's diagnoses",
                           "right": "the full board follows"}
        res.board, res.board_uid = board, sink.board_uid(session_uids, run_id)
        sink.publish(res.board_uid, board)
        res.stats.early_s = round(time.monotonic() - res.t0, 1)
        step("CSA diagnoses on the board; the analyst goes on")
        if on_board is not None:
            on_board(res.board_uid)
    except Exception as e:                     # never costs the run
        res.stats.recovered.append(f"early board skipped: {type(e).__name__}: {e}"[:160])


_VALUE_UNIT = re.compile(r"^\s*([-+]?\d[\d,]*(?:\.\d+)?%?)\s*([^\d\s][^\d]{0,7})\s*$")


def _split_units(draft: dict) -> None:
    """A card value written with its unit ('6.425 V', '5 logs') becomes the
    figure and its unit: where the unit goes, not what the figure is. Every
    real designer run lost a retry to it (2026-11-03). A unit that
    disagrees with the card's own is left for the check to send back."""
    for region in ("vitals", "faults"):
        for card in draft.get(region) or []:
            if not isinstance(card, dict):
                continue
            m = _VALUE_UNIT.match(str(card.get("value", "")))
            if m and str(card.get("unit") or "").strip() in ("", m.group(2).strip()):
                card["value"], card["unit"] = m.group(1), m.group(2).strip()[:8]


def _lay_out(toolbox: Toolbox, draft: dict, insights: dict, ledger, options: list[dict],
             session_uids: list[str], analyst: str, designer: str, context: list[float]
             ) -> tuple[dict | None, list[str]]:
    """A designer draft (the model's or `layout.draft`'s) made a board and checked."""
    _split_units(draft)
    problems = schema.validate(draft, schema.designer_schema())
    if problems:
        return None, problems
    board = _complete(toolbox, draft, insights, session_uids, analyst, designer)
    _wear_cards(board, insights, ledger, toolbox)
    problems = assign_status(board, insights)
    problems += schema.validate(board, schema.load("board"))
    problems += checks.check_board(board, insights, context)
    problems += checks.check_charts(board, options, session_uids)
    # Charts are optional ("no chart is better than one that doesn't help"):
    # a chart that fails a check is dropped, never the board. Every other
    # problem still sends it back.
    bad = {i for i in range(len(board.get("charts") or []))
           if any(p.startswith((f"charts[{i}]:", f"charts[{i}].")) for p in problems)}
    if bad:
        board["charts"] = [c for i, c in enumerate(board["charts"]) if i not in bad]
        problems = [p for p in problems if not p.startswith("charts[")]
        problems += checks.check_charts(board, options, session_uids)
    return board, problems


def _code_board(toolbox: Toolbox, insights: dict, ledger, options: list[dict] | None,
                session_uids: list[str], analyst: str, context: list[float],
                context_text: list[str]) -> tuple[dict | None, list[str]]:
    """The board laid out by code (`layout.py`), or None and why it failed."""
    from app.ai import layout
    if options is None:
        options = checks.chart_options(
            insights, ledger, lambda u, sig, col: _extreme(toolbox, u, sig, col))
    d = layout.draft(insights, name=context_text[0] if context_text else "",
                     match_key=context_text[1] if len(context_text) > 1 else "",
                     options=options)
    board, problems = _lay_out(toolbox, d, insights, ledger, options, session_uids,
                               analyst, layout.DESIGNER, context)
    return (board, []) if board is not None and not problems else (None, problems)


def _drop_bad_series(toolbox: Toolbox, insights: dict, session_uids: list[str]) -> None:
    """
    A finding's `series` only points a card at a history to draw; it isn't a
    figure. One that names no real per-second series (another session, a
    device that isn't there) is cleared by code, not argued over: that alone
    rejected a good run (tools/ai_eval.py, 2026-10-05). The metric itself is
    still checked against the tools like every figure.
    """
    for f in insights.get("findings") or []:
        ref = f.get("series") if isinstance(f, dict) else None
        if not isinstance(ref, dict):
            continue
        keep = ref.get("session_uid") in session_uids and all(
            ref.get(k) is not None for k in ("device_type", "can_id", "signal"))
        if keep:
            got = toolbox.call("series_1s", {**{k: ref[k] for k in (
                "session_uid", "device_type", "can_id", "signal")}, "max_points": 2})
            keep = "error" not in got and bool(got.get("points"))
        if not keep:
            f["series"] = None


def assign_status(board: dict, insights: dict) -> list[str]:
    """
    Colour is policy, so code applies it: each card takes its finding's
    severity, the headline the worst finding's, and a finding gets one card
    (a later duplicate is dropped). Returns cards naming no finding.
    """
    sev = {f.get("id"): f.get("severity", "idle") for f in insights.get("findings") or []}
    problems, shown = [], set()
    for region in ("vitals", "faults", "subsystems"):
        if region not in board:
            continue
        kept = []
        for card in board[region] or []:
            fid = card.get("finding")
            name = card.get("label") or card.get("name")
            if fid not in sev:
                problems.append(f"{region}: {name!r} names finding {fid!r}; use one of "
                                f"{', '.join(map(str, sev))}")
                card["status"] = "idle"
            elif fid in shown:
                continue
            else:
                shown.add(fid)
                card["status"] = sev[fid]
            kept.append(card)
        board[region] = kept
    if isinstance(board.get("headline"), dict):
        board["headline"]["status"] = min(sev.values(), default="idle",
                                          key=lambda x: checks.SEVERITY_ORDER.get(x, 9))
    # One red region is policy too: gather the fault cards where they fit
    # (both regions hold Readings; faults up to 6, vitals up to 8). The
    # designer placed them in two regions on a real run (2026-10-05).
    red = lambda cards: [c for c in cards or [] if c.get("status") == "fault"]  # noqa: E731
    if red(board.get("vitals")) and red(board.get("faults")):
        allred = red(board["vitals"]) + red(board["faults"])
        calm_v = [c for c in board["vitals"] if c.get("status") != "fault"]
        calm_f = [c for c in board["faults"] if c.get("status") != "fault"]
        if len(allred) + len(calm_f) <= 6:
            board["vitals"], board["faults"] = calm_v, allred + calm_f
        elif len(allred) + len(calm_v) <= 8:
            board["vitals"], board["faults"] = allred + calm_v, calm_f
        else:
            # More faults than a screen holds (11 on a real CAN drop-out): the
            # worst 8, in one region. Every finding stays in the run itself.
            board["vitals"], board["faults"] = (allred + calm_v)[:8], calm_f
    return problems


def _extreme(toolbox: Toolbox, session_uid: str, signal: str, column: str) -> dict | None:
    """The device that set a figure: lowest minimum for v_min, else highest."""
    rows = [r for r in toolbox.call("series_stats", {"session_uid": session_uid,
                                                     "signal": signal}).get("devices") or []]
    key = {"v_min": "min", "v_avg": "mean"}.get(column, "max")
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        return None
    return (min if key == "min" else max)(rows, key=lambda r: r[key])


def _complete(toolbox: Toolbox, draft: dict, insights: dict, session_uids: list[str],
              analyst: str, designer: str) -> dict:
    """The designer's draft plus everything code fills in."""
    evidence, seen = [], set()
    for f in insights.get("findings") or []:
        for ev in f.get("evidence") or []:
            item = {"tool": ev.get("tool", ""), "args": ev.get("args") or {},
                    "value": ev.get("value")}
            key = _dumps(item)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
    board = {
        "schema": 1,
        "session_uid": session_uids[0] if len(session_uids) == 1 else None,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": {"analyst": analyst, "designer": designer},
        **draft,
        "evidence": evidence,
    }
    # An identifier, not a figure: a one-session run has only one session for a
    # card's series or a chart to name, so a missing one is filled (trial 8's
    # designer left it off every chart). A different one is still rejected.
    only = session_uids[0] if len(session_uids) == 1 else None
    if only:
        for region in ("vitals", "faults"):
            for card in board.get(region) or []:
                if isinstance(card.get("series"), dict) and not card["series"].get("session_uid"):
                    card["series"]["session_uid"] = only
        for chart in board.get("charts") or []:
            src = chart.get("source")
            if (isinstance(src, dict) and chart.get("kind") in ("line", "bars")
                    and not src.get("session_uid")):
                src["session_uid"] = only
    for region in ("vitals", "faults"):
        for card in board.get(region) or []:
            s = card.get("series")
            if isinstance(s, dict):
                got = toolbox.call("series_1s", {
                    "session_uid": s["session_uid"], "signal": s["signal"],
                    "device_type": s["device_type"], "can_id": s["can_id"],
                    "column": s.get("column") or "v_max", "max_points": SHAPE_POINTS})
                card["shape"] = [p[1] for p in got.get("points") or []]
    for chart in board.get("charts") or []:
        chart["data"] = _chart_data(toolbox, chart)
    return board


def _chart_data(toolbox: Toolbox, chart: dict) -> dict:
    """Fetch what a chart's source names, through the same tools the analyst used."""
    src, kind = chart.get("source") or {}, chart.get("kind")
    if kind == "line":
        args = {"session_uid": src.get("session_uid"), "signal": src.get("signal"),
                "device_type": src.get("device_type"), "can_id": src.get("can_id"),
                "column": src.get("column") or "v_max", "max_points": 120}
        got = toolbox.call("series_1s", args)
        return {"tool": "series_1s", "args": args, "points": got.get("points") or []}
    if kind == "bars":
        col = src.get("column") or "max"
        args = {"session_uid": src.get("session_uid"), "signal": src.get("signal"),
                "device_type": src.get("device_type"), "can_id": src.get("can_id")}
        got = toolbox.call("series_stats", args)
        bars = [{"label": d.get("device_label") or f"{d['device_type']} {d['can_id']}",
                 "device_type": d["device_type"], "can_id": d["can_id"], "value": d[col]}
                for d in got.get("devices") or [] if d.get(col) is not None]
        bars.sort(key=lambda b: b["value"], reverse=col != "min")    # worst first
        return {"tool": "series_stats", "args": args, "bars": bars[:24]}
    if kind == "sessions" and str(src.get("signal", "")).startswith("wear:"):
        # A wear trend (app/robot/wear.py): one motor's measure, log by log.
        uids = src.get("session_uids") or []
        args = {"session_uid": uids[-1] if uids else None,
                "device_type": src.get("device_type"), "can_id": src.get("can_id")}
        got = toolbox.call("wear", args)
        measure = src["signal"].split(":", 1)[1]
        trend = next((t["trend"] for t in got.get("trends") or []
                      if t.get("measure") == measure), [])
        return {"tool": "wear", "args": args, "sessions": [
            {"session_uid": p["session_uid"], "min": p["value"], "max": p["value"]}
            for p in trend if p["session_uid"] in uids]}
    if kind == "sessions":
        args = {"session_uids": src.get("session_uids") or [], "signal": src.get("signal"),
                "device_type": src.get("device_type"), "can_id": src.get("can_id")}
        got = toolbox.call("compare_sessions", args)
        return {"tool": "compare_sessions", "args": args, "sessions": [
            {"session_uid": x["session_uid"], "min": x.get("min"), "max": x.get("max")}
            for x in got.get("sessions") or []]}
    return {}
