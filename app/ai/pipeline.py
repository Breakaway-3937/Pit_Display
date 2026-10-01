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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Protocol

from app.ai import checks, schema
from app.ai.ollama import Ollama, OllamaError

DEFAULT_QUESTION = "What should the pit crew look at before the next match?"

# Bump whenever a prompt, a check or a tool result changes what a run can say,
# so the scoreboard compares like with like (feedback.scoreboard()). The
# number is recorded in every run's stats.
#   1 — first trials (2026-09-30)
#   2 — faults summary/by_device, code sets colours, chart options from vitals
PROMPT_VERSION = 2
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
- Call the tools you need (usually 2 to 6 calls), then stop calling tools and say \
you're done. Keep your notes between calls short.
/no_think"""

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
(which motor ran hottest); "sessions" for match-to-match change. Set finding to \
the finding's id and say in why, briefly, why this graph. You never write the \
data: it is fetched for you. No chart is better than one that doesn't help.
/no_think"""


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _parse(content: str) -> tuple[dict | None, str]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        return None, f"not valid JSON: {e}"
    return (value, "") if isinstance(value, dict) else (None, "not a JSON object")


def analyse(toolbox: Toolbox, sink: Sink, llm: Ollama, session_uids: list[str], *,
            analyst: str, designer: str | None = None, question: str = DEFAULT_QUESTION,
            max_tool_calls: int = 12, retries: int = 2,
            on_step: Callable[[str], None] | None = None) -> RunResult:
    """Run the whole pipeline once. Never raises for a model or tool problem."""
    designer = designer or analyst
    step = on_step or (lambda _msg: None)
    run_id = sink.start_run(session_uids, question, analyst)
    res = RunResult(run_id, "failed")
    ledger = checks.Ledger()
    analyst_msgs: list[dict] = []
    designer_msgs: list[dict] = []
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
        primer = []
        for uid in session_uids:
            ov = toolbox.call("session_overview", {"session_uid": uid})
            if "error" in ov:
                return finish("failed", ov["error"])
            ledger.add("session_overview", ov)
            meta = ov.get("session") or {}
            context_text += [str(meta.get("name") or ""), str(meta.get("match_key") or "")]
            primer.append(f"session_overview result:\n{_dumps(ov)}")
        context = checks.context_numbers(*context_text)

        # ── 1. analyst ────────────────────────────────────────────────────
        step(f"analyst ({analyst}) reading the log")
        analyst_msgs += [
            {"role": "system", "content": ANALYST_SYSTEM},
            {"role": "user", "content": f"Sessions: {', '.join(session_uids)}\n"
                                        f"Question: {question}\n\n" + "\n\n".join(primer)},
        ]
        specs = toolbox.specs()
        while True:
            reply = llm.chat(analyst, analyst_msgs, tools=specs)
            res.stats.count(reply, "analyst")
            analyst_msgs.append({"role": "assistant", "content": reply.content,
                                 **({"tool_calls": reply.message.get("tool_calls")}
                                    if reply.tool_calls else {})})
            if not reply.tool_calls:
                break
            for c in reply.tool_calls:
                if res.stats.tool_calls >= max_tool_calls:
                    out = {"args": c["arguments"], "error": "tool budget spent; write findings"}
                else:
                    res.stats.tool_calls += 1
                    step(f"tool {c['name']}")
                    out = toolbox.call(c["name"], c["arguments"])
                    if "error" not in out:
                        ledger.add(c["name"], out)
                analyst_msgs.append({"role": "tool", "tool_name": c["name"],
                                     "tool_call_id": c.get("id") or "",
                                     "content": _dumps(out)})
            if res.stats.tool_calls >= max_tool_calls:
                break

        step("analyst writing findings")
        analyst_msgs.append({"role": "user",
                             "content": FINDINGS_PROMPT % (_dumps(session_uids), question)})
        problems: list[str] = []
        for attempt in range(retries + 1):
            reply = llm.chat(analyst, analyst_msgs, format=schema.insight_format(),
                             temperature=0.1 if attempt == 0 else 0.5)
            res.stats.count(reply, "analyst")
            analyst_msgs.append({"role": "assistant", "content": reply.content})
            insights, err = _parse(reply.content)
            problems = [err] if insights is None else (
                schema.validate(insights, schema.load("insight")))
            if insights is not None and not problems:
                problems = checks.check_findings(insights, ledger, session_uids, context)
                problems += _series_problems(toolbox, insights, session_uids)
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
        if problems:
            return finish("rejected", "findings: " + "; ".join(problems[:6]))

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
            reply = llm.chat(designer, designer_msgs, format=schema.designer_format(),
                             temperature=0.2 if attempt == 0 else 0.6)
            res.stats.count(reply, "designer")
            designer_msgs.append({"role": "assistant", "content": reply.content})
            draft, err = _parse(reply.content)
            if draft is None:
                problems = [err]
            else:
                problems = schema.validate(draft, schema.designer_schema())
                if not problems:
                    board = _complete(toolbox, draft, res.insights, session_uids,
                                      analyst, designer)
                    problems = assign_status(board, res.insights)
                    problems += schema.validate(board, schema.load("board"))
                    problems += checks.check_board(board, res.insights, context)
                    problems += checks.check_charts(board, options, session_uids)
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
            return finish("rejected", "board: " + "; ".join(problems[:6]))

        # ── 4. publish ────────────────────────────────────────────────────
        res.board = board
        res.board_uid = sink.board_uid(session_uids, run_id)
        sink.publish(res.board_uid, board)
        return finish("published")
    except OllamaError as e:
        return finish("failed", str(e))
    except Exception as e:         # a bug must not leave the run 'running' forever
        return finish("failed", f"{type(e).__name__}: {e}")


def _series_problems(toolbox: Toolbox, insights: dict, session_uids: list[str]) -> list[str]:
    out = []
    for ref in checks.series_refs(insights):
        if ref["session_uid"] not in session_uids:
            out.append(f"series {ref['signal']!r} names another session")
            continue
        got = toolbox.call("series_1s", {**ref, "max_points": 2})
        if "error" in got or not got.get("points"):
            out.append(f"series {ref['device_type']} {ref['can_id']} {ref['signal']!r} has no "
                       f"per-second data; use null or a series list_signals shows")
    return out


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
    if kind == "sessions":
        args = {"session_uids": src.get("session_uids") or [], "signal": src.get("signal"),
                "device_type": src.get("device_type"), "can_id": src.get("can_id")}
        got = toolbox.call("compare_sessions", args)
        return {"tool": "compare_sessions", "args": args, "sessions": [
            {"session_uid": x["session_uid"], "min": x.get("min"), "max": x.get("max")}
            for x in got.get("sessions") or []]}
    return {}
