"""
The general sweep: every health signal on every device, against its own past.

Brayden, 2026-11-03: "there's a lot of questions of things that are going
wrong but not being reported." The named detectors (anomaly.py's five
ranges, partners, wear, the loop, events) answer questions someone thought
to ask. This one asks nobody's question: for **every** numeric signal on
every device, is this log's extreme beyond anything this robot recorded
before? A sensor reading far outside its life so far, a current on a
mechanism nobody watches, a rail voltage, a CAN error count: whatever it is,
it shows up here and the analyst gets to explain it.

The whole cost is in not crying wolf, so it's strict:
* only **health** signals: commands, positions, setpoints, PID internals,
  joystick and match bookkeeping, versions and identifiers are left out
  (`_SKIP`); they move whenever the driver does;
* judged against **comparable** logs only (enabled against enabled), at
  least `MIN_HISTORY` of them, the per-session extreme (`series.v_min` /
  `v_max`, so no samples are read);
* beyond the history's own edge by `max(MAD_K × MAD × 1.4826, EDGE_SHARE × the
  range, the signal's floor)`, both directions only where that means
  something (`_LOW`: voltages);
* a signal that never moved in its history (a mechanism newly logged reads
  0 in every earlier log) has no normal to judge by, so it's skipped;
* anything a named detector already covers is left to it (`_COVERED`), and
  at most `MAX_ITEMS` per log, furthest out first.

Items are anomaly.py's shape, kind `unusual`, severity `warn`, never
`must_cover`: the analyst decides whether one matters. Tuned on the team's
104 logs with `tools/anomaly_report.py`.
"""

from __future__ import annotations

import re
import statistics

MIN_HISTORY = 8
MAD_K = 6.0
EDGE_SHARE = 0.5
MAX_ITEMS = 6
_SKIP = re.compile(
    r"Position|Setpoint|Reference|Target|PID|ClosedLoop|Goal|Error$|Joystick|Axis|Button|POV|"
    r"MatchTime|MatchNumber|ReplayNumber|Timestamp|Time(?:Micros|Valid)|Version|Serial|"
    r"FPGA|TeamNumber|AllianceStation|Energy|Count$|Counts$|Odometry|Pose|Trajectory|Path|"
    r"Auto|Velocity|Acceleration|DutyCycle|MotorVoltage|ControlMode|Mode$|Slot|Enabled|"
    r"Enable$|Polarity|Connected|Licensed|MotorK|StallCurrent|Limit$|Rotation|Angle|Heading|"
    r"Yaw|Pitch|Roll|Gyro|Distance|Speed|Vision|Camera|Tag|Quest|Odom|Swerve.*(?:State|Module)|"
    r"/Setpoints|Desired|Commanded|Request|Input$|Output$|Active$|RSL|Button|Comments|"
    r"FMS|MatchType|DSAttached|EmergencyStop|Field$|GameSpecific|EventName", re.I)
# Signals whose low end is the news (a supply sagging), not only the high.
_LOW = re.compile(r"Voltage|Volts", re.I)
# Named detectors own these (anomaly.RANGES, loop_health, events).
_COVERED = {"DeviceTemp", "ProcessorTemp", "StatorCurrent", "SupplyCurrent", "SupplyVoltage",
            "TorqueCurrent",
            "RealOutputs/LoggedRobot/FullCycleMS", "SystemStats/BatteryVoltage",
            "DS/BatteryVoltage", "DS/TripTimeMS", "DS/PacketLossPct"}
FLOOR = 1e-3
CURRENT_FLOOR_A = 2.0
IDLE_SHARE = 0.05         # history all under this share of today's value: first use, not a fault


def _extremes(db, session_ids: list[int]) -> dict[tuple, dict[int, tuple]]:
    """(device_type, can_id, signal) → {session: (v_min, v_max)}."""
    if not session_ids:
        return {}
    marks = ",".join("?" * len(session_ids))
    out: dict[tuple, dict[int, tuple]] = {}
    for r in db.fetchall(
            f"""SELECT se.session_id, d.device_type, d.can_id, d.label, g.name, se.v_min, se.v_max
                FROM series se JOIN signal g ON g.id = se.signal_id
                JOIN device d ON d.id = se.device_id
                WHERE se.session_id IN ({marks}) AND g.value_kind = 'num'
                  AND se.v_min IS NOT NULL AND se.v_max IS NOT NULL""", tuple(session_ids)):
        out.setdefault((r["device_type"], r["can_id"], r["name"], r["label"]), {})[
            r["session_id"]] = (r["v_min"], r["v_max"])
    return out


def find(db, session_id: int, like_ids: list[int]) -> list[dict]:
    """`like_ids`: earlier comparable logs of the same bus (anomaly.py's `like`)."""
    if len(like_ids) < MIN_HISTORY:
        return []
    now = _extremes(db, [session_id])
    past = _extremes(db, like_ids)
    items = []
    for key, mine in now.items():
        dtype, can, name, label = key
        short = name.rsplit("/", 1)[-1]
        if name in _COVERED or short in _COVERED or _SKIP.search(name):
            continue
        hist = past.get(key) or {}
        if len(hist) < MIN_HISTORY:
            continue
        lo_now, hi_now = mine[session_id]
        for side in ("max", "min"):
            if side == "min" and not _LOW.search(name):
                continue
            vals = [v[1] if side == "max" else v[0] for v in hist.values()]
            value = hi_now if side == "max" else lo_now
            if "Current" in name and abs(value) < CURRENT_FLOOR_A:
                continue          # under a couple of amps is sensor noise, not a fault
            if max(vals) - min(vals) <= FLOOR or \
                    max(abs(v) for v in vals) < IDLE_SHARE * abs(value):
                continue          # idle before (newly logged or newly used): no normal yet
            med = statistics.median(vals)
            mad = statistics.median(abs(v - med) for v in vals) * 1.4826
            edge = max(vals) if side == "max" else min(vals)
            margin = max(MAD_K * mad, EDGE_SHARE * (max(vals) - min(vals)), FLOOR,
                         0.05 * abs(med))
            value = hi_now if side == "max" else lo_now
            beyond = value - edge if side == "max" else edge - value
            if beyond <= margin:
                continue
            device = label or (f"{dtype} {can}" if dtype != "Robot" else "robot")
            items.append({
                "kind": "unusual", "severity": "warn", "device": device,
                "device_type": dtype, "can_id": can, "subsystem": None, "signal": name,
                "value": round(value, 4),
                "normal": {"median": round(med, 4), "low": round(min(vals), 4),
                           "high": round(max(vals), 4), "logs": len(vals)},
                "detail": f"{name} {'peaked at' if side == 'max' else 'fell to'} {value:.4g}, "
                          f"beyond anything in {len(vals)} comparable earlier logs "
                          f"({min(vals):.4g} to {max(vals):.4g}): no detector watches it; "
                          "worth a look if it isn't explained",
                "_beyond": beyond / max(margin, FLOOR)})
    items.sort(key=lambda a: -a["_beyond"])
    for a in items:
        a.pop("_beyond")
    return items[:MAX_ITEMS]
