"""
Incidents: the moments a match went wrong, with everything every log saw then.

A CSA's second step (`events.py` is the first): line the logs of one
recording up on one clock and look at everything at the moment it broke. A
recording is the AdvantageKit log, the hoot of every CAN bus and the Driver
Station's log, written at once by three different devices.

* **One clock.** Each log starts at its `started_at` (the file name's time,
  or the DS log's own header), to the second. A file name's second is the
  only alignment there is, so it's refined where it can be: when two logs
  both record the robot being enabled, the first enable is the same instant
  in both, and the later log is shifted to match (within `ALIGN_MAX_S`).
* **Incidents.** Events (every log's) sorted on that clock; a new incident
  starts when nothing happened for `GAP_S`. Each has a time (`at`, from the
  recording's start), the phase it was in, its severity (the worst event's),
  the kinds and devices involved, and the events themselves, repeats
  counted. Incidents of only `info` events are left out.
* **History** (`history()`): which of this recording's issues happened in
  the robot's earlier recordings too (the last `HISTORY`), and how many in a
  row. A CSA's first question: new today, or every match this week? It
  separates a cause that travels with the robot (wiring, code, a mechanism)
  from one that doesn't (one battery, one field).
* **The moment** (`moment()`): for any time, what every log of the
  recording read around it: the battery (the roboRIO's, the DS's, the lowest
  controller supply), total current, CAN utilization and errors, loop time,
  comms and packet loss, the three hardest-working motors. What a CSA scrolls
  to in AdvantageScope, as numbers.

No Qt; plain SQL; home can run it as it runs `anomaly.py`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from app.robot import events as ev

GAP_S = 3.0
ALIGN_MAX_S = 30.0
OVERLAP_S = 60.0          # a log starting this close to another is the same recording
ORDER = {"fault": 0, "warn": 1, "info": 2}
MAX_EVENTS = 12           # per incident, for the model (the rest are counted)
HISTORY = 10              # earlier recordings `history()` looks back over


def _epoch(started: str | None) -> float | None:
    if not started:
        return None
    try:
        return datetime.fromisoformat(str(started)).timestamp()
    except ValueError:
        return None


def recording(db, uid: str) -> list[dict]:
    """`uid` and every log recorded with it: started within `OVERLAP_S`, or a
    Driver Station log whose span covers it (a DS log runs the whole time
    the DS is connected, starting minutes before the robot's logs)."""
    me = db.fetchone("SELECT id, uid, source_name, source_kind, started_at, duration_s "
                     "FROM log_session WHERE uid = ?", (uid,))
    if me is None:
        return []
    t0 = _epoch(me["started_at"])
    if t0 is None:
        return [dict(me)]
    t1 = t0 + float(me["duration_s"] or 0)
    out = [dict(me)]
    for r in db.fetchall("""SELECT id, uid, source_name, source_kind, started_at, duration_s
                            FROM log_session WHERE uid IS NOT NULL AND uid <> ?
                              AND started_at IS NOT NULL""", (uid,)):
        a = _epoch(r["started_at"])
        if a is None:
            continue
        b = a + float(r["duration_s"] or 0)
        close = abs(a - t0) <= OVERLAP_S
        spans = (r["source_kind"] == "dslog" or me["source_kind"] == "dslog") \
            and a <= t1 and b >= t0
        if close or spans:
            out.append(dict(r))
    out.sort(key=lambda r: (_epoch(r["started_at"]) or 0, r["id"]))
    return out


def _windows(phases) -> list[tuple[float, float]]:
    return [(p["start_s"], p["end_s"]) for p in (phases or {}).get("phases") or []
            if p.get("phase") in ("auto", "teleop", "test")]


def clock(db, logs: list[dict], phases_of: Callable) -> dict[int, float]:
    """Each log's start on one clock (Unix seconds), refined by first enables."""
    start = {r["id"]: _epoch(r["started_at"]) or 0.0 for r in logs}
    first_on = {}
    for r in logs:
        w = _windows(phases_of(r["uid"]))
        if w:
            first_on[r["id"]] = start[r["id"]] + w[0][0]
    if len(first_on) >= 2:
        # The reference: the log the enable is most trustworthy in (the DS's
        # own log commands it; then the AdvantageKit log; then a hoot).
        rank = {"dslog": 0, "wpilog": 1}
        ref = min(first_on, key=lambda i: (rank.get(next(r["source_kind"] for r in logs
                                                        if r["id"] == i), 2), i))
        for i, t in first_on.items():
            shift = first_on[ref] - t
            if i != ref and abs(shift) <= ALIGN_MAX_S:
                start[i] += shift
    return start


def build(db, uid: str, phases_of: Callable) -> dict[str, Any]:
    logs = recording(db, uid)
    if not logs:
        return {"error": f"no session {uid!r}"}
    starts = clock(db, logs, phases_of)
    t0 = min(starts.values())
    every: list[dict] = []
    ref_phases = None
    for r in logs:
        ph = phases_of(r["uid"])
        if ref_phases is None and _windows(ph):
            ref_phases = (r["id"], ph)
        for e in ev.find(db, r["id"], _windows(ph)):
            e = dict(e, log=r["source_name"], log_uid=r["uid"], log_kind=r["source_kind"],
                     at_s=round(starts[r["id"]] - t0 + e["t_s"], 3))
            if "end_s" in e:
                e["end_at_s"] = round(starts[r["id"]] - t0 + e["end_s"], 3)
            every.append(e)
    every.sort(key=lambda e: e["at_s"])

    def phase_at(t: float) -> str:
        if ref_phases is None:
            return "unknown"
        sid, ph = ref_phases
        local = t - (starts[sid] - t0)
        for p in ph.get("phases") or []:
            if p["start_s"] <= local <= p["end_s"]:
                return p["phase"]
        return "disabled"

    groups: list[list[dict]] = []
    for e in every:
        end = max(x.get("end_at_s", x["at_s"]) for x in groups[-1]) if groups else None
        if groups and e["at_s"] - end <= GAP_S:
            groups[-1].append(e)
        else:
            groups.append([e])
    incidents = []
    for g in groups:
        sev = min((e["severity"] for e in g), key=lambda s: ORDER.get(s, 3))
        if sev == "info":
            continue
        a = g[0]["at_s"]
        b = max(e.get("end_at_s", e["at_s"]) for e in g)
        incidents.append({
            "id": f"i{len(incidents) + 1}", "start_s": a, "end_s": round(b, 3),
            "at": f"{int(a // 60)}:{int(a % 60):02d}", "phase": phase_at(a),
            "severity": sev,
            "kinds": sorted({e["kind"] for e in g}),
            "devices": sorted({e["device"] for e in g if e["device"] != "robot"}),
            "logs": sorted({e["log"] for e in g}),
            "n_events": sum(e.get("count", 1) for e in g),
            "events": [{k: e[k] for k in ("at_s", "kind", "device", "value", "unit", "detail",
                                          "severity", "log", "count", "fault") if k in e}
                       for e in sorted(g, key=lambda e: (ORDER.get(e["severity"], 3),
                                                         e["at_s"]))[:MAX_EVENTS]],
        })
    for i in incidents:
        i["primary"] = primary(i)
    incidents.sort(key=lambda i: (ORDER.get(i["severity"], 3), -len(i["kinds"]),
                                  i["phase"] == "disabled", i["start_s"]))
    for n, i in enumerate(incidents, 1):
        i["rank"] = n
    return {"logs": [{"uid": r["uid"], "name": r["source_name"], "kind": r["source_kind"],
                      "offset_s": round(starts[r["id"]] - t0, 3)} for r in logs],
            "incidents": incidents, "issues": issues(incidents), "n_events": len(every),
            "kinds": ev.summary(every),
            # What else went wrong anywhere in the recording: the CSA's
            # `recording_has` tests (console spam behind a loop stall, low
            # memory behind a code restart, minutes apart).
            "problems": sorted({e["kind"] for e in every if e["severity"] != "info"})}


def history(db, uid: str, phases_of: Callable, limit: int = HISTORY) -> dict[str, Any]:
    """
    This robot's earlier recordings (the `limit` before this one, newest
    first, each recording once however many logs it has): `of` how many were
    looked at, and per issue key (`issues()`'s `primary`, warn or fault) `in`
    how many had it, `streak` how many of the newest in a row, `last` when.
    Recordings with no events at all count too: a clean match is evidence.
    """
    me = db.fetchone("SELECT started_at FROM log_session WHERE uid = ?", (uid,))
    if me is None or _epoch(me["started_at"]) is None:
        return {"of": 0, "issues": {}}
    seen = {r["uid"] for r in recording(db, uid)}
    recs: list[dict] = []
    for r in db.fetchall("""SELECT uid, started_at, match_key FROM log_session
                            WHERE uid IS NOT NULL AND started_at IS NOT NULL
                              AND started_at < ? ORDER BY started_at DESC, id DESC""",
                         (me["started_at"],)):
        if r["uid"] in seen:
            continue
        logs = recording(db, r["uid"])
        seen |= {x["uid"] for x in logs}
        got = build(db, r["uid"], phases_of)
        if "error" in got:
            continue
        keys = {i["primary"] for i in got["issues"] if i["severity"] in ("fault", "warn")}
        match = next((x["match_key"] for x in db.fetchall(
            f"SELECT match_key FROM log_session WHERE uid IN ({','.join('?' * len(logs))})",
            tuple(x["uid"] for x in logs)) if x["match_key"]), None)
        recs.append({"at": str(r["started_at"])[:16], "match_key": match, "keys": keys})
        if len(recs) >= limit:
            break
    out: dict[str, dict] = {}
    for n, rec in enumerate(recs):
        for k in rec["keys"]:
            h = out.setdefault(k, {"in": 0, "streak": 0, "last": rec["at"],
                                   "last_match": rec["match_key"]})
            h["in"] += 1
            if h["streak"] == n:          # every newer recording had it too
                h["streak"] += 1
    return {"of": len(recs), "issues": out}


def before(hist: dict, key: str) -> dict | None:
    """One issue's line of `history()`, as the tools show it."""
    if not hist.get("of"):
        return None
    h = hist["issues"].get(key)
    if h is None:
        return {"in": 0, "of": hist["of"], "new": True}
    return {"in": h["in"], "of": hist["of"], "streak": h["streak"], "last": h["last"],
            **({"last_match": h["last_match"]} if h.get("last_match") else {})}


# What an incident is mainly about, most telling first: a reboot explains a
# comms loss, a brownout explains a controller reset, not the other way.
PRIMARY = ("roborio_reboot", "code_restart", "exception", "config_failed", "missing_file",
           "brownout", "comms_lost", "lost_comms_msg", "fms_lost",
           "comms_disable", "radio_lost", "disabled_mid_match", "watchdog", "can_bus_off",
           "rail_fault", "device_reset", "voltage_dip", "device_voltage_dip", "can_tx_full",
           "can_errors", "can_busy", "packet_loss", "high_trip_time", "estop", "motor_stall",
           "can_timeout", "code_error", "no_robot_code", "joystick_unplugged",
           "joystick_missing", "ds_overloaded", "loop_stall",
           "gc_pause", "console_spam", "low_memory", "radio_firmware",
           "device_fault")


def primary(incident: dict) -> str:
    kinds = set(incident["kinds"])
    head = next((k for k in PRIMARY if k in kinds), incident["kinds"][0])
    if head == "device_fault":
        faults = [e.get("fault") for e in incident["events"] if e.get("fault")]
        if faults:
            head += ":" + max(set(faults), key=faults.count)
    return head


FOLD = {"voltage_dip": "brownout", "device_voltage_dip": "brownout",
        "device_fault:BridgeBrownout": "brownout", "device_fault:Undervoltage": "brownout",
        "lost_comms_msg": "comms_lost", "packet_loss": "comms_lost",
        "high_trip_time": "comms_lost", "device_fault:BootDuringEnable": "device_reset",
        "ds_overloaded": "comms_lost"}


def issues(incidents: list[dict]) -> list[dict]:
    """
    Incidents that are the same problem again, as one issue: by their primary
    kind. A CSA says "it browned out 21 times", not 21 diagnoses (the team's
    Aug 12 practice log: 21 brownout incidents, one cause). Each issue keeps
    every occurrence, the union of kinds and devices, and its worst incident.
    """
    out: dict[str, dict] = {}
    for i in sorted(incidents, key=lambda i: i["start_s"]):
        key = i["primary"]
        it = out.setdefault(key, {"id": key, "primary": key, "severity": i["severity"],
                                  "count": 0, "occurrences": [], "at": [], "phases": [],
                                  "kinds": set(), "devices": set(), "worst": i["id"]})
        it["count"] += 1
        figs = it.setdefault("figures", {})
        for e in i["events"]:
            v, unit = e.get("value"), e.get("unit")
            if not isinstance(v, (int, float)) or not unit:
                continue
            pick = min if unit == "V" else max
            figs[unit] = round(pick(figs[unit], v), 4) if unit in figs else round(v, 4)
        it["longest_s"] = round(max(it.get("longest_s", 0.0), i["end_s"] - i["start_s"]), 3)
        it["occurrences"].append(i["id"])
        it["at"].append(i["at"])
        it["phases"].append(i["phase"])
        it["kinds"] |= set(i["kinds"])
        it["devices"] |= set(i["devices"])
        buses = it.setdefault("buses", {})
        kind, _, fault = key.partition(":")
        for e in i["events"]:          # which bus each device showing *this* problem was on
            if (e.get("device") and e["device"] != "robot" and e["kind"] == kind
                    and (not fault or e.get("fault") == fault)):
                buses.setdefault(bus_of(e.get("log")), set()).add(e["device"])
        if ORDER.get(i["severity"], 3) < ORDER.get(it["severity"], 3):
            it["severity"], it["worst"] = i["severity"], i["id"]
        elif i["severity"] == it["severity"] and len(i["kinds"]) > len(
                next(x for x in incidents if x["id"] == it["worst"])["kinds"]):
            it["worst"] = i["id"]
    # The same problem seen from another angle joins it: a dip in a session
    # that browned out is the brownout; the DS's lost-comms message is the
    # comms loss.
    for small, big in FOLD.items():
        if small in out and big in out and small != big:
            a, b = out.pop(small), out[big]
            b["count"] += a["count"]
            b["occurrences"] += a["occurrences"]
            b["at"] += a["at"]
            b["phases"] += a["phases"]
            b["kinds"] |= a["kinds"]
            b["devices"] |= a["devices"]
            for bus, devs in (a.get("buses") or {}).items():
                b.setdefault("buses", {}).setdefault(bus, set()).update(devs)
            b["longest_s"] = max(b.get("longest_s", 0.0), a.get("longest_s", 0.0))
            for unit, v in (a.get("figures") or {}).items():
                f = b.setdefault("figures", {})
                f[unit] = (min if unit == "V" else max)(f[unit], v) if unit in f else v
    ranked = sorted(out.values(), key=lambda it: (
        ORDER.get(it["severity"], 3), PRIMARY.index(it["primary"].split(":")[0])
        if it["primary"].split(":")[0] in PRIMARY else 99, -it["count"]))
    for it in ranked:
        it["kinds"], it["devices"] = sorted(it["kinds"]), sorted(it["devices"])
        it["buses"] = {bus: sorted(d) for bus, d in sorted((it.get("buses") or {}).items())}
        it["phases"] = sorted(set(it["phases"]))
        it["at"] = it["at"][:12]
    return ranked


# ── the moment ───────────────────────────────────────────────────────────

_MOMENT = (
    ("battery_v", "SystemStats/BatteryVoltage", "Robot", "v_min", min),
    ("ds_battery_v", "DS/BatteryVoltage", "Robot", "v_min", min),
    ("total_current_a", "PowerDistribution/TotalCurrent", "Robot", "v_max", max),
    ("total_current_a", "RealOutputs/Robot Total Current Amps", "Robot", "v_max", max),
    ("total_current_a", "SystemStats/BatteryCurrent", "Robot", "v_max", max),
    ("total_current_a", "DS/TotalCurrent", "Robot", "v_max", max),
    ("can_util_pct", "SystemStats/CANBus/Utilization", "Robot", "v_max", max),
    ("ds_can_util_pct", "DS/CANUtilPct", "Robot", "v_max", max),
    ("loop_ms", "RealOutputs/LoggedRobot/FullCycleMS", "Robot", "v_max", max),
    ("trip_ms", "DS/TripTimeMS", "Robot", "v_max", max),
    ("packet_loss_pct", "DS/PacketLossPct", "Robot", "v_max", max),
    ("ds_comms", "DS/Comms", "Robot", "v_min", min),
    ("radio_connected", "RadioStatus/Connected", "Robot", "v_min", min),
    ("roborio_cpu_pct", "DS/CPUPct", "Robot", "v_max", max),
)


def bus_of(source_name: str | None) -> str:
    """Which CAN bus a log recorded: the roboRIO's hoot is `rio_…`, a
    CANivore's carries its serial; AdvantageKit and DS logs aren't a bus."""
    name = str(source_name or "")
    if name.startswith("rio_"):
        return "roboRIO CAN"
    if re.match(r"[0-9A-F]{16,}_", name):
        return "CANivore"
    return "AdvantageKit" if name.endswith(".wpilog") else "bus not named (a DS message)"


def moment(db, uid: str, at_s: float, span_s: float, phases_of: Callable) -> dict[str, Any]:
    """What every log of `uid`'s recording read in [at_s - span_s, at_s + span_s]
    (seconds from the recording's start, as `build` gives them)."""
    logs = recording(db, uid)
    if not logs:
        return {"error": f"no session {uid!r}"}
    starts = clock(db, logs, phases_of)
    t0 = min(starts.values())
    a, b = at_s - span_s, at_s + span_s
    out: dict[str, Any] = {}
    motors: list[dict] = []
    pdh: list[dict] = []                 # the DS log's PDH channels, when one is in the recording
    lowest_supply = None
    for r in logs:
        off = starts[r["id"]] - t0
        lo, hi = int(a - off), int(b - off) + 1

        def stat(series_id, col):
            rows = [x[0] for x in db.fetchall(
                f"SELECT {col} FROM samples.sample_1s WHERE series_id = ? AND t_s BETWEEN ? AND ?",
                (series_id, lo, hi)) if x[0] is not None]
            return rows

        for key, name, dtype, col, pick in _MOMENT:
            for sid in ev._series(db, r["id"], name, dtype):
                vals = stat(sid, col)
                if vals:
                    v = pick(vals)
                    if key == "can_util_pct" and v <= 1:
                        v *= 100
                    out[key] = round(v, 4) if key not in out else round(pick(out[key], v), 4)
        if r["source_kind"] == "dslog":
            for ch in range(24):
                for sid in ev._series(db, r["id"], f"DS/PDCurrent[{ch}]"):
                    vals = stat(sid, "v_max")
                    if vals and max(vals) >= 1.0:
                        pdh.append({"ch": ch, "a": round(max(vals), 4)})
        for d in db.fetchall("""SELECT DISTINCT d.id, d.device_type, d.can_id, d.label
                                FROM device d JOIN series se ON se.device_id = d.id
                                WHERE se.session_id = ? AND d.device_type IN ('TalonFX', 'TalonFXS')""",
                             (r["id"],)):
            name = d["label"] or f"{d['device_type']} {d['can_id']}"
            for sid in ev._series(db, r["id"], "StatorCurrent", d["device_type"]):
                if db.fetchone("SELECT device_id FROM series WHERE id = ?", (sid,))[0] != d["id"]:
                    continue
                vals = stat(sid, "v_max") + [-x for x in stat(sid, "v_min")]
                if vals:
                    m = {"device": name, "bus": bus_of(r["source_name"]),
                         "stator_a_max": round(max(vals), 4),
                         # where its trace is, so a card can draw it
                         "session_uid": r["uid"], "device_type": d["device_type"],
                         "can_id": d["can_id"]}
                    for ssid in ev._series(db, r["id"], "SupplyCurrent", d["device_type"]):
                        if db.fetchone("SELECT device_id FROM series WHERE id = ?",
                                       (ssid,))[0] == d["id"]:
                            sup = stat(ssid, "v_max")
                            if sup:
                                m["supply_a_max"] = round(max(sup), 4)
                    motors.append(m)
            for sid in ev._series(db, r["id"], "SupplyVoltage", d["device_type"]):
                if db.fetchone("SELECT device_id FROM series WHERE id = ?", (sid,))[0] != d["id"]:
                    continue
                vals = [v for v in stat(sid, "v_min") if v > 0.5]
                if vals and (lowest_supply is None or min(vals) < lowest_supply["v"]):
                    lowest_supply = {"device": name, "v": round(min(vals), 4)}
    motors.sort(key=lambda m: -m["stator_a_max"])
    out["hardest_motors"] = motors[:3]
    # What loaded the battery: supply current (stator can be several times it).
    fed = sorted((m for m in motors if m.get("supply_a_max")), key=lambda m: -m["supply_a_max"])
    if fed:
        out["most_supply_current"] = fed[:3]
    if pdh:
        out["pdh_channels"] = sorted(pdh, key=lambda c: -c["a"])[:3]
    if lowest_supply:
        out["lowest_controller_supply"] = lowest_supply
    return {"from_s": round(a, 3), "to_s": round(b, 3), "readings": out,
            "logs": [r["source_name"] for r in logs]}
