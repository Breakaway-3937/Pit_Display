#!/usr/bin/env python3
"""
Check the analysis pipeline (app/ai/). Exit 0/1.

    uv run tools/ai_check.py                          # no model needed
    uv run tools/ai_check.py --model qwen3:30b-a3b    # plus a real run on Ollama

Runs against a **copy** of this machine's database in `.ai_check/`, so the
real one never gets a run row or a board. Needs at least one imported log.

Without a model it proves the parts that make output trustworthy:

* the nine tools answer for a real session, agree with the pit's own
  diagnostics board, and answer bad calls with an error, not an exception;
* the contracts load and the validator catches what they forbid;
* the number checks pass honest findings and boards and reject invented,
  rounded, unsupported and over-red ones;
* charts graph only what the findings stand on, and code fills their data;
* in the app: a run off the GUI thread flashes the strips purple, and the
  Analysis panel records ratings that queue to sync;
* `--mcp` speaks MCP on stdio (the official client was also run against it:
  mcp 2.2.0, 2025-06-18);
* a scripted model through the whole pipeline publishes a board (namespaced
  uid, shapes filled, run logged), and a model that keeps inventing a number
  is rejected and the rejection logged.

With `--model` it also runs the real thing once and prints how long each
stage took and what it cost in tokens. That run passing means the model
produced a board that survived every check; failing prints why.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.console import use_utf8  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


# The log every check here is written against: 50 latched faults, a battery
# low of 11.25 V, a hottest motor of 26 °C. A sync bundle (the database's own
# lossless format), so it imports the same on every machine and the check never
# depends on what this machine's database happens to hold.
FIXTURE = ROOT / "TEST_LOGS" / "fixture_faults_2026-07-29.pitlog.zst"


def scratch_db() -> Path:
    """A fresh install's database (built by the migrations, as CI's seed is),
    never a copy of this machine's own data."""
    scratch = ROOT / ".ai_check"
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "data").mkdir(parents=True)
    os.environ["PIT_DISPLAY_DATA"] = str(scratch)
    # The real model, not a scratch copy of 5 GB: the checkout's own models/.
    os.environ.setdefault("PIT_AI_MODELS", str(ROOT / "models"))
    return scratch


# ── a model that says what it's told ─────────────────────────────────────

class Scripted:
    """Stands in for Ollama: each chat() returns the next scripted reply."""

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, model, messages, *, tools=None, format=None, **_):
        from app.ai.ollama import Reply
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, list):          # tool calls
            calls = [{"name": n, "arguments": a} for n, a in r]
            return Reply(tool_calls=calls, message={"tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in r]})
        return Reply(content=r if isinstance(r, str) else json.dumps(r))


def honest_findings(uid: str) -> dict:
    return {
        "schema": 1, "session_uids": [uid], "question": "check",
        "findings": [
            {"id": "bridge_brownout",
             "claim": "50 latched faults, led by bridge brownouts: check the battery and "
                      "main breaker wiring.",
             "metric": {"name": "latched faults", "value": 50},
             "severity": "fault", "subsystem": None, "series": None,
             "evidence": [{"tool": "session_overview", "args": {"session_uid": uid},
                           "value": 50},
                          {"tool": "faults", "args": {"session_uid": uid}, "value": 1}]},
            {"id": "battery_sag",
             "claim": "Battery sagged to 11.25 V, well clear of the 6.8 V brownout floor.",
             "metric": {"name": "battery sag", "value": 11.25, "unit": "V"},
             "severity": "ok", "subsystem": None,
             "series": {"session_uid": uid, "device_type": "TalonFX", "can_id": 1,
                        "signal": "SupplyVoltage", "column": "v_min"},
             "evidence": [{"tool": "session_overview", "args": {"session_uid": uid},
                           "value": 11.25}]},
            {"id": "hottest_motor",
             "claim": "The hottest motor reached 26 °C.",
             "metric": {"name": "hottest motor", "value": 26.0, "unit": "°C"},
             "severity": "idle", "subsystem": None, "series": None,
             "evidence": [{"tool": "series_stats",
                           "args": {"session_uid": uid, "signal": "DeviceTemp"},
                           "value": 26.0}]},
        ],
    }


def honest_board(uid: str) -> dict:
    """As the designer writes it: cards name findings, code sets every status."""
    return {
        "title": "Practice · brownout",
        "headline": {"title": "Bridge brownout",
                     "sentence": "50 latched faults: check the battery and breaker wiring."},
        "vitals": [
            {"label": "Latched faults", "value": "50", "unit": "",
             "finding": "bridge_brownout", "detail": "bridge brownout first"},
            {"label": "Battery sag", "value": "11.3", "unit": "V", "finding": "battery_sag",
             "detail": "brownout at 6.8 V",
             "series": {"session_uid": uid, "device_type": "TalonFX", "can_id": 1,
                        "signal": "SupplyVoltage", "column": "v_min"}},
            {"label": "Hottest motor", "value": "26", "unit": "°C",
             "finding": "hottest_motor"},
        ],
        "charts": [
            {"kind": "line", "title": "Battery across the match", "unit": "V",
             "finding": "battery_sag", "why": "a sag is a moment; the line shows when",
             "source": {"session_uid": uid, "device_type": "TalonFX", "can_id": 1,
                        "signal": "SupplyVoltage", "column": "v_min"}},
            {"kind": "bars", "title": "Motor temperature", "unit": "°C",
             "finding": "hottest_motor", "why": "which motor, compared with the rest",
             "source": {"session_uid": uid, "signal": "DeviceTemp", "column": "max"}},
        ],
        "footer": {"left": "practice log", "right": "local model"},
    }


# ── the checks ───────────────────────────────────────────────────────────

def tools_section(uid: str) -> None:
    from app.ai import tools
    from app.robot import diagnostics

    print("\nTools, over this database")
    for spec in tools.SPECS:
        name = spec["name"]
        args = {"session_uid": uid, "session_uids": [uid], "signal": "DeviceTemp",
                "device_type": "TalonFX", "can_id": 1}
        props = spec["input_schema"]["properties"]
        out = tools.call(name, {k: v for k, v in args.items() if k in props})
        check(f"{name} answers, echoing its args", "error" not in out and "args" in out,
              str(out.get("error")))
        check(f"{name} fits in a model's context", len(json.dumps(out)) < 40_000,
              f"{len(json.dumps(out))} bytes")

    ov = tools.session_overview(uid)
    sid = tools._session_id(uid)
    board = {r.label: r for r in diagnostics.vitals(sid)}
    agree = all(v["label"] in board and v["status"] == board[v["label"]].status
                for v in ov["vitals"])
    check("session_overview agrees with the pit's own board (labels, status)", agree)
    sag = next((v for v in ov["vitals"] if v["label"] == "Battery sag"), None)
    if sag:
        check("…and on the battery figure", f"{sag['value']:.2f}" == board["Battery sag"].value,
              f"{sag['value']} vs {board['Battery sag'].value}")

    # TheBlueAlliance, as home pushes it (home/REQUESTS.md R5): one event, two
    # matches, one tagged on the log by the crew and one only near it in time.
    from datetime import datetime
    from app.db import db
    started = db.fetchone("SELECT started_at FROM log_session WHERE uid = ?",
                          (uid,))["started_at"]
    near_ms = int(datetime.fromisoformat(started).timestamp() * 1000) + 90_000
    with db.transaction() as c:
        c.execute("INSERT INTO tba_event (uid, name, data) VALUES ('2026chk', 'Check', '{}')")
        for key, ms, red, blue, rs, bs, win in (
                ("qm3", near_ms, ["3937", "16", "118"], ["254", "1678", "971"], 101, 99, "red"),
                ("qm9", None, ["254", "1", "2"], ["3937", "4", "5"], None, None, "")):
            c.execute("INSERT INTO tba_match (uid, event_key, comp_level, match_key, actual_ms, "
                      "data) VALUES (?, '2026chk', 'qm', ?, ?, ?)",
                      (f"2026chk_{key}", key, ms, json.dumps({
                          "event_key": "2026chk", "comp_level": "qm",
                          "match_number": int(key[2:]), "red_teams": red, "blue_teams": blue,
                          "red_score": rs, "blue_score": bs, "winning_alliance": win,
                          "actual_time": ms})))
    got = tools.match_context(uid)
    m = got.get("match") or {}
    check("match_context finds the match nearest the log's start, and says so",
          got["how"] == "by time" and m.get("match_key") == "2026chk_qm3", str(got)[:200])
    check("…our alliance, partners, opponents and the result, from TBA's rows",
          m.get("our_alliance") == "red" and m.get("partners") == ["16", "118"]
          and m.get("result") == "won" and m.get("opponents") == ["254", "1678", "971"])
    with db.transaction() as c:
        c.execute("UPDATE log_session SET match_key = 'qm9' WHERE uid = ?", (uid,))
    got = tools.match_context(uid)
    check("a match key the crew typed wins over the clock",
          got["how"] == "tagged" and got["match"]["match_key"] == "2026chk_qm9"
          and got["match"]["our_alliance"] == "blue" and got["match"]["result"] is None)
    with db.transaction() as c:
        c.execute("UPDATE log_session SET match_key = NULL WHERE uid = ?", (uid,))
        c.execute("DELETE FROM tba_match")
    check("no TBA data is a null match, not an error",
          tools.match_context(uid)["match"] is None)

    check("an unknown session is an answer, not an exception",
          "error" in tools.call("faults", {"session_uid": "nope"}))
    check("an unknown tool is an answer", "error" in tools.call("drop_table", {}))
    check("bad arguments are an answer",
          "error" in tools.call("series_1s", {"session_uid": uid, "signal": "X"}))
    check("arguments a tool doesn't take are dropped, not passed on",
          "error" not in tools.call("faults", {"session_uid": uid, "sql": "DELETE"}))


def schema_section(uid: str) -> None:
    from app.ai import schema
    print("\nContracts")
    check("board and insight schemas load", bool(schema.load("board") and schema.load("insight")))
    fmt = json.dumps(schema.designer_format())
    check("the designer's format has no $ref and no code-filled field",
          "$ref" not in fmt and '"evidence"' not in fmt and '"shape"' not in fmt)
    check("honest findings validate", not schema.validate(honest_findings(uid),
                                                          schema.load("insight")))
    bad = honest_board(uid) | {"vitals": [{"label": "x", "value": "1", "status": "red"}] * 9,
                               "extra": 1}
    errs = schema.validate(bad, schema.load("board"))
    check("the validator catches enum, maxItems and additionalProperties",
          any("not one of" in e for e in errs) and any("more than 8" in e for e in errs)
          and any("unexpected 'extra'" in e for e in errs), "; ".join(errs[:4]))


def checks_section(uid: str) -> None:
    import copy

    from app.ai import checks, schema, tools

    print("\nEvery number is real")
    ledger = checks.Ledger()
    for name, a in (("session_overview", {"session_uid": uid}),
                    ("faults", {"session_uid": uid}),
                    ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})):
        ledger.add(name, tools.call(name, a))
    ctx = checks.context_numbers("practice")
    good = honest_findings(uid)
    probs = checks.check_findings(good, ledger, [uid], ctx)
    check("honest findings pass", not probs, "; ".join(probs))

    def rejects(label: str, mutate, needle: str) -> None:
        f = copy.deepcopy(good)
        mutate(f)
        probs = checks.check_findings(f, ledger, [uid], ctx)
        check(label, any(needle in p for p in probs), "; ".join(probs) or "accepted")

    rejects("a rounded metric is rejected (11.3 for 11.25)",
            lambda f: f["findings"][1]["metric"].update(value=11.3), "metric value")
    rejects("an invented number in a claim is rejected",
            lambda f: f["findings"][2].update(claim="It hit 31.5 °C in the second half."),
            "claim says 31.5")
    rejects("'fault' without a latched fault behind it is rejected",
            lambda f: f["findings"][1].update(severity="fault"), "severity 'fault'")
    rejects("a count that belongs to a different fault is rejected (trial 3's Pigeon)",
            lambda f: f["findings"].append({
                "id": "pigeon", "claim": "Check Pigeon2 25 warnings.",
                "metric": {"name": "Pigeon2 25", "value": 4, "unit": "warnings"},
                "severity": "warn", "subsystem": None, "series": None,
                "evidence": [{"tool": "faults", "args": {"session_uid": uid}, "value": 4}]}),
            "isn't the count")
    fl = next(d for d in ledger.matching("faults", {"session_uid": uid})[0].result["by_device"]
              if d["device"].startswith("Front-Left"))
    rejects(f"a fault's count taken from a device it lists is rejected (trial 3's "
            f"StatorCurrLimit: {fl['n_faults']})",
            lambda f: f["findings"].append({
                "id": "stator", "claim": f"Check StatorCurrLimit on {fl['device']} and others.",
                "metric": {"name": "StatorCurrLimit", "value": fl["n_faults"]},
                "severity": "warn", "subsystem": None, "series": None,
                "evidence": [{"tool": "faults", "args": {"session_uid": uid},
                              "value": fl["n_faults"]}]}),
            "isn't the count")
    pigeon = next(d for d in ledger.matching("faults", {"session_uid": uid})[0]
                  .result["by_device"] if d["device_type"] == "Pigeon2")
    f2 = copy.deepcopy(good)
    f2["findings"].append({
        "id": "pigeon", "claim": f"{pigeon['device']} latched several faults.",
        "metric": {"name": pigeon["device"], "value": pigeon["n_faults"], "unit": "faults"},
        "severity": "warn", "subsystem": None, "series": None,
        "evidence": [{"tool": "faults", "args": {"session_uid": uid},
                      "value": pigeon["n_faults"]}]})
    probs = checks.check_findings(f2, ledger, [uid], ctx)
    check(f"…while its real count passes ({pigeon['device']}: {pigeon['n_faults']})",
          not probs, "; ".join(probs))
    rejects("'fault' for a fault the pit board ranks warn is rejected (trial 4)",
            lambda f: f["findings"].append({
                "id": "field", "claim": "StickyFaultField latched on 9 devices.",
                "metric": {"name": "StickyFaultField", "value": 9},
                "severity": "fault", "subsystem": None, "series": None,
                "evidence": [{"tool": "faults", "args": {"session_uid": uid}, "value": 9}]}),
            "ranks warn")
    rejects("evidence citing a call never made is rejected",
            lambda f: f["findings"][2]["evidence"][0]["args"].update(signal="StatorCurrent"),
            "never made")
    rejects("an evidence value the tool didn't return is rejected",
            lambda f: f["findings"][2]["evidence"][0].update(value=27.0), "value 27")

    from app.ai import pipeline
    board = honest_board(uid)
    check("the designer's draft fits the designer schema (no colours in it)",
          not schema.validate(board, schema.designer_schema()))
    probs = pipeline.assign_status(board, good)
    check("code colours each card from its finding, the headline from the worst",
          not probs and [v["status"] for v in board["vitals"]] == ["fault", "ok", "idle"]
          and board["headline"]["status"] == "fault", "; ".join(probs))
    dup = copy.deepcopy(honest_board(uid))
    dup["faults"] = [{"label": "Bridge brownout", "value": "50", "finding": "bridge_brownout"}]
    stray = copy.deepcopy(honest_board(uid))
    stray["vitals"][2]["finding"] = "made_up"
    check("a second card for the same finding is dropped, not coloured twice",
          not pipeline.assign_status(dup, good) and dup["faults"] == [])
    check("a card naming no finding is a problem",
          any("made_up" in p for p in pipeline.assign_status(stray, good)))
    probs = checks.check_board(board, good, ctx)
    check("an honest board passes", not probs, "; ".join(probs))

    def board_rejects(label: str, mutate, needle: str) -> None:
        b = copy.deepcopy(board)
        mutate(b)
        probs = checks.check_board(b, good, ctx)
        check(label, any(needle in p for p in probs), "; ".join(probs) or "accepted")

    board_rejects("a board figure not in the findings is rejected",
                  lambda b: b["vitals"][2].update(value="29"), "29")
    board_rejects("a card value that isn't just a figure is rejected (trial 4's ':9')",
                  lambda b: b["vitals"][0].update(value=":50"), "just the figure")
    board_rejects("red in two regions is rejected",
                  lambda b: (b.update(faults=[{"label": "Bridge brownout", "value": "50",
                                               "status": "fault"}]),
                             b["vitals"][0].update(status="fault")), "regions")
    board_rejects("a headline calmer than the worst finding is rejected",
                  lambda b: b["headline"].update(status="ok"), "headline.status")
    print("\nCharts: the designer picks, code fetches")
    from app.ai import pipeline
    from app.ai.local import LocalToolbox
    opts = checks.chart_options(good)
    check("chart options come from the findings (a line, and bars per signal)",
          {o["kind"] for o in opts} == {"line", "bars"}
          and {o["source"]["signal"] for o in opts} == {"SupplyVoltage", "DeviceTemp"},
          json.dumps(opts))
    bare = copy.deepcopy(good)
    for f in bare["findings"]:
        f["series"] = None
    via = checks.chart_options(bare, ledger,
                               lambda u, sig, col: pipeline._extreme(LocalToolbox(), u, sig, col))
    line = next((o for o in via if o["kind"] == "line"), None)
    lowest = min(tools.series_stats(uid, "SupplyVoltage")["devices"], key=lambda d: d["min"])
    check("a cited overview figure offers its signal, and a line on the device that set it",
          line is not None and line["source"]["signal"] == "SupplyVoltage"
          and (line["source"]["device_type"], line["source"]["can_id"])
          == (lowest["device_type"], lowest["can_id"]), json.dumps(via))
    opts = opts + via

    def charts_of(b: dict) -> list[str]:
        for c in b.get("charts") or []:
            c["data"] = pipeline._chart_data(LocalToolbox(), c)
        return checks.check_charts(b, opts, [uid])

    b = copy.deepcopy(board)
    probs = charts_of(b)
    check("honest charts pass", not probs, "; ".join(probs))
    line, bars = b["charts"][0]["data"], b["charts"][1]["data"]
    check("a line is filled from series_1s", line["tool"] == "series_1s"
          and 2 < len(line["points"]) <= 120)
    check("bars are filled from series_stats, worst first, labelled by name",
          len(bars["bars"]) > 1 and bars["bars"][0]["value"] >= bars["bars"][-1]["value"]
          and any(x["label"] == "Front-Left Drive Motor Long Name" for x in bars["bars"]))

    def chart_rejects(label: str, mutate, needle: str) -> None:
        b = copy.deepcopy(board)
        mutate(b["charts"])
        probs = charts_of(b)
        check(label, any(needle in p for p in probs), "; ".join(probs) or "accepted")

    b = copy.deepcopy(board)
    del b["charts"][1]["source"]["session_uid"]
    filled = pipeline._complete(LocalToolbox(), b, good, [uid], "m", "m")
    check("a chart missing its session is given the run's only one (an id, not a figure)",
          filled["charts"][1]["source"]["session_uid"] == uid
          and filled["charts"][1]["data"].get("bars"))
    chart_rejects("a chart of a signal the findings don't stand on is rejected",
                  lambda c: c[1]["source"].update(signal="StatorCurrent"), "isn't a signal")
    chart_rejects("a line without a device is rejected",
                  lambda c: c[0]["source"].update(can_id=None), "needs device_type")
    chart_rejects("a line of a device with no data is rejected",
                  lambda c: c[0]["source"].update(can_id=77), "no data")
    chart_rejects("a sessions chart without a compare_sessions call is rejected",
                  lambda c: c.append({"kind": "sessions", "title": "x", "source": {
                      "session_uids": [uid], "signal": "DeviceTemp"}}), "compare_sessions")
    b = copy.deepcopy(board)
    b["charts"][0]["title"] = "Battery under 10.5 V"
    check("a number in a chart title still has to be real",
          any("charts[0].title" in p for p in checks.check_board(b, good, ctx)))

    board_rejects("a trace from a series the analyst never named is rejected",
                  lambda b: b["vitals"][2].update(series={
                      "session_uid": uid, "device_type": "TalonFX", "can_id": 2,
                      "signal": "DeviceTemp"}), "series")


def pipeline_section(uid: str) -> None:
    from app.ai import pipeline
    from app.ai.local import LocalToolbox, SqliteSink
    from app.db import db

    print("\nThe pipeline, with a scripted model")
    sink = SqliteSink(machine_id="pit-aicheck")
    llm = Scripted([
        [("faults", {"session_uid": uid})],
        [("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
        "Done.",
        honest_findings(uid),
        honest_board(uid),
    ])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    check("an honest run publishes", res.status == "published", res.reason)
    row = db.fetchone("SELECT uid, spec FROM analysis_board WHERE uid = ?",
                      (res.board_uid or "",))
    check("the board lands in analysis_board under a machine-namespaced uid",
          row is not None and row["uid"] == f"{uid}:pit-aicheck-{res.run_id}",
          str(res.board_uid))
    if row:
        spec = json.loads(row["spec"])
        sag = spec["vitals"][1]
        check("code filled schema, models, evidence and the card's shape",
              spec["schema"] == 1 and spec["models"]["analyst"] == "scripted"
              and len(spec["evidence"]) >= 3 and len(sag.get("shape", [])) > 2)
        check("…and fetched both charts' data",
              all(c.get("data", {}).get("points") or c.get("data", {}).get("bars")
                  for c in spec.get("charts", [])) and len(spec.get("charts", [])) == 2)
    run = db.fetchone("SELECT status, transcript, stats FROM analysis_run WHERE id = ?",
                      (res.run_id,))
    check("the run is logged with its transcript",
          run is not None and run["status"] == "published"
          and json.loads(run["stats"])["tool_calls"] == 2)

    liar = honest_findings(uid)
    liar["findings"][2]["metric"]["value"] = 31.5
    llm = Scripted([
        [("faults", {"session_uid": uid}),
         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
        "Done.", liar, liar, liar])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    run = db.fetchone("SELECT status, reject_reason FROM analysis_run WHERE id = ?",
                      (res.run_id,))
    check("a model that keeps inventing a number is rejected, after two retries",
          res.status == "rejected" and llm.calls == 5, f"{res.status} after {llm.calls} turns")
    check("…and the rejection is logged with its reason",
          run is not None and run["status"] == "rejected" and "31.5" in run["reject_reason"]
          and "never made" not in run["reject_reason"], run["reject_reason"] if run else "")
    check("…and nothing was published",
          db.fetchone("SELECT COUNT(*) n FROM analysis_board WHERE uid LIKE ?",
                      (f"%-{res.run_id}",))["n"] == 0)


def app_section(uid: str) -> None:
    """The service and the panel, offscreen, with a scripted model and stand-in strips."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PIT_AI_QUIET"] = "1"
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication

    from app.ai import feedback, service
    from app.config import init_config
    from app.db import db
    from app.leds import leds

    print("\nIn the app: the service, the strips, the panel")
    app = QApplication.instance() or QApplication([])
    init_config()

    class Strips:                       # records what the service asks of the LEDs
        alerts: list = []

        def start_alert(self, alert):
            self.alerts.append(alert)

    leds._install(Strips())

    def script():
        return Scripted([[("faults", {"session_uid": uid}),
                          ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
                         "Done.", honest_findings(uid), honest_board(uid)])

    svc = service.init_analysis(llm_factory=script)
    done: list = []
    svc.run_finished.connect(lambda rid, st: done.append((rid, st)))
    loop = QEventLoop()
    svc.run_finished.connect(lambda *_: loop.quit())
    QTimer.singleShot(60_000, loop.quit)
    started = svc.analyse(uid)
    check("a run starts without blocking the GUI thread", started and svc.busy)
    loop.exec()
    check("…and finishes published", bool(done) and done[0][1] == "published", str(done))
    alert = Strips.alerts[-1] if Strips.alerts else None
    check("a published board flashes the strips purple, centre white",
          alert is not None and alert.sides == service.STRIP_PURPLE and alert.centre_white)
    run_id = done[0][0] if done else -1
    check("the run row synced out (outbox)",
          db.fetchone("SELECT COUNT(*) n FROM sync_outbox WHERE tbl = 'analysis_run'")["n"] > 0)

    from app.theme import dark_qss
    from app.widgets.analysis_panel import AnalysisPanel
    from app.widgets.brand_widgets import SelectableChip
    app.setStyleSheet(dark_qss())
    panel = AnalysisPanel()
    panel.resize(900, 1400)
    panel.show()
    app.processEvents()
    chips = panel.findChildren(SelectableChip)
    useful = [c for c in chips if c.property("rating") == "useful"]
    check("the panel shows the run's three findings, each with its rating chips",
          panel._selected == run_id and len(useful) == 3, f"{len(useful)} chips")
    useful[0].click()
    app.processEvents()
    fid = honest_findings(uid)["findings"][0]["id"]
    check("tapping Useful records it", feedback.verdicts(run_id).get(fid, {}).get("rating")
          == "useful")
    again = [c for c in panel.findChildren(SelectableChip)
             if c.property("rating") == "useful" and c.property("finding") == fid]
    again[0].click()
    app.processEvents()
    check("tapping it again clears it", feedback.verdicts(run_id)[fid]["rating"] is None)
    rank = [c for c in panel.findChildren(SelectableChip) if c.property("score") == 4]
    rank[0].click()
    app.processEvents()
    check("ranking the run 4 records it", feedback.verdicts(run_id)[feedback.RUN]["score"] == 4)
    useful = [c for c in panel.findChildren(SelectableChip)
              if c.property("rating") == "useful" and c.property("finding") == fid]
    useful[0].click()
    wrong = [c for c in panel.findChildren(SelectableChip)
             if c.property("rating") == "wrong"
             and c.property("finding") == honest_findings(uid)["findings"][1]["id"]]
    wrong[0].click()
    app.processEvents()
    from app.ai.pipeline import PROMPT_VERSION
    model = db.fetchone("SELECT analyst FROM analysis_run WHERE id = ?", (run_id,))["analyst"]
    row = next((r for r in feedback.scoreboard()
                if r["model"] == model and r["prompt"] == PROMPT_VERSION), None)
    check("the scoreboard counts what the crew said: 1 useful and 1 wrong of 2 rated, rank 4",
          row is not None and row["rated"] == 2 and row["useful"] == 0.5
          and row["wrong"] == 0.5 and row["rank"] == 4.0, str(row))
    fb_uid = db.fetchone("SELECT uid FROM analysis_feedback WHERE run_id = ? AND finding_id = ''",
                         (run_id,))["uid"]
    run_uid = db.fetchone("SELECT uid FROM analysis_run WHERE id = ?", (run_id,))["uid"]
    check("a verdict's uid is its run's uid + '#' + finding, and it's queued to sync",
          fb_uid == f"{run_uid}#" and db.fetchone(
              "SELECT COUNT(*) n FROM sync_outbox WHERE tbl = 'analysis_feedback' AND uid = ?",
              (fb_uid,))["n"] == 1)
    panel.close()

    from app.ai import boards
    from app.widgets.analysis_overlay import AnalysisOverlay
    from app.webcast.state import analysis_board_state
    face = AnalysisOverlay(screen_id="presentation_a")
    face.resize(1920, 1080)
    face.show()
    app.processEvents()
    latest = boards.latest()
    check("the Analysis face shows the newest board, in its own words",
          latest is not None and face._headline()[1] == latest.headline.get("title")
          and len(face._data.vitals) == len(latest.spec.get("vitals") or []))
    check("…with the colours code set (a fault card is a fault on the painter)",
          [r.status for r in face._data.vitals]
          == [v["status"] for v in latest.spec.get("vitals") or []])
    check("…and renders at 1920×1080 with its charts", not face.grab().isNull()
          and len(latest.charts) == 2)
    web = analysis_board_state()
    check("the pit-LAN page gets the same board, headline and filled charts",
          web["headline"]["title"] == latest.headline.get("title")
          and all(c["points"] or c["bars"] for c in web["charts"]))
    face.close()


def mcp_section(uid: str) -> None:
    """`main.py --mcp`, spoken to as a host would: newline-delimited JSON-RPC on stdio."""
    import subprocess
    print("\n--mcp: this machine's logs for Claude Desktop / Claude Code")
    proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), "--mcp"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env={**os.environ})

    def rpc(i, method, params=None):
        msg = {"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        init = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "ai_check", "version": "1"}})
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        check("initialize answers with the version asked for, tools only",
              init["result"]["protocolVersion"] == "2025-06-18"
              and "tools" in init["result"]["capabilities"])
        names = [t["name"] for t in rpc(2, "tools/list")["result"]["tools"]]
        check("it lists the ten tools and the two analysis ones",
              len(names) == 12 and "analysis_scoreboard" in names
              and "match_context" in names, ", ".join(names))
        got = rpc(3, "tools/call", {"name": "faults", "arguments": {"session_uid": uid}})
        sc = got["result"]["structuredContent"]
        check("a tool call returns the same result the analyst gets, structured and as text",
              sc["args"] == {"session_uid": uid} and sc.get("summary")
              and json.loads(got["result"]["content"][0]["text"]) == sc)
        bad = rpc(4, "tools/call", {"name": "faults", "arguments": {"session_uid": "nope"}})
        check("an unknown session is a tool error, not a dead server",
              bad["result"]["isError"] is True)
        runs = rpc(5, "tools/call", {"name": "analysis_runs", "arguments": {"limit": 3}})
        check("analysis_runs carries the crew's verdicts",
              all("verdicts" in r for r in runs["result"]["structuredContent"]["runs"]))
        check("an unknown method is a JSON-RPC error",
              rpc(6, "resources/list").get("error", {}).get("code") == -32601)
    finally:
        proc.stdin.close()
        code = proc.wait(timeout=20)
    check("it exits cleanly when the host hangs up", code == 0, f"exit {code}")


def live_section(uid: str, model: str, designer: str | None, question: str,
                 engine: str = "ollama") -> None:
    from app.ai import pipeline, runtime
    from app.ai.llama import LlamaServer
    from app.ai.local import LocalToolbox, SqliteSink
    from app.ai.ollama import Ollama

    rt = None
    if engine == "llama":
        model = runtime.MODEL["name"]
        print(f"\nA real run on the built-in engine ({runtime.MODEL['file']})")
        if not check("llama-server is here", runtime.binary() is not None,
                     "uv run tools/fetch_llama.py"):
            return
        if not check("the model is downloaded and verified", runtime.model_ready(),
                     "download it from Control → Analysis"):
            return
        rt = runtime.Runtime()
        llm = LlamaServer(ensure=rt.ensure, label=model)
    else:
        print(f"\nA real run on {model}" + (f" + {designer}" if designer else ""))
        llm = Ollama()
        version = llm.version()
        if not check(f"Ollama answers at {llm.url}", version is not None,
                     "start it (`ollama serve`) or set PIT_OLLAMA_URL"):
            return
        for m in dict.fromkeys(x for x in (model, designer) if x):
            if not check(f"{m} is pulled", llm.has_model(m), f"`ollama pull {m}`"):
                return
    peak = [0]

    def sample():
        import subprocess as sp
        while rt is not None and not done.is_set():
            if rt._proc is not None:
                out = sp.run(["ps", "-o", "rss=", "-p", str(rt._proc.pid)],
                             capture_output=True, text=True).stdout.strip()
                if out.isdigit():
                    peak[0] = max(peak[0], int(out) * 1024)
            done.wait(1.0)

    import threading
    done = threading.Event()
    if rt is not None and sys.platform != "win32":
        threading.Thread(target=sample, daemon=True).start()
    t0 = time.monotonic()

    def step(msg: str) -> None:
        print(f"    {time.monotonic() - t0:6.1f}s  {msg}", flush=True)

    try:
        res = pipeline.analyse(LocalToolbox(), SqliteSink(), llm, [uid], analyst=model,
                               designer=designer, question=question, on_step=step)
    finally:
        done.set()
        if rt is not None:
            rt.stop()
    s = res.stats
    if peak[0]:
        print(f"    llama-server peak resident memory: {peak[0] / 1e9:.1f} GB "
              f"(budget: a 16 GB pit machine)")
        check("the engine stays inside the 16 GB machine's budget (under 8 GB)",
              peak[0] < 8e9, f"{peak[0] / 1e9:.1f} GB")
    print(f"    analyst {s.analyst_s:.0f}s, designer {s.designer_s:.0f}s, {s.turns} turns, "
          f"{s.tool_calls} tool calls, {s.prompt_tokens} tokens read, "
          f"{s.output_tokens} written")
    out = ROOT / ".ai_check" / f"run-{res.run_id}.json"
    out.write_text(json.dumps({"status": res.status, "reason": res.reason,
                               "insights": res.insights, "board": res.board,
                               "transcript": res.transcript}, indent=2, ensure_ascii=False))
    print(f"    everything it said: {out.relative_to(ROOT)}")
    check("the model's board survived every check", res.status == "published", res.reason)
    if res.board:
        h = res.board["headline"]
        print(f"    headline [{h['status']}] {h['title']}: {h['sentence']}")
        for v in res.board.get("vitals", []):
            print(f"      [{v['status']:5}] {v['label']}: {v['value']} {v.get('unit', '')}")


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", help="also run the real pipeline on this Ollama model")
    ap.add_argument("--designer", help="a different model for the designer (default: same)")
    ap.add_argument("--engine", choices=("ollama", "llama"), default="ollama",
                    help="llama: the built-in llama-server and the downloaded model "
                         "(--model is then ignored)")
    ap.add_argument("--session", help="session uid (default: the newest)")
    ap.add_argument("--question", default=None)
    opts = ap.parse_args()

    scratch_db()
    import app.db.migrations  # noqa: F401  — registers the schema
    from app.ai import tools
    from app.db import init_db
    db = init_db()
    from app.db.sync import bundle
    scratch_dir = ROOT / ".ai_check" / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    bundle.import_bundle(db.path, FIXTURE, scratch_dir)
    shutil.rmtree(scratch_dir, ignore_errors=True)

    sessions = tools.list_sessions(limit=1)["sessions"]
    uid = opts.session or (sessions[0]["uid"] if sessions else None)
    if not uid:
        print(f"The fixture {FIXTURE.name} didn't import.")
        return 1
    print(f"Session {uid}")

    tools_section(uid)
    schema_section(uid)
    checks_section(uid)
    pipeline_section(uid)
    app_section(uid)
    mcp_section(uid)
    if opts.model or opts.engine == "llama":
        from app.ai.pipeline import DEFAULT_QUESTION
        live_section(uid, opts.model or "", opts.designer, opts.question or DEFAULT_QUESTION,
                     opts.engine)

    print(f"\n{'All checks passed.' if not _failures else f'{len(_failures)} failed.'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
