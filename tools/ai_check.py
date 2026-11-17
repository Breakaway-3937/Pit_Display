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
  is rejected and the rejection logged;
* the playbook (learning from the crew's verdicts): filler dropped by code,
  lessons chosen by the log's data, the curator's checks refuse ungrounded
  lessons, a lesson's record, enforcement of an ignored one, and the context
  budget (an overflow is condensed and retried, not a failed run).

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
        if isinstance(r, Reply):         # as given (a reply cut off at its token cap)
            return r
        if isinstance(r, list):          # tool calls
            calls = [{"name": n, "arguments": a} for n, a in r]
            return Reply(tool_calls=calls, message={"tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in r]})
        return Reply(content=r if isinstance(r, str) else json.dumps(r))


def honest_findings(uid: str) -> dict:
    return {
        "schema": 1, "session_uids": [uid], "question": "check",
        "findings": [
            {"id": "latched_faults",
             "claim": "50 latched faults: work through the faults list before the next "
                      "match.",
             "metric": {"name": "latched faults", "value": 50},
             "severity": "fault", "subsystem": None, "series": None,
             "evidence": [{"tool": "session_overview", "args": {"session_uid": uid},
                           "value": 50},
                          {"tool": "faults", "args": {"session_uid": uid}, "value": 1}]},
            {"id": "battery_sag",
             "claim": "Battery sagged to 11.25 V under load: charge or swap it before the "
                      "next match.",
             "metric": {"name": "battery sag", "value": 11.25, "unit": "V"},
             "severity": "warn", "subsystem": None,
             "series": {"session_uid": uid, "device_type": "TalonFX", "can_id": 1,
                        "signal": "SupplyVoltage", "column": "v_min"},
             "evidence": [{"tool": "session_overview", "args": {"session_uid": uid},
                           "value": 11.25}]},
            {"id": "hottest_motor",
             "claim": "The hottest motor reached 26 °C: feel it after the next match.",
             "metric": {"name": "hottest motor", "value": 26.0, "unit": "°C"},
             "severity": "warn", "subsystem": None, "series": None,
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
             "finding": "latched_faults", "detail": "latched faults first"},
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
                "device_type": "TalonFX", "can_id": 1, "at_s": 30.0}
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


def anomaly_section() -> None:
    """The detector on a made-up robot whose answers are known: last year's
    shooter (a follower never commanded), a missing device, a hot motor, a
    flipped inversion. And silence on a log that matches its history."""
    from app.ai import tools
    from app.db import db
    print("\nWhat isn't normal (the anomaly detector, against the robot's own history)")
    ids = {}

    def sig(c, dtype, name, kind="num"):
        c.execute("INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class) "
                  "VALUES (?, ?, ?, 'telemetry')", (dtype, name, kind))
        return c.execute("SELECT id FROM signal WHERE device_type=? AND name=?",
                         (dtype, name)).fetchone()[0]

    def dev(c, dtype, can, label=None, sub=None):
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES (?, ?)", (dtype, can))
        if label:
            c.execute("UPDATE device SET label=?, subsystem=? WHERE device_type=? AND can_id=?",
                      (label, sub, dtype, can))
        return c.execute("SELECT id FROM device WHERE device_type=? AND can_id=?",
                         (dtype, can)).fetchone()[0]

    def session(n, *, drive17=True, with18=True, temp16=40.0, polarity="CounterClockwise_Positive"):
        uid = f"anom-{n}"
        with db.transaction() as c:
            c.execute("INSERT INTO log_session (source_file, source_name, source_kind, device_serial, "
                      "started_at, duration_s, uid) VALUES (?, ?, 'hoot', 'rio', ?, 120.0, ?)",
                      (uid, f"rio_2026-09-0{n}_10-00-00.hoot", f"2026-09-0{n} 10:00:00", uid))
            sid = c.execute("SELECT id FROM log_session WHERE uid=?", (uid,)).fetchone()[0]
            robot = dev(c, "Robot", -1)
            en = sig(c, "Robot", "RobotEnable")
            c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)", (sid, robot, en))
            se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.executemany("INSERT INTO samples.sample VALUES (?, ?, 0, ?)",
                          [(se, 0, 0), (se, 10000, 1), (se, 110000, 0)])
            for can, label in ((16, "Shooter Lead"), (17, "Shooter Follow"), (18, "Intake Roller")):
                if can == 18 and not with18:
                    continue
                d = dev(c, "TalonFX", can, label, "Shooter" if can != 18 else "Intake")
                driven = can != 17 or drive17
                for name, val in (("DutyCycle", 0.8 if driven else 0.0),
                                  ("DeviceTemp", (temp16 if can == 16 else 38.0) + n * 0.5)):
                    g = sig(c, "TalonFX", name)
                    c.execute("INSERT INTO series (session_id, device_id, signal_id, v_min, v_max) "
                              "VALUES (?, ?, ?, ?, ?)", (sid, d, g, 0.0, val))
                    se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    c.executemany("INSERT INTO samples.sample_1s (series_id, t_s, v_min, v_max, v_avg, n) "
                                  "VALUES (?, ?, ?, ?, ?, 1)",
                                  [(se, t, 0.0, val if 15 <= t <= 100 else 0.0, 0.0) for t in range(0, 120, 5)])
                cm = sig(c, "TalonFX", "ControlMode", "enum")
                codes = {}
                for label2 in ("DutyCycleOut", "NeutralOut"):     # the real fixture may hold others
                    row = c.execute("SELECT code FROM signal_enum WHERE signal_id=? AND label=?",
                                    (cm, label2)).fetchone()
                    if row is None:
                        nxt = c.execute("SELECT COALESCE(MAX(code), 0) + 1 FROM signal_enum "
                                        "WHERE signal_id=?", (cm,)).fetchone()[0]
                        c.execute("INSERT INTO signal_enum VALUES (?, ?, ?)", (cm, nxt, label2))
                        row = (nxt,)
                    codes[label2] = row[0]
                c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)", (sid, d, cm))
                se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                c.execute("INSERT INTO samples.sample VALUES (?, 12000, 0, ?)",
                          (se, codes["DutyCycleOut" if driven else "NeutralOut"]))
                pol = sig(c, "TalonFX", "AppliedRotorPolarity", "enum")
                c.execute("INSERT INTO session_constant (session_id, device_id, signal_id, v, v_text) "
                          "VALUES (?, ?, ?, 0, ?)", (sid, d, pol, polarity if can == 16 else "CounterClockwise_Positive"))
        return uid

    for n in range(1, 5):
        session(n)
    calm = tools.call("anomalies", {"session_uid": session(5)})
    check("a log just like its history: nothing flagged",
          calm.get("anomalies") == [] and calm["history_logs"] == 4, str(calm.get("anomalies"))[:300])
    got = tools.call("anomalies", {"session_uid": session(6, drive17=False, with18=False,
                                                         temp16=70.0, polarity="Clockwise_Positive")})
    kinds = {(a["kind"], a["device"], a.get("signal")) for a in got.get("anomalies") or []}
    check("last year's shooter: the follower on the bus but never driven is a fault",
          ("not_driven", "Shooter Follow", None) in kinds, str(kinds))
    nd = next((a for a in got["anomalies"] if a["kind"] == "not_driven"), {})
    check("…flagged against its history and its partner",
          "earlier enabled logs" in nd.get("detail", "") and "Shooter Lead" in nd.get("detail", ""),
          nd.get("detail", ""))
    check("a device missing from the bus is a fault", ("missing", "Intake Roller", None) in kinds)
    check("a motor far hotter than it usually runs is flagged, with its normal range",
          ("out_of_range", "Shooter Lead", "DeviceTemp") in kinds
          and any(a.get("normal", {}).get("logs") == 5 for a in got["anomalies"]
                  if a["kind"] == "out_of_range"))
    check("a flipped inversion is a fault",
          any(a["kind"] == "config_changed" and a["signal"] == "AppliedRotorPolarity"
              and a["severity"] == "fault" for a in got["anomalies"]))
    check("nothing else is flagged", len(kinds) == 4, str(sorted(kinds)))
    # The detector finds, the model explains: a fault it flags must be covered.
    from app.ai import checks
    led = checks.Ledger()
    led.add("anomalies", got)
    silent = {"findings": [{"id": "brownout", "claim": "BridgeBrownout on 10 devices.",
                            "metric": {"name": "BridgeBrownout", "value": 10}}]}
    covered = {"findings": silent["findings"] + [
        {"id": "follower", "claim": "Shooter Follow was never driven while enabled.",
         "metric": {"name": "Shooter Follow", "value": 100}}]}
    gaps = checks.anomaly_coverage(silent, led)
    check("findings that leave out a detector fault are sent back, naming it",
          any("Shooter Follow" in g for g in gaps), "; ".join(gaps)[:200])
    still = [g for g in checks.anomaly_coverage(covered, led) if "Shooter Follow" in g]
    check("…and a finding naming that device covers it", not still, "; ".join(still))
    from app.ai import pipeline as _pl, schema as _schema
    added = _pl._detector_findings(led, silent)
    fixed = {"schema": 1, "session_uids": [got["args"]["session_uid"]], "findings": added}
    check("…and if the model never covers it, the detector's own finding is added "
          "(valid against the contract, gap closed)",
          any(f["id"].startswith("detector_") and "Shooter Follow" in f["claim"] for f in added)
          and not _schema.validate(fixed, _schema.load("insight"))
          and not [g for g in checks.anomaly_coverage(fixed, led) if "Shooter Follow" in g])
    from app.robot import delete_session
    for r in db.fetchall("SELECT id FROM log_session WHERE uid LIKE 'anom-%'"):
        delete_session(r["id"], db.path)


def health_section() -> None:
    """The robot's own health beyond faults (Brayden, 2026-11-03): two motors on
    one shaft where one stops working, and the code's loop overrunning."""
    import math
    from app.ai import checks, playbook, tools
    from app.ai import pipeline as _pl, schema as _schema
    from app.db import db
    from app.robot import delete_session, partners
    print("\nPartners and the loop (what the robot never reports as a fault)")

    def sig(c, dtype, name, kind="num"):
        c.execute("INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class) "
                  "VALUES (?, ?, ?, 'telemetry')", (dtype, name, kind))
        return c.execute("SELECT id FROM signal WHERE device_type=? AND name=?",
                         (dtype, name)).fetchone()[0]

    def dev(c, dtype, can):
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES (?, ?)", (dtype, can))
        return c.execute("SELECT id FROM device WHERE device_type=? AND can_id=?",
                         (dtype, can)).fetchone()[0]

    def per_second(c, sid, d, g, values, v_min=None, v_max=None):
        c.execute("INSERT INTO series (session_id, device_id, signal_id, v_min, v_max) "
                  "VALUES (?, ?, ?, ?, ?)", (sid, d, g, v_min, v_max))
        se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.executemany("INSERT INTO samples.sample_1s (series_id, t_s, v_min, v_max, v_avg, n) "
                      "VALUES (?, ?, ?, ?, ?, 1)", [(se, t, v, v, v) for t, v in values])
        return se

    def shaft(n, amps_b=20.0, rise_b=12.0, gear=1.0):
        """Two motors on one shaft (TalonFX 30 and 31), 60 s of a changing speed."""
        uid = f"twin-{n}"
        with db.transaction() as c:
            c.execute("INSERT INTO log_session (source_file, source_name, source_kind, "
                      "device_serial, started_at, duration_s, uid) "
                      "VALUES (?, ?, 'hoot', 'rig', ?, 80.0, ?)",
                      (uid, f"rig_2026-11-0{n}.hoot", f"2026-11-0{n} 10:00:00", uid))
            sid = c.execute("SELECT id FROM log_session WHERE uid=?", (uid,)).fetchone()[0]
            ts = range(10, 70)
            speed = {t: 40 + 30 * math.sin(t / 6) for t in ts}
            for can, amps, rise, g in ((30, 20.0, 12.0, 1.0), (31, amps_b, rise_b, gear)):
                d = dev(c, "TalonFX", can)
                per_second(c, sid, d, sig(c, "TalonFX", "RotorVelocity"),
                           [(t, speed[t] * g) for t in ts])
                per_second(c, sid, d, sig(c, "TalonFX", "StatorCurrent"),
                           [(t, amps * (0.8 + 0.4 * (t % 3) / 2)) for t in ts])
                per_second(c, sid, d, sig(c, "TalonFX", "DeviceTemp"),
                           [(t, 30 + rise * t / 70) for t in ts], 30.0, 30.0 + rise)
        return uid

    def flags(uid, kinds=("partner_idle", "partner_mismatch")):
        got = tools.call("anomalies", {"session_uid": uid})
        return [a for a in got.get("anomalies") or [] if a["kind"] in kinds], got

    partners._CACHE.clear()
    for n in range(1, 5):
        shaft(n)
    calm, _ = flags(shaft(5))
    check("two healthy motors on one shaft: nothing flagged", not calm, str(calm)[:200])
    pairs = partners.pairs(db, db.fetchone("SELECT id FROM log_session WHERE uid='twin-5'")["id"])
    check("…and they're found as twins from their speeds alone (no labels)",
          (("TalonFX", 30), ("TalonFX", 31)) in pairs, str(pairs)[:160])
    dead, got_dead = flags(shaft(6, amps_b=0.4, rise_b=1.0))
    d = dead[0] if dead else {}
    check("last year's shooter: one motor turned by its partner, drawing nothing, is a fault",
          d.get("kind") == "partner_idle" and d.get("severity") == "fault"
          and d.get("device") == "TalonFX 31" and d.get("partner") == "TalonFX 30",
          str(dead)[:200])
    check("…and it says the temperatures don't match",
          "warmed 1 °C against its partner's 12 °C" in d.get("detail", ""), d.get("detail", ""))
    weak, _ = flags(shaft(7, amps_b=8.0))
    check("a pair out of its usual ratio (a belt slipping, a motor failing) is a warning",
          [a["kind"] for a in weak] == ["partner_mismatch"]
          and weak[0]["severity"] == "warn" and weak[0]["normal"]["logs"] >= 3, str(weak)[:200])
    for r in db.fetchall("SELECT id FROM log_session WHERE uid LIKE 'twin-%'"):
        delete_session(r["id"], db.path)
    partners._CACHE.clear()
    for n in range(1, 5):
        shaft(n, amps_b=10.0, gear=0.5)
    geared, _ = flags(shaft(5, amps_b=10.0, gear=0.5))
    check("a geared pair in its steady ratio (half the speed, half the current) isn't flagged",
          not geared, str(geared)[:200])
    for r in db.fetchall("SELECT id FROM log_session WHERE uid LIKE 'twin-%'"):
        delete_session(r["id"], db.path)

    # ── the loop ─────────────────────────────────────────────────────────
    def akit(n, stalls=0, overruns=0, console=(), overrun_ms=45.0):
        """An AdvantageKit log enabled 20-120 s, 50 Hz loop summarised per second."""
        uid = f"loop-{n}"
        with db.transaction() as c:
            c.execute("INSERT INTO log_session (source_file, source_name, source_kind, "
                      "started_at, duration_s, uid) VALUES (?, ?, 'wpilog', ?, 130.0, ?)",
                      (uid, f"akit_2026-11-0{n}.wpilog", f"2026-11-0{n} 12:00:00", uid))
            sid = c.execute("SELECT id FROM log_session WHERE uid=?", (uid,)).fetchone()[0]
            robot = dev(c, "Robot", -1)
            en = sig(c, "Robot", "DriverStation/Enabled")
            c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)",
                      (sid, robot, en))
            se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.executemany("INSERT INTO samples.sample VALUES (?, ?, 0, ?)",
                          [(se, 0, 0), (se, 20000, 1), (se, 120000, 0)])
            cyc = [(t, 150.0 if 40 <= t < 40 + stalls else overrun_ms if 60 <= t < 60 + overruns
                    else 19.0) for t in range(0, 130)]
            per_second(c, sid, robot, sig(c, "Robot", "RealOutputs/LoggedRobot/FullCycleMS"), cyc)
            per_second(c, sid, robot, sig(c, "Robot", "RealOutputs/Logger/QueuedCycles"),
                       [(t, 40.0 if stalls else 2.0) for t in range(0, 130)])
            con = sig(c, "Robot", "RealOutputs/Console", "enum")
            c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)",
                      (sid, robot, con))
            se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            for i, text in enumerate(console):
                row = c.execute("SELECT code FROM signal_enum WHERE signal_id=? AND label=?",
                                (con, text)).fetchone()
                if row is None:
                    code = c.execute("SELECT COALESCE(MAX(code), 0) + 1 FROM signal_enum "
                                     "WHERE signal_id=?", (con,)).fetchone()[0]
                    c.execute("INSERT INTO signal_enum VALUES (?, ?, ?)", (con, code, text))
                    row = (code,)
                c.execute("INSERT INTO samples.sample VALUES (?, ?, 0, ?)",
                          (se, 41000 + i * 10, row[0]))
        return uid

    tracer = ("Warning at edu.wpi.first.wpilibj.IterativeRobotBase.printLoopOverrunMessage"
              "(IterativeRobotBase.java:436): Loop time of 0.02s overrun\n"
              "\trobotPeriodic(): 0.031000s\n\tShooter.periodic(): 0.014000s\n"
              "\tSmartDashboard.updateValues(): 0.009000s\n\tSwerve.periodic(): 0.001000s")
    loops = lambda uid: flags(uid, ("loop_overrun",))[0]  # noqa: E731
    check("a loop that keeps its 20 ms period: nothing flagged", not loops(akit(1)))
    for n in (2, 3, 4):
        akit(n, stalls=1)
    bad = loops(akit(5, stalls=6, console=[tracer] * 5))
    b = bad[0] if bad else {}
    check("the 'logging too much' watchdog: stalls past 100 ms while enabled are a fault",
          b.get("severity") == "fault" and b.get("stall_s") == 6 and b.get("must_cover"),
          str(bad)[:200])
    check("…naming the slow parts the console's Tracer listed, not its wrappers",
          [p["part"] for p in b.get("slow_parts", [])] == ["Shooter.periodic",
                                                           "SmartDashboard.updateValues"]
          and "robotPeriodic" not in b.get("detail", ""), str(b.get("slow_parts")))
    check("…with the logger's backlog and this robot's usual figure",
          b.get("queued_max") == 40 and (b.get("normal") or {}).get("median") == 1,
          str(b.get("normal")))
    one = loops(akit(6, stalls=1))
    check("one stall in a match is a warning", [a["severity"] for a in one] == ["warn"])
    short = loops(akit(7, overruns=40))
    check("overruns past 40 ms (no stall) in a third of the match are a warning",
          [a["severity"] for a in short] == ["warn"], str(short)[:160])
    check("…but brief 26 ms ones aren't (noise on a real log)",
          not loops(akit(8, overruns=40, overrun_ms=26.0)))

    # ── the board: each must be a finding, and no lesson may drop it ─────
    led = checks.Ledger()
    led.add("anomalies", {"args": {"session_uid": "loop-5"}, "anomalies": bad})
    silent = {"findings": [{"id": "all_clear", "claim": "Nothing latched.",
                            "metric": {"name": "faults", "value": 0}, "severity": "ok"}]}
    gaps = checks.anomaly_coverage(silent, led)
    check("findings that leave out the loop overrun are sent back", any("Robot loop" in g for g in gaps),
          "; ".join(gaps)[:200])
    added = _pl._detector_findings(led, silent)
    fixed = {"schema": 1, "session_uids": ["loop-5"], "findings": added}
    check("…and if the model never covers it, the detector's own finding goes on the board",
          len(added) == 1 and added[0]["severity"] == "fault" and added[0]["metric"]["unit"] == "s"
          and not _schema.validate(fixed, _schema.load("insight"))
          and not checks.anomaly_coverage(fixed, led), str(added)[:200])
    lesson = {"id": "learned_avoid_loop_time", "kind": "avoid", "rule": "skip loop readings",
              "topic": ["looptime", "loop"], "enforce": True}
    mine = {"findings": [{"id": "loop_overruns", "severity": "warn",
                          "claim": "The robot loop stalled past 100 ms in 6 seconds.",
                          "metric": {"name": "loop stalls", "value": 6}},
                         {"id": "other", "severity": "warn", "claim": "Something else.",
                          "metric": {"name": "x", "value": 1}}]}
    playbook.enforce(mine, [lesson], protect=lambda f: _pl._carries_flag(f, led))
    check("…and a learned lesson can never drop a finding that carries a detector flag",
          "loop_overruns" in [f["id"] for f in mine["findings"]])
    for r in db.fetchall("SELECT id FROM log_session WHERE uid LIKE 'loop-%'"):
        delete_session(r["id"], db.path)


def sweep_section() -> None:
    """The general sweep (app/robot/sweep.py): any health signal beyond its own
    past is flagged; commands, flags and first use aren't."""
    from app.db import db
    from app.robot import delete_session, sweep
    print("\nThe sweep (every health signal against its own past)")
    ids = []
    with db.transaction() as c:
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES ('Robot', -1)")
        robot = c.execute("SELECT id FROM device WHERE device_type='Robot'").fetchone()[0]

        def sig(name):
            c.execute("INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class)"
                      " VALUES ('Robot', ?, 'num', 'telemetry')", (name,))
            return c.execute("SELECT id FROM signal WHERE device_type='Robot' AND name=?",
                             (name,)).fetchone()[0]

        for n in range(10):
            uid = f"sweep-{n}"
            c.execute("INSERT INTO log_session (source_file, source_name, source_kind, started_at,"
                      " duration_s, uid) VALUES (?, ?, 'wpilog', ?, 60, ?)",
                      (uid, uid, f"2026-11-0{n % 9 + 1} 10:00:0{n}", uid))
            sid = c.execute("SELECT id FROM log_session WHERE uid=?", (uid,)).fetchone()[0]
            ids.append(sid)
            last = n == 9
            for name, lo, hi in (
                    ("RealOutputs/Logger/ConduitSaveMS", 10.0, 160.0 if last else 20 + n * 2),
                    ("RealOutputs/Turret Stator Motor Current", 0.0, 25.0 if last else 0.0),
                    ("DriverStation/FMSAttached", 0.0, 1.0 if last else 0.0),
                    ("RealOutputs/Arm Setpoint", 0.0, 90.0 if last else 10.0 + n)):
                c.execute("INSERT INTO series (session_id, device_id, signal_id, v_min, v_max) "
                          "VALUES (?, ?, ?, ?, ?)", (sid, robot, sig(name), lo, hi))
    got = sweep.find(db, ids[-1], ids[:-1])
    names = [a["signal"] for a in got]
    check("a health signal far beyond its own past is flagged (the logger stalling)",
          names == ["RealOutputs/Logger/ConduitSaveMS"] and got[0]["normal"]["logs"] == 9,
          str(names))
    check("…but not a mechanism's first use, a field flag, or a setpoint", len(names) == 1)
    check("…and nothing until there are enough comparable logs",
          not sweep.find(db, ids[-1], ids[:5]))
    for sid in ids:
        delete_session(sid, db.path)


def phases_section() -> None:
    """match_phases on a made-up match: disabled, 15 s auto, teleop, disabled."""
    from app.ai import tools
    from app.db import db
    print("\nMatch phases (when the robot was enabled, and in what)")
    with db.transaction() as c:
        c.execute("INSERT INTO log_session (source_file, source_name, source_kind, duration_s, uid) "
                  "VALUES ('phase-test', 'phase-test.wpilog', 'wpilog', 180.0, 'phase-test')")
        sid = c.execute("SELECT id FROM log_session WHERE uid = 'phase-test'").fetchone()[0]
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES ('Robot', -1)")
        did = c.execute("SELECT id FROM device WHERE device_type='Robot' AND can_id=-1").fetchone()[0]
        for name, points in (("DriverStation/Enabled", [(0, 0), (5000, 1), (150000, 0)]),
                             ("DriverStation/Autonomous", [(0, 0), (5000, 1), (20000, 0)])):
            c.execute("INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class) "
                      "VALUES ('Robot', ?, 'num', 'telemetry')", (name,))
            gid = c.execute("SELECT id FROM signal WHERE device_type='Robot' AND name=?",
                            (name,)).fetchone()[0]
            c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)",
                      (sid, did, gid))
            se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.executemany("INSERT INTO samples.sample (series_id, t_ms, ord, v) VALUES (?, ?, 0, ?)",
                          [(se, t, v) for t, v in points])
    out = tools.call("match_phases", {"session_uid": "phase-test"})
    got = [(p["phase"], p["start_s"], p["end_s"]) for p in out.get("phases") or []]
    check("disabled → 15 s auto → teleop → disabled, from the robot's own DS state",
          got == [("disabled", 0.0, 5.0), ("auto", 5.0, 20.0), ("teleop", 20.0, 150.0),
                  ("disabled", 150.0, 180.0)] and out["auto_s"] == 15.0
          and out["teleop_s"] == 130.0 and out["never_enabled"] is False, str(got))
    from app.robot import delete_session
    delete_session(sid, db.path)        # samples too (they live in the attached file)


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
    fresult = ledger.matching("faults", {"session_uid": uid})[0].result
    pig = next(d for d in fresult["by_device"] if d["device_type"] == "Pigeon2")
    # A real count of something in the result, but not the Pigeon's own.
    other = next(g["n_devices"] for g in fresult["summary"] if g["n_devices"] != pig["n_faults"])
    rejects(f"a count that belongs to a different fault is rejected (trial 3's Pigeon: {other})",
            lambda f: f["findings"].append({
                "id": "pigeon", "claim": f"Check {pig['device']} warnings.",
                "metric": {"name": pig["device"], "value": other, "unit": "warnings"},
                "severity": "warn", "subsystem": None, "series": None,
                "evidence": [{"tool": "faults", "args": {"session_uid": uid}, "value": other}]}),
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
    warned = next(g for g in fresult["summary"] if g["status"] == "warn")
    rejects(f"'fault' for a fault the pit board ranks warn is rejected (trial 4: {warned['fault']})",
            lambda f: f["findings"].append({
                "id": "warned", "claim": f"{warned['fault']} latched on {warned['n_devices']} devices.",
                "metric": {"name": warned["fault"], "value": warned["n_devices"]},
                "severity": "fault", "subsystem": None, "series": None,
                "evidence": [{"tool": "faults", "args": {"session_uid": uid},
                              "value": warned["n_devices"]}]}),
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
          not probs and [v["status"] for v in board["vitals"]] == ["fault", "warn", "warn"]
          and board["headline"]["status"] == "fault", "; ".join(probs))
    dup = copy.deepcopy(honest_board(uid))
    dup["faults"] = [{"label": "Latched faults", "value": "50", "finding": "latched_faults"}]
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

    # One finding keeps inventing a number: after two retries it's dropped
    # and never shown; the sound findings still publish.
    liar1 = honest_findings(uid)
    liar1["findings"][2]["metric"]["value"] = 31.5
    llm = Scripted([
        [("faults", {"session_uid": uid}),
         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
        "Done.", liar1, liar1, liar1, _board_without(honest_board(uid),
                                                      liar1["findings"][2]["id"])])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    shown = [f.get("metric", {}).get("value") for f in (res.insights or {}).get("findings") or []]
    check("one finding that keeps inventing a number is dropped, the sound ones publish",
          res.status == "published" and 31.5 not in shown and res.stats.dropped == [3],
          f"{res.status} {res.reason[:120]} dropped {res.stats.dropped}")

    # Every finding invents one: nothing sound is left, so the run is rejected.
    liar = honest_findings(uid)
    for f in liar["findings"]:
        f["metric"]["value"] = 31.5
    llm = Scripted([
        [("faults", {"session_uid": uid}),
         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
        "Done.", liar, liar, liar])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    run = db.fetchone("SELECT status, reject_reason FROM analysis_run WHERE id = ?",
                      (res.run_id,))
    check("a model whose every finding invents a number is rejected, after two retries",
          res.status == "rejected" and llm.calls == 5, f"{res.status} after {llm.calls} turns")
    check("…and the rejection is logged with its reason",
          run is not None and run["status"] == "rejected" and "31.5" in run["reject_reason"]
          and "never made" not in run["reject_reason"], run["reject_reason"] if run else "")
    check("…and nothing was published",
          db.fetchone("SELECT COUNT(*) n FROM analysis_board WHERE uid LIKE ?",
                      (f"%-{res.run_id}",))["n"] == 0)

    # A chart that fails a check is dropped, not the board (2026-10-05: most
    # real runs' boards were rejected over a chart alone).
    import copy as _copy
    bad_board = _copy.deepcopy(honest_board(uid))
    bad_board.setdefault("charts", []).append({
        "kind": "line", "finding": honest_findings(uid)["findings"][0]["id"],
        "title": "A chart the checks refuse", "why": "test",
        "source": {"session_uid": uid, "device_type": None, "can_id": None,
                   "signal": "DeviceTemp"}})
    llm = Scripted([[("faults", {"session_uid": uid}),
                     ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
                    "Done.", honest_findings(uid), bad_board])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    titles = [c.get("title") for c in (res.board or {}).get("charts") or []]
    check("a board with one bad chart publishes without it (charts are optional)",
          res.status == "published" and "A chart the checks refuse" not in titles,
          f"{res.status} {res.reason[:120]}")


def playbook_section(uid: str) -> None:
    """The learning loop (app/ai/playbook.py, curate.py) and the context budget."""
    import copy
    from app.ai import curate, feedback, pipeline, playbook, service
    from app.ai.local import LocalToolbox, SqliteSink
    from app.ai.ollama import OllamaError
    from app.db import db

    print("\nThe playbook: learning from the crew's verdicts")
    playbook.path().unlink(missing_ok=True)

    # ── filler: "nothing to see" findings beside a real one ──────────────
    ins = honest_findings(uid)
    calm = copy.deepcopy(ins["findings"][1])
    calm.update(id="battery_within_limits", severity="ok",
                claim="Battery stayed within normal limits.")
    ins["findings"].append(calm)
    dropped = playbook.drop_filler(ins)
    check("an ok finding beside a fault is dropped by code (94 of 94 such were rated "
          "not useful)", dropped == ["battery_within_limits"]
          and len(ins["findings"]) == 3, str(dropped))
    clear = {"findings": [dict(calm, id="all_clear"), dict(calm, id="second_clear")]}
    playbook.drop_filler(clear)
    check("…a lone all-clear stays, a second one goes",
          [f["id"] for f in clear["findings"]] == ["all_clear"])
    one = {"findings": [dict(calm, id="only")]}
    check("…and it never leaves a run with no findings",
          not playbook.drop_filler(one) and len(one["findings"]) == 1)

    # ── select: only what this log's data touches ────────────────────────
    lessons = playbook.active()
    picked = {x["id"] for x in playbook.select(lessons, '{"fault":"BootDuringEnable"}')}
    check("the built-in lessons load", len(lessons) == len(playbook.SEEDS))
    check("a lesson is given when its topic is in the log's data, not otherwise",
          "seed_reboot_while_enabled" in picked and "seed_unlicensed_feature" not in picked,
          str(sorted(picked)))
    many = [dict(lessons[0], id=f"x{i}", topic=["bootduringenable"], seed=False,
                 rule="r" * 200) for i in range(20)]
    sel = playbook.select(many, "BootDuringEnable")
    check("…never more than a handful, inside its character budget",
          len(sel) <= playbook.SELECT_LIMIT
          and sum(len(x["rule"]) for x in sel) <= playbook.SELECT_CHARS)

    # ── enforce: drops only, never a fault, a detector's or the last ─────
    rule = {"id": "learned_no_temp", "kind": "avoid", "rule": "skip temperatures",
            "topic": ["hottest"], "enforce": True}
    ins = honest_findings(uid)
    gone = playbook.enforce(ins, [rule])
    check("an enforced avoid lesson drops the finding it's about",
          gone == [{"finding": "hottest_motor", "lesson": "learned_no_temp"}]
          and "hottest_motor" not in [f["id"] for f in ins["findings"]])
    rule_f = dict(rule, topic=["latched"])
    ins = honest_findings(uid)
    det = copy.deepcopy(ins["findings"][2])
    det.update(id="detector_hottest", severity="warn")
    ins["findings"].append(det)
    playbook.enforce(ins, [rule_f, dict(rule, topic=["detectorhottest"])])
    check("…never a fault, never a detector finding",
          {"latched_faults", "detector_hottest"} <= {f["id"] for f in ins["findings"]})
    solo = {"findings": [honest_findings(uid)["findings"][2]]}
    check("…never the last finding", not playbook.enforce(solo, [rule]))
    check("…and a lesson not enforced drops nothing",
          not playbook.enforce(honest_findings(uid), [dict(rule, enforce=False)]))

    # ── a run is given its lessons and records them ──────────────────────
    sink = SqliteSink(machine_id="pit-aicheck")
    noisy = honest_findings(uid)
    noisy["findings"].append(calm)
    llm = Scripted([[("faults", {"session_uid": uid}),
                         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})], "Done.", noisy, honest_board(uid)])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted",
                           playbook_lessons=playbook.active())
    check("a run records the lessons it was given and the filler code dropped",
          res.status == "published" and res.stats.filler == ["battery_within_limits"]
          and res.stats.playbook, f"{res.status} {res.reason[:100]} {res.stats.playbook}")
    first = next(m for m in res.transcript["analyst"] if m["role"] == "user")["content"]
    check("…and the lessons are in what the analyst read",
          "This crew's playbook" in first)
    check("asking again for a result it was given up front gets a line, not the result",
          res.stats.repeats == 1 and '"already given' not in first, str(res.stats.repeats))

    # ── the context budget ───────────────────────────────────────────────
    big = {"args": {"session_uid": uid},
           "anomalies": [{"device": f"TalonFX {i}", "detail": "x" * 200} for i in range(200)]}
    lines = pipeline._fit_primer([("anomalies", big)], 6000)
    check("an oversized up-front result is shrunk to fit, and says so",
          len(lines[0]) <= 6400 and '"truncated":true' in lines[0] and "_shown_of" in lines[0])
    msgs = [{"role": "system", "content": "s" * 2000}, {"role": "user", "content": "u" * 2000}]
    msgs += [{"role": "tool", "content": json.dumps(big)} for _ in range(4)]
    n = pipeline._fit(msgs, 4000)
    check("a long conversation is condensed under the budget, oldest results first",
          pipeline._size(msgs) <= 4000 and n >= 1
          and json.loads(msgs[2]["content"]).get("condensed"), f"{pipeline._size(msgs)} {n}")

    class Overflows(Scripted):
        def __init__(self, replies):
            super().__init__(replies)
            self.raised = False

        def chat(self, model, messages, **kw):
            if not self.raised:
                self.raised = True
                raise OllamaError("llama-server: HTTP 400 request (17256 tokens) exceeds "
                                  "the available context size (16384 tokens)")
            return super().chat(model, messages, **kw)

    llm = Overflows([[("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
                     "Done.", honest_findings(uid), honest_board(uid)])
    res = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted")
    check("an engine that says the request was too long gets a condensed retry, not a "
          "failed run", res.status == "published" and len(res.stats.recovered) == 1,
          f"{res.status} {res.reason[:100]}")

    # ── the curator: verdicts → lessons, refused unless grounded ─────────
    run = db.fetchone("SELECT id FROM analysis_run WHERE status = 'published' "
                      "ORDER BY id DESC LIMIT 1")["id"]
    fids = [f["id"] for f in noisy["findings"]]
    feedback.set_rating(run, "hottest_motor", "not_useful")
    feedback.set_note(run, "hottest_motor", "Normal reading")
    feedback.set_rating(run, "latched_faults", "useful")
    verdicts = curate.pending()
    refs = {v["finding"]["id"]: v["ref"] for v in reversed(verdicts)}
    mine = next((i for i, v in enumerate(verdicts) if v["reason"] == "Normal reading"), None)
    check("new verdicts are pending, reasoned ones before unreasoned useful ones",
          mine is not None and all(v["rating"] != "useful" or v["reason"]
                                   for v in verdicts[:mine]), str(fids))
    doc = playbook.load()
    good = {"op": "add", "id": "cool_motors", "kind": "avoid",
            "rule": "A motor temperature well inside its range isn't worth a finding.",
            "topic": ["hottest"], "from": [refs["hottest_motor"]]}

    def refused(label: str, change: dict, needle: str) -> None:
        ok, probs = curate.check_changes([change], verdicts, doc)
        check(label, not ok and any(needle in p for p in probs), "; ".join(probs))

    ok, probs = curate.check_changes([good], verdicts, doc)
    check("a grounded lesson from a reasoned verdict passes", len(ok) == 1 and not probs,
          "; ".join(probs))
    refused("a lesson citing a verdict that isn't there is refused",
            dict(good, **{"from": ["v99"]}), "no verdict")
    refused("an avoid lesson standing on a useful verdict is refused",
            dict(good, **{"from": [refs["latched_faults"]]}), "can't stand on")
    refused("a topic word that matches everything is refused",
            dict(good, topic=["motor"]), "almost")
    refused("a topic not in the cited verdicts is refused",
            dict(good, topic=["shooter"]), "isn't in the verdicts")
    refused("a figure the crew never wrote is refused",
            dict(good, rule="A motor under 60 °C is not worth a finding at all."), "60")
    refused("an avoid lesson that would cover a finding the crew called useful is refused",
            dict(good, topic=["match"]), "rated")
    refused("a built-in lesson can't be changed by a model",
            dict(good, op="update", id="seed_reboot_while_enabled"), "built-in")
    ok, _p = curate.check_changes([dict(good, id="seed_cool_motors")], verdicts, doc)
    check("…but a new lesson the model names like a seed is added, named by code",
          [c["id"] for c in ok] == ["learned_cool_motors"], str(ok))

    llm = Scripted([{"changes": [dict(good, topic=["shooter"])]}, {"changes": [good]}])
    out = curate.curate(llm, "scripted")
    saved = playbook.load()
    check("a curation pass sends a refused change back once, then saves the fixed lesson",
          out.status == "changed" and llm.calls == 2
          and any(x["id"] == "learned_cool_motors" for x in saved["lessons"]),
          f"{out.status} {out.refused} {out.error}")
    check("…with where it came from, the history and the watermark moved",
          saved["lessons"][0]["learned_from"] and saved["history"]
          and saved["curated_at"] and not curate.pending())
    check("…so the next pass has nothing to do",
          curate.curate(Scripted([]), "scripted").status == "nothing")
    ok, probs = curate.check_changes([dict(good, id="again")], verdicts, saved)
    check("a lesson that restates one already there is refused",
          not ok and any("already covers" in p for p in probs), "; ".join(probs))
    check("the playbook syncs as a team setting",
          "playbook" in __import__("app.db.sync.docs", fromlist=["x"]).SETTING_DOCS)

    # ── measure, then enforce what the model ignores ─────────────────────
    lesson = next(x for x in saved["lessons"] if x["id"] == "learned_cool_motors")
    saved["lessons"][0]["learned_from"] = ["a", "b", "c"]
    playbook.save(**saved)
    for _ in range(3):
        llm = Scripted([[("faults", {"session_uid": uid}),
                         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})], "Done.",
                        honest_findings(uid), honest_board(uid)])
        r = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted",
                             playbook_lessons=playbook.active())
        feedback.set_rating(r.run_id, "hottest_motor", "not_useful")
    rec = playbook.evaluate(lessons=playbook.active())[lesson["id"]]
    check("a lesson's record counts the verdicts on its topic after it was given",
          rec.given >= 3 and rec.after["not_useful"] >= 3 and rec.ignored, str(rec))
    on = playbook.auto_enforce()
    check("an avoid lesson the model keeps ignoring is enforced by code, and recorded",
          on == [lesson["id"]] and "enforced by code" in playbook.load()["history"][-1]["what"],
          str(on))
    llm = Scripted([[("faults", {"session_uid": uid}),
                         ("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})], "Done.",
                    honest_findings(uid), _board_without(honest_board(uid), "hottest_motor")])
    r = pipeline.analyse(LocalToolbox(), sink, llm, [uid], analyst="scripted",
                         playbook_lessons=playbook.active())
    check("…and the next run leaves that finding out, the rest publish",
          r.status == "published" and r.stats.enforced == [
              {"finding": "hottest_motor", "lesson": lesson["id"]}], f"{r.status} {r.reason[:90]}")
    playbook.set_flag("seed_unlicensed_feature", retired=True)
    check("an admin can retire a built-in lesson",
          "seed_unlicensed_feature" not in {x["id"] for x in playbook.active()})

    # ── a run a crash left "running" ─────────────────────────────────────
    with db.transaction() as conn:
        conn.execute("INSERT INTO analysis_run (session_uids, analyst, status, started_at) "
                     "VALUES ('[]', 'old', 'running', datetime('now', '-5 hours'))")
        conn.execute("INSERT INTO analysis_run (session_uids, analyst, status) "
                     "VALUES ('[]', 'new', 'running')")
    service._close_stale_runs()
    rows = {r["analyst"]: r["status"] for r in db.fetchall(
        "SELECT analyst, status FROM analysis_run WHERE analyst IN ('old', 'new')")}
    check("a run a quit left 'running' is marked failed at startup; a fresh one isn't",
          rows == {"old": "failed", "new": "running"}, str(rows))
    with db.transaction() as conn:
        conn.execute("DELETE FROM analysis_run WHERE analyst IN ('old', 'new')")

    # ── the panel ────────────────────────────────────────────────────────
    from app.widgets.analysis_panel import AnalysisPanel
    from app.widgets.brand_widgets import SelectableChip
    app = _QAPP
    newest = db.fetchone("SELECT id FROM analysis_run WHERE status = 'published' "
                         "ORDER BY id DESC LIMIT 1")["id"]
    feedback.set_rating(newest, "battery_sag", "not_useful")
    feedback.set_note(newest, "battery_sag", "Normal reading")
    panel = AnalysisPanel()
    panel._selected = newest
    panel._reload()
    app.processEvents()
    why = [c for c in panel.findChildren(SelectableChip) if c.property("reason")]
    check("a Not useful verdict offers one-tap reasons, the chosen one marked",
          any(c.property("reason") == "Normal reading" and c._active for c in why),
          f"{len(why)} reason chips")
    rows = panel._playbook._rows.count()
    check("the playbook shows every lesson in force", rows == len(playbook.active()),
          f"{rows} rows")
    panel.deleteLater()
    app.processEvents()


def csa_section(uid: str) -> None:
    """The stationary CSA (Brayden, 2026-11-03): the Driver Station's log in,
    every log of a recording on one clock, incidents, the knowledge base, the
    model's diagnosis and the checks that keep it honest."""
    import tempfile
    from datetime import datetime
    from app.ai import checks, csa, diagnose, feedback, pipeline as _pl, tools
    from app.ai.local import LocalToolbox, SqliteSink
    from app.db import db
    from app.robot import delete_session, dslog, events, import_log, incidents
    print("\nThe CSA: Driver Station logs, incidents, diagnosis")

    # ── a Driver Station log, through the real importer ──────────────────
    from datetime import UTC
    # UTC: the roboRIO and AdvantageKit name their logs in UTC, and so must the DS's.
    start = datetime(2026, 11, 5, 9, 58, 0, tzinfo=UTC).timestamp()
    recs = []
    for i in range(int(200 / 0.02)):                     # 200 s at 50 Hz
        t = i * 0.02
        en = 125 <= t < 185
        dip = 150 <= t < 150.4
        gone = 160 <= t < 162
        recs.append(dslog.Record(
            trip_ms=4.0, packet_loss=0.0, battery_v=None if gone else (6.2 if dip else 12.4),
            cpu=0.4, can=0.6, wifi_db=40.0, wifi_mb=1.5, brownout=dip, watchdog=False,
            ds_teleop=en and t >= 140, ds_disabled=not en, robot_teleop=en and t >= 140,
            robot_auto=en and t < 140, robot_disabled=not en, pd_type=dslog.PD_REV,
            currents=[2.0] * 24))
    tmp = Path(tempfile.mkdtemp(prefix="dslog-check-"))
    path = tmp / "2026_11_05 09_58_00 Thu.dslog"
    dslog.write(path, start, recs, [
        (1.0, "<TagVersion>1 <time> 01.000 <Code> 44004 <details> FRC: The Driver Station "
              "has lost communication with the robot."),
        (110.0, "<TagVersion>1 <message> FMS Connected:   Qualification - 14:1, Field Time: "
                "26/11/5 9:59:50"),
        (115.0, "<TagVersion>1 <Code> 44007 <details> FRC: Time since robot boot. "
                "<location> 1803.2"),
        (162.5, "<TagVersion>1 <Code> 44007 <details> FRC: Time since robot boot. "
                "<location> 3.682")])
    back = list(dslog.records(path.read_bytes()))
    check("a .dslog reads back exactly as written (trip time, battery, flags, PDH currents)",
          len(back) == len(recs) and back[7510].brownout
          and abs(back[7510].battery_v - 6.2) < 1 / 256
          and back[8050].battery_v is None and back[100].currents[:2] == [2.0, 2.0])
    from app.robot import batch
    copy = tmp / "copied" / path.name
    copy.parent.mkdir()
    copy.write_bytes(path.read_bytes())
    batch.copy_companions(path, copy)
    ok_copy = copy.with_suffix(".dsevents").is_file()
    batch.remove_with_companions(copy)
    check("a folder import copies the .dsevents with its .dslog, and removes it with it",
          ok_copy and not copy.with_suffix(".dsevents").exists()
          and batch.describe_header(path.read_bytes()[:512], ".dslog")[1])
    r = import_log(path, db.path)
    row = db.fetchone("SELECT uid, source_kind, started_at, match_key FROM log_session "
                      "WHERE id = ?", (r.session_id,))
    ds_uid = row["uid"]
    check("it imports as a session: its start from its own header, its match from the FMS",
          row["source_kind"] == "dslog" and row["started_at"] == "2026-11-05 09:58:00"
          and row["match_key"] == "qm14", str(dict(row)))
    ph = tools.match_phases(ds_uid)
    check("match phases read the DS's own command (auto 125-140 s, then teleop)",
          ph.get("auto_s") == 15.0 and ph.get("teleop_s") == 45.0, str(ph)[:200])
    kinds = {e["kind"] for e in events.find(db, r.session_id, [(125, 185)])}
    check("the DS log's brownout, comms loss and the roboRIO's reboot become events",
          {"brownout", "comms_lost", "roborio_reboot"} <= kinds, str(sorted(kinds)))

    # ── what the team's real DS logs taught it (USB drive, 2026-11-05) ────
    real = []
    for i in range(int(300 / 0.02)):                     # a 300 s DS log
        t = i * 0.02
        en = 100 <= t < 120 or 123 <= t < 263           # 20 s auto, the 3 s gap, 140 s teleop
        off = 20 <= t < 40 or 200 <= t < 258             # robot off in the queue; a reboot
        blip = en and 150 <= t < 150.3                   # a brownout's instant of disable
        real.append(dslog.Record(
            trip_ms=4.0, packet_loss=0.0, battery_v=None if off else (6.6 if blip else 12.3),
            cpu=0.5, can=0.45, wifi_db=40.0, wifi_mb=1.5, brownout=blip, watchdog=False,
            ds_teleop=en and t >= 123 and not blip, ds_disabled=(not en) or blip,
            robot_teleop=False, robot_auto=False, robot_disabled=not en,
            # Varying, as a real robot's draw always does: storage keeps the
            # 1 s rows of a value that changes.
            pd_type=dslog.PD_REV, currents=[1.0] * 23 + [1.0 + (i % 2)]))
    msgs = [(5.0, "<TagVersion>1 <message> FMS Connected:   None - 0:0, Field Time: 0"),
            (6.0, "<TagVersion>1 <message> Info roboRIO: Disk Free 14299 MB, Memory Free 12 MB."
                  " DS Laptop: CPU 40% Batt 80%."),
            (17.0, "<TagVersion>1 <message> Info roboRIO: Disk Free 14299 MB, Memory Free 13 "
                   "MB. DS Laptop: CPU 40% Batt 80%."),
            (60.0, "<TagVersion>1 <Code> 44021 <details> Radio Version: VH-109_2.0.1-02062026"
                   "FRC: Please update Radio firmware."),
            (201.0, "<TagVersion>1 <Code> 44002 <details> Ping Results: link-bad, DS "
                    "radio(.4)-GOOD, robot radio(.1)-bad, roboRIO(.2)-bad, FMS-GOOD"),
            (258.5, "<TagVersion>1 <message> ********** Robot program starting **********")]
    msgs += [(100.0 + k * 0.1, "<TagVersion>1 <message> Indexer setState called with: FIRE")
             for k in range(300)]
    rpath = tmp / "2026_11_05 10_30_00 Thu.dslog"
    dslog.write(rpath, start + 1800, real, msgs)
    rr = import_log(rpath, db.path)
    rrow = db.fetchone("SELECT uid, started_at, match_key FROM log_session WHERE id = ?",
                       (rr.session_id,))
    rph = tools.match_phases(rrow["uid"])
    check("a brownout's instant of disable doesn't end the match: 20 s auto, 140 s teleop",
          rph.get("auto_s") == 20.0 and abs((rph.get("teleop_s") or 0) - 140.0) < 0.5,
          str({k: rph.get(k) for k in ("auto_s", "teleop_s")}))
    check("…\"None - 0:0\" off the field is no match, and the start is UTC",
          rrow["match_key"] is None and rrow["started_at"] == "2026-11-05 10:28:00",
          str(dict(rrow)))
    rev = events.find(db, rr.session_id, incidents._windows(rph))
    by = {}
    for e in rev:
        by.setdefault(e["kind"], []).append(e)
    queue = [e for e in by.get("comms_lost", []) if e["t_s"] < 50]
    check("…the robot switched off in the queue is no comms fault; the reboot mid-match is",
          queue and all(e["severity"] == "info" for e in queue)
          and any(e["severity"] == "fault" for e in by.get("comms_lost", []) if e["t_s"] > 190)
          and by.get("roborio_reboot") and by["roborio_reboot"][0]["value"] >= 50,
          str({k: [(round(e["t_s"]), e["severity"]) for e in v] for k, v in by.items()})[:300])
    check("…which hop the ping lost is evidence: the robot's radio, so not the Ethernet",
          "ping_radio_bad" in by, str(sorted(by)))
    spam = (by.get("console_spam") or [{}])[0]
    check("…a line printed 300 times is counted though storage keeps it once",
          spam.get("value") == 300 and "Indexer setState" in spam.get("detail", ""),
          str(spam)[:160])
    mom = incidents.moment(db, rrow["uid"], 150.0, 3.0, tools.match_phases)
    check("…the DS's PDH channels summed are the robot's total current, for the diagnosis",
          (mom.get("readings") or {}).get("total_current_a") == 25.0,
          str(mom)[:300])
    lost = events._lost_time([(100.0, 120.0), (123.0, 140.0), (153.0, 263.0)])
    check("…on the field, a disable past the 3 s auto-teleop gap is match time lost",
          len(lost) == 1 and lost[0]["value"] == 13.0 and lost[0]["kind"] == "disabled_mid_match",
          str(lost)[:160])
    check("…and the roboRIO's low memory and the radio's firmware note are found",
          (by.get("low_memory") or [{}])[0].get("value") == 12 and "radio_firmware" in by,
          str(sorted(by)))

    # ── what else was on the team's drive (2026-11-05) ───────────────────
    from app.robot import batch as _batch, owlet as _owlet
    page = b"<!DOCTYPE html>\r\n<html><head><title>Not Found</title></head><body>Access Error: 404"
    check("a 404 page saved under a log's name is called a failed copy, not a bad log",
          "copy from the robot failed" in _batch.describe_header(page, ".hoot")[2]
          and "404" in _batch.describe_header(page, ".wpilog")[2])
    old = tmp / "rio_2025-10-04_08-24-57.hoot"
    old.write_bytes(b"roboRIO Native CAN Bus".ljust(0x46, b"\0") + bytes([13, 0]) + b"\0" * 64)
    err = ""
    try:
        _owlet.convert(old, tmp / "old.mcap")
    except _owlet.OwletError as e:
        err = str(e)
    check("last season's hoot (format 13) names the owlet that reads it, from CTRE's index",
          _owlet.hoot_compliancy(old) == 13 and "2025 hoot" in err and "25.4.1" in err
          and "redist.ctr-electronics.com" in err, err[:160])
    check("…and this season's test hoot is format 19, the bundled owlet's",
          _owlet.hoot_compliancy(ROOT / "TEST_LOGS" / "rio_2026-08-17_03-42-54.hoot") == 19
          and _owlet.COMPLIANCY_OF.get(_owlet._version(_owlet.find_owlet(19))) == 19)

    # ── a robot log recorded with it (AdvantageKit), on one clock ────────
    with db.transaction() as c:
        c.execute("INSERT INTO log_session (source_file, source_name, source_kind, "
                  "started_at, duration_s, uid) VALUES ('csa-akit', 'akit_26-11-05_10-00-00.wpilog', "
                  "'wpilog', '2026-11-05 10:00:00', 80.0, 'csa-akit')")
        sid = c.execute("SELECT id FROM log_session WHERE uid='csa-akit'").fetchone()[0]
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES ('Robot', -1)")
        c.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES ('TalonFX', 40)")
        robot = c.execute("SELECT id FROM device WHERE device_type='Robot'").fetchone()[0]
        motor = c.execute("SELECT id FROM device WHERE device_type='TalonFX' AND can_id=40"
                          ).fetchone()[0]

        def put(dev, dtype, name, points=(), seconds=()):
            c.execute("INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class) "
                      "VALUES (?, ?, 'num', 'telemetry')", (dtype, name))
            g = c.execute("SELECT id FROM signal WHERE device_type=? AND name=?",
                          (dtype, name)).fetchone()[0]
            c.execute("INSERT INTO series (session_id, device_id, signal_id) VALUES (?, ?, ?)",
                      (sid, dev, g))
            se = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.executemany("INSERT INTO samples.sample VALUES (?, ?, 0, ?)",
                          [(se, t, v) for t, v in points])
            c.executemany("INSERT INTO samples.sample_1s (series_id, t_s, v_min, v_max, v_avg, n)"
                          " VALUES (?, ?, ?, ?, ?, 1)", [(se, t, v, v, v) for t, v in seconds])

        # Enabled 6 s in by its own clock (the DS: 10:00:05, so 1 s off), the
        # brownout at 30 s (10:00:30 on the DS: 150 s after 09:58:00).
        put(robot, "Robot", "DriverStation/Enabled", [(0, 0), (6000, 1), (66000, 0)])
        put(robot, "Robot", "SystemStats/BrownedOut", [(0, 0), (31000, 1), (31400, 0)])
        put(robot, "Robot", "SystemStats/BatteryVoltage", [(0, 12.4)],
            [(t, 6.2 if t == 31 else 12.4) for t in range(0, 80)])
        put(robot, "Robot", "PowerDistribution/TotalCurrent", [(0, 40.0)],
            [(t, 60.0) for t in range(0, 80)])
        put(motor, "TalonFX", "ResetCount", [(0, 0), (31200, 1)])
    rec = incidents.recording(db, "csa-akit")
    check("a recording takes in the DS log whose span covers the robot's log",
          {r["uid"] for r in rec} == {"csa-akit", ds_uid}, str([r["source_name"] for r in rec]))
    got = incidents.build(db, "csa-akit", tools.match_phases)
    offs = {lg["kind"]: lg["offset_s"] for lg in got["logs"]}
    check("the logs are put on one clock, nudged so their first enables line up",
          offs.get("dslog") == 0.0 and offs.get("wpilog") == 119.0, str(offs))
    worst = got["incidents"][0] if got["incidents"] else {}
    check("what happened at the same moment in different logs is one incident",
          {"brownout", "device_reset"} <= set(worst.get("kinds") or [])
          and len(worst.get("logs") or []) == 2, str(worst.get("kinds")))
    issue = next((i for i in tools.incidents("csa-akit")["issues"]
                  if i["primary"] == "brownout"), None)
    check("…and its readings at that moment come with it (battery, total current)",
          issue is not None and issue["readings"].get("battery_v") == 6.2
          and issue["readings"].get("total_current_a") == 60.0, str(issue and issue["readings"]))
    cands = csa.match(issue, {"readings": issue["readings"]}) if issue else []
    check("the knowledge base puts a loose main connection first: a controller rebooted, "
          "the current was low", bool(cands) and cands[0]["id"] == "brownout.loose_main_power"
          and any("device_reset" in f for f in cands[0]["for"]),
          str([(c["id"], c["score"]) for c in cands]))
    check("the DS log's comms loss with a fresh boot is a roboRIO reboot issue",
          any(i["primary"] == "roborio_reboot" for i in got["issues"]))
    check("the console's words are read by meaning",
          events.classify("Talon ID 5 failed config with error TxFailed") == "config_failed"
          and events.classify("Error at java.io.FileInputStream.open(Native Method): "
                              "/home/lvuser/deploy/pathplanner/autos/Left.auto") == "missing_file"
          and events.classify("HAL: CAN Receive has Timed Out") == "can_timeout"
          and events.classify("error: no devices/emulators found") is None)

    # ── the diagnosis: the model reasons, code checks ────────────────────
    led = checks.Ledger()
    led.add("incidents", tools.incidents("csa-akit"))
    good = {"cause": "brownout.loose_main_power", "confidence": "high",
            "why": "A controller rebooted with the brownout while the robot drew only 60 A.",
            "check": "Wiggle-test the battery leads and the main breaker.", "ruled_out": []}

    def run(*answers):
        llm = Scripted(["Looking at the evidence."] + list(answers))
        return diagnose.diagnose(LocalToolbox(), llm, "scripted", "csa-akit", issue, led), llm

    d, _ = run(good)
    check("a diagnosis that follows the facts passes", d.by == "model"
          and d.cause == "brownout.loose_main_power" and not d.problems, str(d.problems))
    d, llm = run(dict(good, cause="brownout.made_up"), good)
    check("a cause that isn't a candidate is sent back", d.by == "model" and llm.calls == 3
          and any("isn't a candidate" in p for p in d.problems), str(d.problems))
    d, _ = run(dict(good, why="The battery fell to 5.1 V."), good)
    check("a figure the evidence doesn't hold is sent back", any("5.1" in p for p in d.problems))
    low = next(c for c in cands if c["score"] < cands[0]["score"])
    d, _ = run(dict(good, cause=low["id"], confidence="medium"), good)
    check("a cause the facts score lower, picked without looking closer, is sent back",
          any("favour" in p for p in d.problems), str(d.problems))
    weak = dict(good, cause="brownout.weak_battery")
    d, _ = run(dict(weak, confidence="high"), dict(weak, confidence="high"))
    check("two wrong answers: code answers from the facts, low confidence, marked",
          d.by == "code" and d.confidence == "low" and d.cause == "brownout.loose_main_power",
          f"{d.by} {d.cause} {d.confidence}")
    f = diagnose.to_finding(issue, d, "csa-akit")
    check("a diagnosis is a finding the contract takes, its figure from the incidents tool",
          not __import__("app.ai.schema", fromlist=["x"]).validate(
              {"schema": 1, "session_uids": ["csa-akit"], "findings": [f]},
              __import__("app.ai.schema", fromlist=["x"]).load("insight"))
          and f["metric"]["value"] == 6.2 and len(f["claim"]) <= 200, str(f)[:200])

    # ── in the pipeline: diagnosed first, on the board even if the analyst fails ──
    liar = {"schema": 1, "session_uids": ["csa-akit"], "question": "q", "findings": [
        {"id": "made_up", "claim": "The battery fell to 2 V.", "severity": "warn",
         "metric": {"name": "battery", "value": 2.0, "unit": "V"},
         "evidence": [{"tool": "session_overview", "args": {"session_uid": "csa-akit"},
                       "value": 2.0}]}]}
    n_issues = len([i for i in tools.incidents("csa-akit")["issues"]
                    if i["severity"] in ("fault", "warn")][:_pl.MAX_DIAGNOSE])
    script = []
    for _ in range(n_issues):
        script += ["Thinking.", {"cause": "unexplained", "confidence": "low",
                                 "why": "Not enough to tell.", "check": "Look at the logs."}]
    board = {"title": "CSA", "headline": {"title": "Brownout", "sentence": "Check the power."},
             "vitals": [], "faults": [], "charts": [],
             "footer": {"left": "csa", "right": "check"}}
    script += ["Done.", liar, liar, liar, board]
    llm = Scripted(script)
    res = _pl.analyse(LocalToolbox(), SqliteSink(machine_id="pit-aicheck"), llm, ["csa-akit"],
                      analyst="scripted", diagnose_issues=True)
    ids = [x["id"] for x in (res.insights or {}).get("findings") or []]
    check("the CSA's diagnoses go on the board even when the analyst's findings don't pass",
          res.status == "published" and any(i.startswith("csa_") for i in ids)
          and "made_up" not in ids and len(res.stats.incidents) == n_issues,
          f"{res.status} {res.reason[:120]} {ids}")

    # ── the first look: the CSA's board up early, laid out by code ───────
    from app.ai import checks as _ck, layout
    from app.ai.ollama import Reply
    bad_board = dict(board, headline={"title": "Brownout", "sentence": "It fell to 99 V."})
    script = []
    for _ in range(n_issues):
        script += ["Thinking.", {"cause": "unexplained", "confidence": "low",
                                 "why": "Not enough to tell.", "check": "Look at the logs."}]
    script += ["Done.", Reply(content='{"schema": 1, "findings": [{"id": "a", "claim": "a a a',
                              truncated=True), liar, liar, bad_board, bad_board, bad_board]
    published: list[str] = []
    boards_seen: list[dict] = []

    def on_board(uid: str) -> None:
        published.append(uid)
        row = db.fetchone("SELECT spec FROM analysis_board WHERE uid = ?", (uid,))
        boards_seen.append(json.loads(row["spec"]) if row else {})

    res = _pl.analyse(LocalToolbox(), SqliteSink(machine_id="pit-aicheck"), Scripted(script),
                      ["csa-akit"], analyst="scripted", diagnose_issues=True,
                      publish_early=True, on_board=on_board)
    first = boards_seen[0] if boards_seen else {}
    check("the CSA's diagnoses go up first, laid out by code, before the analyst is done",
          len(published) == 2 and published[0] == published[1] == res.board_uid
          and (first.get("footer") or {}).get("left", "").startswith("First look")
          and all(c.get("finding", "").startswith(("csa_", "detector_"))
                  for c in (first.get("vitals") or []) + (first.get("faults") or []))
          and res.stats.early_s is not None,
          f"{published} {first.get('footer')} {res.stats.recovered} {res.stats.early_s}")
    check("…an answer cut off at its token cap goes back saying so, not as bad JSON",
          res.stats.truncated == 1 and any("cut off" in str(m.get("content"))
                                           for m in res.transcript["analyst"]
                                           if m["role"] == "user"), str(res.stats.truncated))
    check("…a designer board rejected twice is laid out by code, not thrown away",
          res.status == "published" and res.board["models"]["designer"] == layout.DESIGNER
          and not [p for p in _ck.check_board(res.board, res.insights, [])
                   if not p.startswith("title")]
          and any("laid out by code" in r for r in res.stats.recovered),
          f"{res.status} {res.reason[:120]} {res.board['models']} {res.stats.recovered}")
    d0 = layout.draft({"findings": [
        {"id": "x", "severity": "warn", "claim": "Battery sagged to 11.25 V under load: swap it",
         "metric": {"name": "battery", "value": 11.25, "unit": "V"}}]})
    check("…the code's board cuts whole words, its figure only from the finding",
          d0["vitals"][0]["value"] == "11.25" and d0["headline"]["title"] == "Battery sagged "
          "to 11.25 V under load", str(d0)[:200])

    # ── many detector items: grouped, and written by code ────────────────
    led = _ck.Ledger()
    led.add("anomalies", {"args": {"session_uid": "csa-akit"}, "anomalies": [
        {"kind": "missing", "severity": "fault", "device": f"TalonFX {n}", "device_type":
         "TalonFX", "can_id": n, "present_in": 5, "detail": "on this bus before, not now",
         "must_cover": True} for n in (12, 13, 14, 15, 16)]})
    pre = _pl._detector_findings(led, {"findings": []})
    check("five controllers missing from one bus are one finding naming all five, not five",
          len(pre) == 1 and all(f"TalonFX {n}" in pre[0]["claim"] for n in (12, 13, 14, 15, 16))
          and not _ck.anomaly_coverage({"findings": pre}, led) and len(pre[0]["claim"]) <= 200,
          str([f["claim"] for f in pre])[:200])
    tail = _pl._code_wrote(pre)
    check("…and past a few items the model is told they're on the board, not to restate them",
          "ALREADY ON THE BOARD" in tail and pre[0]["claim"] in tail, tail[:120])

    d1 = {"vitals": [{"value": "6.425 V"}, {"value": "3 A", "unit": "V"}]}
    _pl._split_units(d1)
    check("a card value written with its unit gets the unit moved out, a clash left to the check",
          d1["vitals"][0] == {"value": "6.425", "unit": "V"}
          and d1["vitals"][1]["value"] == "3 A", str(d1))

    # ── diagnosable: the motor, the bus, and the reach kept apart ────────
    bo = {"id": "brownout", "primary": "brownout", "severity": "fault", "count": 4,
          "at": ["8:44"], "phases": ["teleop"], "kinds": ["brownout"], "devices": [],
          "worst": {"at": "8:44", "events": []}, "figures": {"V": 6.6},
          "readings": {"total_current_a": 250.0, "most_supply_current": [
              {"device": "TalonFX 15", "supply_a_max": 66.0, "bus": "roboRIO CAN",
               "session_uid": "csa-akit", "device_type": "TalonFX", "can_id": 15},
              {"device": "TalonFX 16", "supply_a_max": 62.0, "bus": "roboRIO CAN"}]}}
    fb = diagnose.to_finding(bo, diagnose.Diagnosis(issue="brownout"), "csa-akit")
    check("a brownout names the motors that drew the most, and its card draws the first",
          "TalonFX 15 66 A, TalonFX 16 62 A (roboRIO CAN)" in fb["claim"]
          and (fb["series"] or {}).get("can_id") == 15
          and any("whole robot 250 A" in m for m in diagnose.measured(bo)), fb["claim"])
    rs = dict(bo, id="rs", primary="device_fault:RemoteSensorDataInvalid", readings={},
              buses={"CANivore": ["TalonFX 10", "TalonFX 18", "TalonFX 8"]})
    fr2 = diagnose.to_finding(rs, diagnose.Diagnosis(issue="rs"), "csa-akit")
    check("…encoders dropping out name their bus and the controllers",
          "CANivore: TalonFX 10, 18, 8" in fr2["claim"]
          and "all 3 on one bus (CANivore)" in diagnose.measured(rs), fr2["claim"])
    reach = dict(good, hunch="Maybe a swerve module is binding; feel each one turn by hand.")
    loose = dict(good, hunch="It fell to 3.3 V once.")
    check("…a hunch is allowed and kept; a figure in it still has to be in the evidence",
          not [p for p in diagnose.check(reach, cands, diagnose._pool(
              _ck.Ledger(), issue, cands)) if "hunch" in p]
          and any("hunch" in p for p in diagnose.check(loose, cands, diagnose._pool(
              _ck.Ledger(), issue, cands))))

    # ── new or recurring: the robot's earlier recordings ─────────────────
    from app.ai import csa as _csa
    seen_before = dict(issue, before={"in": 5, "of": 10, "streak": 3, "last": "2026-09-21"})
    once = dict(issue, before={"in": 0, "of": 10, "new": True})
    ok_r, fact = _csa._test({"recurring_gte": 3}, seen_before, {})
    no_r, _ = _csa._test({"recurring_gte": 3}, once, {})
    few, _ = _csa._test({"recurring_gte": 3}, dict(issue, before={"in": 1, "of": 2}), {})
    check("an issue in 5 of the last 10 recordings counts as recurring; too few to say is no fact",
          ok_r is True and no_r is False and few is None and "5 of the last 10" in fact, fact)
    wb = lambda it: next(c for c in _csa.match(it, {"readings": it.get("readings") or {}})  # noqa: E731
                         if c["id"] == "brownout.weak_battery")
    check("…a brownout every match argues against one weak battery",
          wb(seen_before)["score"] < wb(once)["score"],
          f"{wb(seen_before)['score']} vs {wb(once)['score']}")
    fr = diagnose.to_finding(seen_before, d, "csa-akit")
    fn = diagnose.to_finding(once, d, "csa-akit")
    from app.robot import incidents as _inc
    newest = db.fetchone("SELECT uid FROM log_session WHERE started_at IS NOT NULL "
                         "ORDER BY started_at DESC LIMIT 1")["uid"]
    hist = _inc.history(tools.connection(), newest, tools.match_phases)
    check("…the history reads every earlier recording, match keys and all", hist["of"] >= 1
          and all("in" in h for h in hist["issues"].values()), str(hist)[:160])
    check("…and the finding says new or recurring first",
          "also in 5 of the last 10 recordings" in fr["claim"]
          and "new, not in the last 10 recordings" in fn["claim"], fr["claim"])

    # ── the crew says what it really was ─────────────────────────────────
    from app.widgets.analysis_panel import AnalysisPanel
    from app.widgets.brand_widgets import SelectableChip
    app = _QAPP
    panel = AnalysisPanel()
    panel._selected = res.run_id
    panel._reload()
    app.processEvents()
    chips = [c for c in panel.findChildren(SelectableChip)
             if str(c.property("reason") or "").startswith(feedback.CONFIRMED)]
    check("each diagnosis offers 'what was it really?', one chip per candidate", bool(chips),
          f"{len(chips)} chips")
    if chips:
        chips[0].click()
        app.processEvents()
        key = str(chips[0].property("reason"))[len(feedback.CONFIRMED):]
        check("…a tap records it, rates the finding useful, and the next diagnosis sees it",
              feedback.confirmations().get(key) == 1
              and feedback.verdicts(res.run_id)[str(chips[0].property("finding"))]["rating"]
              == "useful", str(feedback.confirmations()))
    panel.deleteLater()
    app.processEvents()
    for u in ("csa-akit", ds_uid):
        for r in db.fetchall("SELECT id FROM log_session WHERE uid = ?", (u,)):
            delete_session(r["id"], db.path)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def _board_without(board: dict, finding_id: str) -> dict:
    """The designer's board once a finding was dropped: no card or chart for it."""
    import copy
    b = copy.deepcopy(board)
    for region in ("vitals", "faults", "subsystems", "charts"):
        if isinstance(b.get(region), list):
            b[region] = [c for c in b[region] if c.get("finding") != finding_id]
    return b


_QAPP = None


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
    global _QAPP                        # outlives this function: ask_section reuses it
    app = _QAPP = QApplication.instance() or QApplication([])
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
    with db.transaction() as conn:
        conn.execute("INSERT INTO analysis_run (session_uids, analyst, status, reject_reason, "
                     "stats, started_at, finished_at, uid) VALUES ('[]', ?, 'failed', "
                     "'OperationalError: link failure', ?, 0, 0, 'check-failed-run')",
                     (model, json.dumps({"prompt_version": PROMPT_VERSION})))
    after = next(r for r in feedback.scoreboard()
                 if r["model"] == model and r["prompt"] == PROMPT_VERSION)
    check("a failed run (database or engine gone) is counted but doesn't lower the share "
          "that passed the checks", after["failed"] == row["failed"] + 1
          and after["published"] == row["published"], str(after))
    with db.transaction() as conn:
        conn.execute("DELETE FROM analysis_run WHERE uid = 'check-failed-run'")
    fb_uid = db.fetchone("SELECT uid FROM analysis_feedback WHERE run_id = ? AND finding_id = ''",
                         (run_id,))["uid"]
    run_uid = db.fetchone("SELECT uid FROM analysis_run WHERE id = ?", (run_id,))["uid"]
    check("a verdict's uid is its run's uid + '#' + finding, and it's queued to sync",
          fb_uid == f"{run_uid}#" and db.fetchone(
              "SELECT COUNT(*) n FROM sync_outbox WHERE tbl = 'analysis_feedback' AND uid = ?",
              (fb_uid,))["n"] == 1)

    # What it looked at (app/ai/trace.py): every tool call, read back.
    trace_chip = [c for c in panel.findChildren(SelectableChip)
                  if c.property("run_id") == run_id and "looked at" in c.text()]
    trace_chip[0].click()
    app.processEvents()
    from PyQt6.QtWidgets import QLabel
    shown = " ".join(l.text() for l in panel.findChildren(QLabel) if l.isVisibleTo(panel))
    check("'What it looked at' lists the logs and every tool call with its result",
          "faults(" in shown and "series_stats(" in shown and "Logs:" in shown and "→" in shown)
    panel.close()

    # A batch: every log gets a run, in turn, after the import finishes.
    print("\nA batch: every log analysed, not just the newest")
    ran: list = []
    svc.run_finished.connect(lambda rid, st: ran.append(rid))
    svc.hold(True)                       # an import is running
    svc.enqueue("batch-log-a")
    svc.enqueue("batch-log-b")
    svc.enqueue("batch-log-a")           # queued once
    check("held during the import: queued, nothing running",
          svc.queued == 2 and not svc.busy, f"queued {svc.queued}")
    loop2 = QEventLoop()
    svc.run_finished.connect(lambda *_: loop2.quit() if not svc.queued and not svc.busy else None)
    QTimer.singleShot(60_000, loop2.quit)
    svc.hold(False)
    loop2.exec()
    looked = {r["session_uids"] for r in db.fetchall(
        "SELECT session_uids FROM analysis_run WHERE session_uids LIKE '%batch-log-%'")}
    check("released: each queued log got its own run, in order",
          looked == {'["batch-log-a"]', '["batch-log-b"]'} and svc.queued == 0, str(looked))

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


def ask_section(uid: str) -> None:
    """Ask the pit (app/ai/ask.py): the loop, the figure check, the service, the panel."""
    from app.ai import ask, tools
    from app.ai.local import LocalToolbox
    print("\nAsk the pit")
    ov = tools.session_overview(uid)
    real = next(v for v in __import__("app.ai.checks", fromlist=["numbers"]).numbers(ov)
                if v > 1 and v != int(v))
    shown = f"{real:.1f}"
    tb = LocalToolbox()

    llm = Scripted([[("session_overview", {"session_uid": uid})],
                    f"In the newest log the figure was {shown}."])
    a = ask.ask(tb, llm, "scripted", "What was it in the newest log?")
    check("a figure a tool returned is answered, display-rounded", a.status == "answered"
          and not a.unverified and a.calls == [{"tool": "session_overview",
                                                "args": {"session_uid": uid}}], str(a))

    llm = Scripted([[("session_overview", {"session_uid": uid})],
                    "The motor peaked at 987.65 amps.", "It peaked at 987.65 amps."])
    a = ask.ask(tb, llm, "scripted", "How high did it peak?")
    check("a figure no tool returned goes back once, then is marked unverified",
          a.status == "unverified" and a.unverified == ["987.65"] and llm.calls == 3, str(a))

    llm = Scripted([[("session_overview", {"session_uid": uid})],
                    "It peaked at 987.65 amps.", f"The logs show {shown}."])
    a = ask.ask(tb, llm, "scripted", "How high did it peak?")
    check("…and a corrected answer passes", a.status == "answered" and not a.unverified, str(a))

    a = ask.ask(tb, Scripted(["Your question names qm14 and TalonFX 17: nothing more found."]),
                "scripted", "Did TalonFX 17 fault in qm14?")
    check("numbers from the question itself aren't flagged", a.status == "answered", str(a))

    wrong = "The hottest motor was TalonFX 9 at 26.0 °C."
    a = ask.ask(tb, Scripted([wrong, wrong]), "scripted", "Which motor ran hottest?")
    check("a real figure pinned on a device no row ties it to is caught (a real run did this)",
          a.status == "unverified" and any(u.startswith("26.0 → talonfx 9") for u in a.unverified),
          str(a.unverified))
    a = ask.ask(tb, Scripted([wrong, [("series_stats", {"session_uid": uid, "signal": "DeviceTemp"})],
                              "Front-Left Drive Motor Long Name reached 26.0 °C."]),
                "scripted", "Which motor ran hottest?")
    check("…and sent back, it looks the device up and passes", a.status == "answered"
          and any(c["tool"] == "series_stats" for c in a.calls), str(a))

    class Broken:
        def chat(self, *a, **k):
            raise ConnectionError("engine went away")
    a = ask.ask(tb, Broken(), "scripted", "Anything?")
    check("an engine that dies is a failed answer, never an exception",
          a.status == "failed" and "engine went away" in a.error)

    # The service and the panel, offscreen, behind a running analysis.
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication
    from app.ai.service import analysis
    from app.widgets.ask_panel import AskPanel
    app = _QAPP or QApplication.instance()
    analysis._llm_factory = lambda: Scripted([[("faults", {"session_uid": uid})],
                                              "No latched faults in the newest log."])
    panel = AskPanel()
    panel.resize(900, 900)
    panel.show()
    analysis.busy = True                # stands in for an analysis run in progress
    panel.ask("Any faults in the newest log?")
    check("asked while an analysis runs: it waits, ahead of queued logs",
          analysis.questions_waiting == 1 and not analysis.asking)
    analysis.busy = False
    got: list = []
    loop = QEventLoop()
    analysis.answered.connect(got.append)
    analysis.answered.connect(loop.quit)
    QTimer.singleShot(30_000, loop.quit)
    analysis._next()                    # what a finishing run does
    loop.exec()
    app.processEvents()
    check("…then it's answered off the GUI thread, and the panel shows it",
          got and got[0].status == "answered" and analysis.answers[-1] is got[0]
          and panel._answers.count() >= 1, str(got))
    from app import paths
    log = paths.data("ask_log.jsonl")
    check("every answer is logged for judging the prompt later",
          log.exists() and "Any faults in the newest log?" in log.read_text(encoding="utf-8"))
    panel.ask("   ")
    check("an empty question isn't sent", "Type a question" in panel._state.text())
    panel.close()


def _wear_logs() -> list[str]:
    """Nine logs on a bus of their own, eight motors running steadily at 20 rps.
    Planted: TalonFX 41 needs 50% more current for the same speed in logs 6-8;
    TalonFX 42 heats 8 °C more than the others in 6-8; 41, 42 and 43 fault 15
    times more in 6-8 (a shared cause, three of eight, as on the team's
    CANivore); every motor heats 10 °C more in log 8
    (a hard session, robot-wide); log 9 has 41 back to normal. Returns uids."""
    import sqlite3
    from app.db import db
    conn = sqlite3.connect(str(db.path))
    conn.execute("ATTACH DATABASE ? AS samples", (str(db.samples_path),))
    sig = {}
    for name in ("RotorVelocity", "StatorCurrent", "DeviceTemp", "Fault_RemoteSensorDataInvalid"):
        conn.execute("INSERT OR IGNORE INTO signal (device_type, name) VALUES ('TalonFX', ?)", (name,))
        sig[name] = conn.execute("SELECT id FROM signal WHERE device_type = 'TalonFX' AND name = ?",
                                 (name,)).fetchone()[0]
    dev = {}
    for can in range(41, 49):
        conn.execute("INSERT OR IGNORE INTO device (device_type, can_id) VALUES ('TalonFX', ?)", (can,))
        dev[can] = conn.execute("SELECT id FROM device WHERE device_type = 'TalonFX' AND can_id = ?",
                                (can,)).fetchone()[0]
    uids = []
    for n in range(1, 10):
        cur = conn.execute(
            "INSERT INTO log_session (source_file, source_name, source_kind, device_serial, "
            "started_at, duration_s, uid) VALUES (?, ?, 'hoot', 'WEARCHECK', ?, 120, ?)",
            (f"/wear/{n}.hoot", f"WEARCHECK_{n}.hoot", f"2026-10-0{n} 12:00:00", f"wearcheck{n:02d}"))
        sid = cur.lastrowid
        uids.append(f"wearcheck{n:02d}")
        recent = 6 <= n <= 8
        for can, did in dev.items():
            k = 0.45 if (can == 41 and recent) else 0.30
            rise = 10.0 + (8.0 if can == 42 and recent else 0.0) + (10.0 if n == 8 else 0.0)
            ids = {}
            for name in ("RotorVelocity", "StatorCurrent", "DeviceTemp"):
                lo, hi = (25.0, 25.0 + rise) if name == "DeviceTemp" else (None, None)
                c = conn.execute("INSERT INTO series (session_id, device_id, signal_id, n_raw, "
                                 "n_stored, v_min, v_max) VALUES (?, ?, ?, 60, 60, ?, ?)",
                                 (sid, did, sig[name], lo, hi))
                ids[name] = c.lastrowid
            for t in range(60):
                conn.execute("INSERT INTO samples.sample_1s VALUES (?, ?, 19.5, 20.5, 20.0, 50)",
                             (ids["RotorVelocity"], t))
                conn.execute("INSERT INTO samples.sample_1s VALUES (?, ?, ?, ?, ?, 50)",
                             (ids["StatorCurrent"], t, k * 20 - 0.2, k * 20 + 0.2, k * 20))
            faults = 2 + (15 if can in (41, 42, 43) and recent else 0)
            for i in range(faults):
                conn.execute("INSERT INTO fault_event (session_id, device_id, signal_id, sticky, "
                             "t_ms_start) VALUES (?, ?, ?, 0, ?)",
                             (sid, did, sig["Fault_RemoteSensorDataInvalid"], i * 1000))
    conn.commit()
    conn.close()
    return uids


def wear_section(uid: str) -> None:
    """Wear (app/robot/wear.py) and its path through the whole analysis."""
    from app.ai import ask, checks, pipeline, tools
    from app.ai.local import LocalToolbox
    print("\nWear: motors getting worse across logs")
    logs = _wear_logs()
    got = tools.call("wear", {"session_uid": logs[7]})
    warn = {(w["measure"], w["device"]): w for w in got["warnings"]}
    load = warn.get(("load_per_speed", "TalonFX 41"))
    check("more current for the same speed on one motor is a wear warning, with the trend",
          load is not None and load["value"] == 9.0 / 20.0 and load["normal"]["median"] == 0.3
          and load["change_pct"] == 50 and len(load["trend"]) == 8, str(got["warnings"])[:400])
    check("a motor heating more than the others, more each log, is one",
          ("heat_vs_peers", "TalonFX 42") in warn, str(list(warn)))
    group = next((w for w in got["warnings"] if w["measure"] == "faults_vs_peers"), None)
    check("three motors faulting more at once is one warning for the bus, naming them",
          group is not None and group["devices"] and len(group["devices"]) == 3
          and group["device_type"] is None and "shared cause" in group["detail"], str(group))
    check("a robot-wide hotter session flags nobody's heat but the drifting motor",
          [d for m, d in warn if m == "heat_vs_peers"] == ["TalonFX 42"], str(list(warn)))
    check("a wear warning is a warning, never a fault",
          all(w["severity"] == "warn" for w in got["warnings"]))
    later = tools.call("wear", {"session_uid": logs[8]})
    check("once the motor's back to normal, its load warning is gone",
          not any(w["measure"] == "load_per_speed" for w in later["warnings"]),
          str(later["warnings"])[:300])
    early = tools.call("wear", {"session_uid": logs[2]})
    check("too few logs is a note, not a judgement", not early["warnings"] and early["note"])
    an = tools.call("anomalies", {"session_uid": logs[7]})
    check("the anomaly detector carries the wear warnings",
          sum(a["kind"] == "wear" for a in an["anomalies"]) == len(got["warnings"]))

    # Through the analysis: coverage, the detector's own findings, cards, charts.
    ledger = checks.Ledger()
    ledger.add("anomalies", an)
    other = {"findings": [{"id": "talonfx_41_faults", "claim": "TalonFX 41 latched faults.",
                           "metric": {"name": "faults", "value": 17, "unit": ""},
                           "severity": "warn", "evidence": []}]}
    check("a finding that only names the motor doesn't count as covering its wear",
          any("as wearing" in p and "TalonFX 41" in p
              for p in checks.anomaly_coverage(other, ledger)))
    good = {"findings": [{"id": "talonfx_41_wear", "claim": "TalonFX 41 is wearing: more "
                          "current for the same speed.", "metric": {"name": "load per speed",
                          "value": load["value"], "unit": "A/rps", "compare_to": 0.3},
                          "severity": "warn", "evidence": [{"tool": "anomalies",
                          "args": {"session_uid": logs[7]}, "value": load["value"]}]}]}
    left = checks.anomaly_coverage(good, ledger)
    check("…a finding about its wear does", not any("TalonFX 41" in p for p in left), str(left))
    made = pipeline._detector_findings(ledger, good)
    check("the detector's own wear findings fill in what the model left out, as warnings "
          "with the earlier figure to compare", made and all(f["severity"] == "warn" for f in made)
          and all("compare_to" in f["metric"] for f in made)
          and not any("TalonFX 41" in f["claim"] and "load" in f["id"] for f in made),
          str([f["id"] for f in made]))
    opts = checks.chart_options(good, ledger)
    trend = [o for o in opts if o["source"]["signal"] == "wear:load_per_speed"]
    check("a wear finding offers its motor's trend as a chart",
          trend and trend[0]["kind"] == "sessions" and trend[0]["source"]["can_id"] == 41)
    data = pipeline._chart_data(LocalToolbox(), {"kind": "sessions", "source": trend[0]["source"]})
    check("…and code fetches that trend, log by log",
          [round(x["max"], 2) for x in data["sessions"]] == [0.3] * 5 + [0.45] * 3, str(data)[:300])
    board = {"vitals": [], "faults": [], "headline": {}}
    pipeline._wear_cards(board, good, ledger)
    pipeline.assign_status(board, good)
    card = next((c for c in board["vitals"] if c["finding"] == "talonfx_41_wear"), None)
    check("a wear finding the designer left off the board gets its card, amber",
          card is not None and card["status"] == "warn" and card["value"] == "0.45"
          and "0.3" in card.get("detail", ""), str(board["vitals"]))
    board = {"vitals": [], "faults": [], "charts": [], "headline": {}}
    pipeline._wear_cards(board, good, ledger, LocalToolbox())
    drawn = [c for c in board["charts"] if c["finding"] == "talonfx_41_wear"]
    check("…and, with room on the board, its trend chart, data fetched by code",
          drawn and drawn[0]["kind"] == "sessions" and len(drawn[0]["data"]["sessions"]) == 8
          and not checks.check_charts(board, checks.chart_options(good, ledger), [logs[7]]),
          str(board["charts"])[:300])
    prompt = pipeline._must_cover(ledger)
    check("the analyst is told, where it writes, to cover each wear warning",
          "wear warning" in prompt and "TalonFX 41" in prompt)
    primer = ask._primer(LocalToolbox(), checks.Ledger(), {"uid": logs[7], "started_at":
                         "2026-10-08 12:00", "duration_s": 120, "match_key": None})
    check("Ask the Pit reads the wear warnings up front", '"kind": "wear"' in primer)


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
        check("it lists the fifteen tools and the two analysis ones",
              len(names) == 17 and "anomalies" in names and "wear" in names
              and "analysis_scoreboard" in names
              and "match_context" in names and "match_phases" in names, ", ".join(names))
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
    phases_section()
    anomaly_section()
    health_section()
    sweep_section()
    pipeline_section(uid)
    app_section(uid)
    ask_section(uid)
    wear_section(uid)
    playbook_section(uid)
    csa_section(uid)
    mcp_section(uid)
    if opts.model or opts.engine == "llama":
        from app.ai.pipeline import DEFAULT_QUESTION
        live_section(uid, opts.model or "", opts.designer, opts.question or DEFAULT_QUESTION,
                     opts.engine)

    print(f"\n{'All checks passed.' if not _failures else f'{len(_failures)} failed.'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
