"""
What's getting worse: each motor's trend across this robot's own logs.

`anomaly.py` asks "is this log abnormal?". This asks "is this device wearing
out?": a motor that needs more current for the same speed every match (a
bearing, a belt, a mechanism starting to bind), one that heats up more each
time, one whose faults keep coming more often. Caught early, it's a
maintenance item between matches instead of a failure in one (Brayden,
2026-10-18: wear warnings through the whole analysis).

Three measures, per motor, per log, from data every hoot already carries,
over the seconds the motor actually ran (no enable state needed: many hoots
don't record it):

* **load_per_speed**: stator current per rotor rotation-per-second, the
  median over steady running (|RotorVelocity| ≥ LOAD_MIN_RPS, within
  STEADY of its own average that second). Friction and binding raise it.
* **heat_vs_peers**: DeviceTemp's rise across the log (max − min) minus the
  median rise of the other motors in the same log, in logs where it ran at
  least MIN_ACTIVE_S. Binding, a rubbing part, poor cooling.
* **faults_vs_peers**: live (non-sticky) fault occurrences in the log, minus
  the other motors' median (power-on noise and bitfield summaries left out).
  A connector or sensor going bad.

**Why against the other motors** (the team's 63 logs, 2026-10-18): a raw
heat rise or fault count moves with the session, on every motor at once (one
hard session put every motor at 11–14 °C of rise and every drive motor at
70–84 fault events). That's the match, or a bus-wide cause, not wear; a motor
wearing out drifts away from the others. Needs MIN_PEERS other motors
measured in the log.

**The judgement**, per device and measure, over the same bus's comparable
logs up to and including this one (`anomaly.bus_of`, newest HISTORY): at
least MIN_LOGS values; the RECENT newest against the ones before. A warning
when the recent median is beyond the baseline median by both the measure's
relative and absolute margin, at least RECENT_ABOVE of the recent logs sit
above the baseline's upper quartile, and **this log** is still above the
baseline median (a trend that already recovered isn't news). Thresholds are
named constants; retune with `tools/anomaly_report.py --data`, never per log.

Pure stdlib plus the `db` interface (`fetchall` / `fetchone`); portable SQL,
because home runs the detector over SQL Server (R14, R17).
"""

from __future__ import annotations

import statistics
from typing import Any

from app.robot.anomaly import HISTORY, _label, _r, _series_ids, bus_of

MIN_LOGS = 6                 # 3 to judge against + RECENT
RECENT = 3                   # the newest logs that make "now"
RECENT_ABOVE = 2             # of RECENT, at least this many above the baseline's upper quartile
LOAD_MIN_RPS = 5.0           # rotor speed that counts as running
STEADY = 0.25                # that second's speed within this share of its average
MIN_STEADY_S = 10            # steady seconds a log needs for load_per_speed
MIN_ACTIVE_S = 30            # seconds running (|speed| ≥ 1 rps) a log needs for heat/faults
MIN_PEERS = 3                # other motors measured in the same log, for the *_vs_peers measures

# measure: (relative margin, absolute margin, unit, what it means, what to check).
# A relative margin of 0 is ignored: a *_vs_peers value is a difference, and
# can be near zero or negative.
MEASURES: dict[str, tuple[float, float, str, str, str]] = {
    "load_per_speed": (0.20, 0.03, "A/rps",
                       "needs more current for the same speed",
                       "friction or binding: check bearings, belts, gears and alignment, and "
                       "for something rubbing"),
    "heat_vs_peers": (0.0, 4.0, "°C",
                      "heats up more than the other motors, more each log",
                      "binding, a rubbing part or poor cooling: check the mechanism turns freely "
                      "and the motor's airflow"),
    "faults_vs_peers": (0.0, 8.0, "events",
                        "faults more often than the other motors, more each log",
                        "a connector, CAN wire or sensor starting to fail: reseat and inspect "
                        "its wiring"),
}
# Left out of fault_events: power-on noise, and current limits engaging, which
# is them working as configured (the fault guide; the crew rated them not
# useful). On the team's logs current limits were most of every count.
_QUIET = ("BridgeBrownout", "Undervoltage", "BootDuringEnable", "StatorCurrLimit",
          "SupplyCurrLimit")
GROUP = 3                    # motors flagging one measure at once: one warning, for the bus

_CACHE: dict[tuple, dict] = {}      # (session uid, device_type, can_id): a log never changes


def measures(db, session_id: int, uid: str, device: dict) -> dict[str, float | None]:
    """This device's raw measures in one log (None where the log can't say):
    load_per_speed, heat_rise, fault_events. `find()` turns the last two into
    differences from the other motors."""
    key = (uid, device["device_type"], device["can_id"])
    if key in _CACHE:
        return _CACHE[key]
    ids = _series_ids(db, session_id, device["id"], ["RotorVelocity", "StatorCurrent",
                                                      "DeviceTemp"])
    out: dict[str, float | None] = {"load_per_speed": None, "heat_rise": None,
                                    "fault_events": None}   # raw: find() compares with peers
    vel, cur = ids.get("RotorVelocity"), ids.get("StatorCurrent")
    active = 0
    if vel is not None:
        active = db.fetchone(
            """SELECT COUNT(*) AS n FROM samples.sample_1s
               WHERE series_id = ? AND (v_max >= 1 OR v_min <= -1)""", (vel,))["n"] or 0
    if vel is not None and cur is not None:
        ratios = []
        for r in db.fetchall(
                """SELECT v.v_avg AS w, v.v_min AS lo, v.v_max AS hi, i.v_avg AS amps
                   FROM samples.sample_1s v JOIN samples.sample_1s i
                     ON i.series_id = ? AND i.t_s = v.t_s
                   WHERE v.series_id = ?""", (cur, vel)):
            w, amps = r["w"], r["amps"]
            if w is None or amps is None or abs(w) < LOAD_MIN_RPS:
                continue
            if r["hi"] is not None and r["lo"] is not None and r["hi"] - r["lo"] > STEADY * abs(w):
                continue
            ratios.append(abs(amps) / abs(w))
        if len(ratios) >= MIN_STEADY_S:
            out["load_per_speed"] = statistics.median(ratios)
    if active >= MIN_ACTIVE_S:
        temp = ids.get("DeviceTemp")
        if temp is not None:
            r = db.fetchone("SELECT v_min, v_max FROM series WHERE id = ?", (temp,))
            if r is not None and r["v_min"] is not None and r["v_max"] is not None:
                out["heat_rise"] = r["v_max"] - r["v_min"]
        else:
            const = db.fetchone(
                """SELECT sc.v FROM session_constant sc JOIN signal g ON g.id = sc.signal_id
                   WHERE sc.session_id = ? AND sc.device_id = ? AND g.name = 'DeviceTemp'""",
                (session_id, device["id"]))
            out["heat_rise"] = 0.0 if const is not None else None
    if active >= MIN_ACTIVE_S:
        n = 0
        for r in db.fetchall(
                """SELECT g.name FROM fault_event f JOIN signal g ON g.id = f.signal_id
                   WHERE f.session_id = ? AND f.device_id = ? AND f.sticky = 0""",
                (session_id, device["id"])):
            name = r["name"].replace("Fault_", "")
            if not name.endswith("Field") and name not in _QUIET:
                n += 1
        out["fault_events"] = float(n)
    _CACHE[key] = out
    return out


def judge(values: list[float], measure: str) -> dict | None:
    """The rule on one device's chronological values (this log last)."""
    if len(values) < MIN_LOGS:
        return None
    rel, absolute = MEASURES[measure][:2]
    base, recent = values[:-RECENT], values[-RECENT:]
    b_med, r_med = statistics.median(base), statistics.median(recent)
    q3 = statistics.quantiles(base, n=4)[2] if len(base) >= 2 else base[0]
    if r_med - b_med < absolute or (rel and r_med < b_med * (1 + rel)):
        return None
    if sum(v > q3 for v in recent) < RECENT_ABOVE or values[-1] <= b_med:
        return None
    return {"recent": r_med, "baseline": b_med, "low": min(base), "high": max(base),
            "change_pct": round(100 * (r_med - b_med) / b_med) if rel and b_med > 0 else None}


def history(db, session_uid: str) -> tuple[dict | None, list[dict]]:
    """(this session's row, the bus's sessions up to and including it, oldest first)."""
    me = db.fetchone("SELECT id, uid, source_kind, source_name, device_serial, started_at "
                     "FROM log_session WHERE uid = ?", (session_uid,))
    if me is None:
        return None, []
    bus = bus_of(me)
    rows = [r for r in db.fetchall(
        """SELECT id, uid, source_kind, source_name, device_serial, started_at
           FROM log_session
           WHERE id = ? OR started_at IS NULL OR ? IS NULL OR started_at <= ?
           ORDER BY started_at DESC, id DESC""", (me["id"], me["started_at"], me["started_at"]))
        if bus_of(r) == bus]
    mine = [r for r in rows if r["id"] == me["id"]]
    others = [r for r in rows if r["id"] != me["id"]][:HISTORY - 1]
    return me, list(reversed(others)) + mine


def _per_log(db, log: dict) -> dict[tuple, dict[str, float | None]]:
    """Every motor's measures in one log, the peer comparisons included."""
    devs = db.fetchall(
        """SELECT DISTINCT d.id, d.device_type, d.can_id, d.label, d.subsystem
           FROM device d JOIN series se ON se.device_id = d.id
           WHERE se.session_id = ? AND d.device_type IN ('TalonFX', 'TalonFXS')""", (log["id"],))
    raw = {(d["device_type"], d["can_id"]): measures(db, log["id"], log["uid"], d) for d in devs}
    out: dict[tuple, dict[str, float | None]] = {}
    for key, m in raw.items():
        row: dict[str, float | None] = {"load_per_speed": m["load_per_speed"]}
        for name, src in (("heat_vs_peers", "heat_rise"), ("faults_vs_peers", "fault_events")):
            peers = [o[src] for k, o in raw.items() if k != key and o[src] is not None]
            row[name] = (m[src] - statistics.median(peers)
                         if m[src] is not None and len(peers) >= MIN_PEERS else None)
        out[key] = row
    return out


def _grouped(warnings: list[dict]) -> list[dict]:
    """GROUP or more motors flagging one measure at once share a cause (on the
    team's logs: four motors' remote-sensor faults climbing together, their
    sensors' wiring branch): one warning naming them, for the bus."""
    by: dict[str, list[dict]] = {}
    for w in warnings:
        by.setdefault(w["measure"], []).append(w)
    out: list[dict] = []
    for measure, group in by.items():
        if len(group) < GROUP:
            out.extend(group)
            continue
        worst = max(group, key=lambda w: w["value"] - w["normal"]["median"])
        names = [w["device"] for w in group]
        means = MEASURES[measure][3]
        out.append({**worst, "device": f"{len(group)} motors on this bus", "device_type": None,
                    "can_id": None, "subsystem": None, "devices": names,
                    "detail": (f"{len(group)} motors {means.replace('heats', 'heat').replace('faults', 'fault').replace('needs', 'need')} "
                               f"at once ({', '.join(names)}; worst {worst['device']}: "
                               f"{worst['value']} {worst['unit']}"
                               + (" above the other motors" if measure.endswith("_vs_peers") else "")
                               + f" against {worst['normal']['median']} before). "
                               "A shared cause is likelier than each wearing out: their wiring "
                               "branch, power, or a code or configuration change.")})
    return out


def find(db, session_uid: str, device_type: str | None = None,
         can_id: int | None = None) -> dict[str, Any]:
    """Every motor's trends as of this log, and the warnings among them."""
    me, logs = history(db, session_uid)
    if me is None:
        return {"error": f"no session {session_uid!r}"}
    devices = [d for d in db.fetchall(
        """SELECT DISTINCT d.id, d.device_type, d.can_id, d.label, d.subsystem
           FROM device d JOIN series se ON se.device_id = d.id
           WHERE se.session_id = ? AND d.device_type IN ('TalonFX', 'TalonFXS')""", (me["id"],))
        if (device_type is None or d["device_type"] == device_type)
        and (can_id is None or d["can_id"] == can_id)]
    per_log = [(log, _per_log(db, log)) for log in logs]
    trends, warnings = [], []
    for d in sorted(devices, key=lambda d: (d["device_type"], d["can_id"])):
        key = (d["device_type"], d["can_id"])
        for m in MEASURES:
            points = [(log, got[key][m]) for log, got in per_log
                      if key in got and got[key][m] is not None]
            # Only a trend that reaches this log says anything about it.
            if not points or points[-1][0]["id"] != me["id"]:
                continue
            values = [v for _, v in points]
            trend = [{"session_uid": log["uid"], "started_at": log["started_at"], "value": _r(v)}
                     for log, v in points[-10:]]
            row = {"device": _label(d), "device_type": d["device_type"], "can_id": d["can_id"],
                   "subsystem": d["subsystem"], "measure": m, "unit": MEASURES[m][2],
                   "logs": len(values), "trend": trend}
            verdict = judge(values, m)
            if verdict:
                _rel, _abs, unit, means, check = MEASURES[m]
                pct = verdict["change_pct"]
                row["warning"] = True
                warnings.append({
                    "kind": "wear", "severity": "warn", "device": _label(d),
                    "device_type": d["device_type"], "can_id": d["can_id"],
                    "subsystem": d["subsystem"], "signal": f"wear:{m}", "measure": m,
                    "value": _r(verdict["recent"]), "unit": unit, "change_pct": pct,
                    "normal": {"median": _r(verdict["baseline"]), "low": _r(verdict["low"]),
                               "high": _r(verdict["high"]), "logs": len(values) - RECENT},
                    "trend": trend,
                    "detail": (f"{means}: {_r(verdict['recent'])} {unit}"
                               + (" above the other motors" if m.endswith("_vs_peers") else "")
                               + f" over its last {RECENT} logs against "
                               f"{_r(verdict['baseline'])} in the "
                               f"{len(values) - RECENT} before"
                               + (f" (+{pct}%)" if pct is not None else "")
                               + f". Likely {check}.")})
            trends.append(row)
    return {"args": {"session_uid": session_uid, "device_type": device_type, "can_id": can_id},
            "bus": bus_of(me), "logs": len(logs), "warnings": _grouped(warnings),
            "trends": trends,
            "note": None if len(logs) >= MIN_LOGS else
            f"only {len(logs)} log(s) on this bus so far: a trend needs {MIN_LOGS}"}
