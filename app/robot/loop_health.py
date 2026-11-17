"""
The robot code's own loop: overruns, stalls, what made them, and whether the
logger kept up.

Brayden, 2026-11-03: "we were getting the FMS watchdog alert coming through
because we were logging too much data. That needs to be shown as a finding."
The analysis saw only the *average* loop time and warned when it passed
20 ms; a robot overrunning one cycle in fifty averages well under that, so
it never did. Measured while enabled on the team's 25 enabled AdvantageKit
logs: every one overran; practice logs had up to 16% of cycles over 20 ms
and 57 over 100 ms (a 2.1 s stall); OMB matches 1 or 2 over 100 ms each.

From the AdvantageKit log (pseudo-device `Robot`):

* `RealOutputs/LoggedRobot/FullCycleMS` per enabled second (`sample_1s`
  v_max): seconds with an **overrun** (a cycle past the 20 ms period) and
  with a **stall** (a cycle ≥ 100 ms, WPILib's motor-safety timeout: long
  enough for motor output to be cut and the Driver Station to report its
  watchdog). The first second of each enabled stretch is left out: a mode
  change makes one long cycle that isn't the code's fault.
* `RealOutputs/Console`: WPILib's own words. Overrun messages counted, and
  the **Tracer's epochs** read for which parts of the code were slow
  (`Shooter.periodic(): 0.0107s`); the wrappers (`robotPeriodic`, …) are
  left out so the subsystems show. `SmartDashboard.updateValues` is the cost
  of publishing dashboard data. Console text is capped per log at import
  (`wpilog.MAX_ENUM_LABELS`), so these counts are "at least".
* `RealOutputs/Logger/QueuedCycles`: AdvantageKit's write queue; when it
  backs up, the logger is behind the robot.

Severity: **fault** with ≥ `STALL_FAULT` stall seconds (motors could have
been cut repeatedly); **warn** with any stall, or overruns in ≥
`OVERRUN_WARN` of enabled seconds with a cycle past `SLOW_MS` (3 short
overruns in 26 s, longest 26.7 ms, was noise on a real log). Both are `must_cover`: the board says so.
Earlier enabled logs of the same bus give the usual figure, for context.

Plain SQL over `series`, `signal`, `signal_enum`, `samples.sample(_1s)`; no Qt.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

FULL_CYCLE = "RealOutputs/LoggedRobot/FullCycleMS"
QUEUED = "RealOutputs/Logger/QueuedCycles"
CONSOLE = "RealOutputs/Console"
PERIOD_MS = 20.0
STALL_MS = 100.0           # WPILib MotorSafety's default expiration
STALL_FAULT = 5            # stall seconds for a fault
OVERRUN_WARN = 0.25        # share of enabled seconds with an overrun, for a warning…
SLOW_MS = 40.0             # …with a cycle at least this long (twice the period)
MIN_ENABLED_S = 10
SLOW_EPOCH_S = 0.005       # an epoch this slow in an overrun report counts against it
_EPOCH = re.compile(r"([A-Za-z_][\w.$]*(?:\([^)]*\))?)\(\): (\d+\.\d+)s")
_WRAPPERS = ("robotPeriodic", "teleopPeriodic", "autonomousPeriodic", "disabledPeriodic",
             "testPeriodic", "simulationPeriodic", "LiveWindow.updateValues",
             "Shuffleboard.update", "buttons.run")


def _series(db, session_id: int, name: str) -> int | None:
    r = db.fetchone("""SELECT se.id FROM series se JOIN signal g ON g.id = se.signal_id
                       WHERE se.session_id = ? AND g.name = ?""", (session_id, name))
    return r["id"] if r else None


def _inside(t: float, windows) -> bool:
    return any(a + 1 <= t <= b for a, b in windows)


def measure(db, session_id: int, windows) -> dict | None:
    """One log's loop while enabled, or None if it doesn't record it."""
    cyc = _series(db, session_id, FULL_CYCLE)
    if cyc is None:
        return None
    secs = [(r["t_s"], r["v_max"]) for r in db.fetchall(
        "SELECT t_s, v_max FROM samples.sample_1s WHERE series_id = ?", (cyc,))
        if r["v_max"] is not None and _inside(r["t_s"], windows)]
    if len(secs) < MIN_ENABLED_S:
        return None
    worst = max(v for _t, v in secs)
    out = {"enabled_s": len(secs),
           "overrun_s": sum(v > PERIOD_MS for _t, v in secs),
           "stall_s": sum(v >= STALL_MS for _t, v in secs),
           "worst_ms": round(worst, 4), "queued_max": None,
           "overrun_messages": None, "slow_parts": []}
    q = _series(db, session_id, QUEUED)
    if q is not None:
        vals = [r["v_max"] for r in db.fetchall(
            "SELECT t_s, v_max FROM samples.sample_1s WHERE series_id = ?", (q,))
            if r["v_max"] is not None and _inside(r["t_s"], windows)]
        out["queued_max"] = round(max(vals), 4) if vals else None
    try:
        out.update(_console(db, session_id))
    except Exception:               # a database without signal_enum (home's may differ)
        pass
    return out


def _console(db, session_id: int) -> dict:
    con = _series(db, session_id, CONSOLE)
    if con is None:
        return {}
    sig = db.fetchone("SELECT signal_id FROM series WHERE id = ?", (con,))["signal_id"]
    labels = {r["code"]: r["label"] for r in db.fetchall(
        "SELECT code, label FROM signal_enum WHERE signal_id = ?", (sig,))}
    overruns, slow = 0, Counter()
    for r in db.fetchall("SELECT v FROM samples.sample WHERE series_id = ?", (con,)):
        text = labels.get(int(r["v"])) if r["v"] is not None else None
        if not text:
            continue
        overruns += text.count("overrun")
        for name, sec in _EPOCH.findall(text):
            if float(sec) >= SLOW_EPOCH_S and not name.startswith(_WRAPPERS):
                slow[name] += 1
    return {"overrun_messages": overruns,
            "slow_parts": [{"part": n, "times": c} for n, c in slow.most_common(4)]}


def find(db, session_id: int, windows, hist: list[tuple[int, list]]) -> list[dict]:
    """The loop item for this log (anomaly.py's shape), if there's one to say.
    `hist`: (session id, its enabled windows) of earlier logs of this bus."""
    m = measure(db, session_id, windows)
    if m is None:
        return []
    share = m["overrun_s"] / m["enabled_s"]
    if m["stall_s"] >= STALL_FAULT:
        severity = "fault"
    elif m["stall_s"] >= 1 or (share >= OVERRUN_WARN and m["worst_ms"] >= SLOW_MS):
        severity = "warn"
    else:
        return []
    past = [x["stall_s"] for x in (measure(db, sid, w) for sid, w in hist if w) if x]
    parts = ", ".join(f"{p['part']} ({p['times']}×)" for p in m["slow_parts"])
    detail = (f"the robot code's loop overran its {PERIOD_MS:g} ms period in "
              f"{m['overrun_s']} of {m['enabled_s']} enabled seconds and stalled past "
              f"{STALL_MS:g} ms (motor safety's timeout: motors can be cut and the Driver "
              f"Station reports its watchdog) in {m['stall_s']}; longest {m['worst_ms']:g} ms")
    if parts:
        detail += f". The console's Tracer named the slow parts: {parts}"
    if any(p["part"].startswith("SmartDashboard") for p in m["slow_parts"]):
        detail += " (SmartDashboard.updateValues is publishing dashboard data)"
    if m["queued_max"]:
        detail += f". AdvantageKit's log queue backed up to {m['queued_max']:g} cycles"
    if past:
        detail += (f". Earlier enabled logs: a median of {statistics.median(past):g} stall "
                   f"second(s) ({len(past)} logs)")
    detail += (". Check what the slow parts do every loop: logging or publishing a lot of "
               "data, reading the PDH or CAN devices, vision processing")
    item = {"kind": "loop_overrun", "severity": severity, "device": "Robot loop",
            "devices": ["loop"], "device_type": "Robot", "can_id": -1, "subsystem": None,
            "signal": "seconds with a loop stall over 100 ms" if m["stall_s"]
                      else "seconds with a loop overrun",
            "value": m["stall_s"] if m["stall_s"] else m["overrun_s"], "unit": "s",
            "enabled_s": m["enabled_s"], "overrun_s": m["overrun_s"],
            "stall_s": m["stall_s"], "worst_ms": m["worst_ms"],
            "queued_max": m["queued_max"], "overrun_messages": m["overrun_messages"],
            "slow_parts": m["slow_parts"], "must_cover": True, "detail": detail}
    if past:
        item["normal"] = {"median": statistics.median(past), "low": min(past),
                          "high": max(past), "logs": len(past)}
    return [item]
