"""
What isn't normal in this log, judged against this robot's own history.

Brayden, 2026-10-05: "a model that effectively filters out abnormalities; finds
issues before we can and has a basic understanding of what's normal ... Last
year we had an entire shooter motor not being initialized correctly in the
code." An 8B language model can't learn "normal" from one log; a detector
built from the team's own logs can, every time, with exact numbers. So this is
code, and the analyst (app/ai/) explains what it finds.

Five kinds, each from data the logs already carry:

* **missing**: a device on this CAN bus in most earlier logs isn't in this one
  (power, wiring, a changed CAN id).
* **not_driven**: the robot was enabled, the motor was on the bus, and it was
  never commanded: its ControlMode stayed neutral/disabled and it put out no
  voltage or current. Flagged against its own history ("normally driven") and
  against its partners ("another Shooter motor ran, this one didn't"), which
  needs no history at all. Last year's shooter.
* **out_of_range**: a device's peak (temperature, current) or low (supply
  voltage) beyond what it normally reaches: the median of earlier logs plus a
  robust spread (MAD) or an absolute margin, whichever is larger.
* **config_changed**: firmware version, inversion (AppliedRotorPolarity),
  attached motor type, licence differ from what the device usually reports.
* **new_fault**: a device latched a fault it has never latched before.

"Earlier logs" are the same bus's (`bus_of`: the AdvantageKit logs, the Rio
hoots, each CANivore's hoots), newest `HISTORY` of them. Thresholds are named
constants, not tuned per log. No Qt, any `db` with fetchall/fetchone.
"""

from __future__ import annotations

import statistics
from typing import Any

HISTORY = 30                 # earlier logs of the same bus to learn from
MIN_HISTORY = 3              # fewer than this: no history-based judgement
PRESENT_RATE = 0.6           # "usually there": in at least this share of history
DRIVEN_RATE = 0.6            # "usually driven": in at least this share of enabled history
MIN_ENABLED_S = 20.0         # enabled this long before "never driven" means anything

# A motor counts as driven when any of these moved this far while enabled.
DRIVE_SIGNALS = {"DutyCycle": 0.02, "MotorVoltage": 0.5, "StatorCurrent": 2.0,
                 "TorqueCurrent": 2.0}
# ControlMode labels that mean "nothing is commanding this motor".
IDLE_MODES = ("DisabledOutput", "NeutralOut", "Disabled", "CoastOut", "StaticBrake")

# (signal, which extreme, absolute margin): beyond median + max(4·MAD·1.4826, margin).
# Currents are "abs": a motor working in reverse reads negative.
RANGES = [("DeviceTemp", "max", 10.0), ("ProcessorTemp", "max", 10.0),
          ("StatorCurrent", "abs", 20.0), ("SupplyCurrent", "abs", 15.0),
          ("SupplyVoltage", "min", 1.0)]
CONFIG = ("VersionMajor", "VersionMinor", "VersionBugfix", "VersionBuild",
          "AppliedRotorPolarity", "ConnectedMotor", "IsProLicensed")
# Faults that say nothing new the first time (power-on noise).
QUIET_FAULTS = ("StickyFault_BridgeBrownout", "StickyFault_Undervoltage",
                "StickyFault_BootDuringEnable")


def _r(x):
    return None if x is None else round(float(x), 4)


def bus_of(row) -> str:
    """Which bus a session recorded: AdvantageKit, the Rio, or one CANivore."""
    kind = row["source_kind"] or ""
    name = (row["source_name"] or "").lower()
    if kind == "wpilog":
        return "wpilog"
    return f"hoot:{row['device_serial'] or name.split('_', 1)[0]}"


def _label(dev) -> str:
    return dev["label"] or f"{dev['device_type']} {dev['can_id']}"


def find(db, session_uid: str, phases_of) -> dict[str, Any]:
    """Every anomaly in one session. `phases_of(uid)` gives a session's
    `match_phases` (passed in, so this module needs nothing from app/ai)."""
    phases = phases_of(session_uid)
    me = db.fetchone("SELECT id, source_kind, source_name, device_serial, started_at "
                     "FROM log_session WHERE uid = ?", (session_uid,))
    if me is None:
        return {"error": f"no session {session_uid!r}"}
    bus = bus_of(me)
    earlier = [r for r in db.fetchall(
        """SELECT id, source_kind, source_name, device_serial FROM log_session
           WHERE id <> ? AND (started_at IS NULL OR ? IS NULL OR started_at <= ?)
           ORDER BY started_at DESC, id DESC""", (me["id"], me["started_at"], me["started_at"]))
        if bus_of(r) == bus][:HISTORY]
    hist_ids = [r["id"] for r in earlier]
    out: list[dict] = []

    devices = {(d["device_type"], d["can_id"]): d for d in db.fetchall(
        """SELECT DISTINCT d.id, d.device_type, d.can_id, d.label, d.subsystem
           FROM device d JOIN series se ON se.device_id = d.id
           WHERE se.session_id = ? AND d.device_type <> 'Robot'""", (me["id"],))}

    # ── missing ─────────────────────────────────────────────────────────
    if len(hist_ids) >= MIN_HISTORY:
        seen: dict[tuple, int] = {}
        info: dict[tuple, Any] = {}
        for sid in hist_ids:
            for d in db.fetchall(
                    """SELECT DISTINCT d.device_type, d.can_id, d.label, d.subsystem
                       FROM device d JOIN series se ON se.device_id = d.id
                       WHERE se.session_id = ? AND d.device_type <> 'Robot'""", (sid,)):
                key = (d["device_type"], d["can_id"])
                seen[key] = seen.get(key, 0) + 1
                info[key] = d
        for key, n in sorted(seen.items()):
            if key not in devices and n / len(hist_ids) >= PRESENT_RATE:
                d = info[key]
                out.append({"kind": "missing", "severity": "fault", "device": _label(d),
                            "device_type": key[0], "can_id": key[1], "subsystem": d["subsystem"],
                            "detail": f"on this bus in {n} of the last {len(hist_ids)} logs, "
                                      "not in this one: power, CAN wiring or a changed CAN id",
                            "history_logs": len(hist_ids), "present_in": n})

    # ── not driven while enabled ────────────────────────────────────────
    windows = [(p["start_s"], p["end_s"]) for p in (phases or {}).get("phases") or []
               if p.get("phase") in ("auto", "teleop", "test")]
    enabled_s = sum(b - a for a, b in windows)
    driven_now: dict[tuple, bool | None] = {}
    if enabled_s >= MIN_ENABLED_S:
        for key, d in devices.items():
            if key[0] not in ("TalonFX", "TalonFXS"):
                continue
            driven_now[key] = _driven(db, me["id"], d["id"], windows)
        hist_driven = (_history_driven(db, hist_ids, devices, phases_of)
                       if len(hist_ids) >= MIN_HISTORY else {})
        by_sub: dict[str, list[tuple]] = {}
        for key, d in devices.items():
            if d["subsystem"]:
                by_sub.setdefault(d["subsystem"], []).append(key)
        for key, now in driven_now.items():
            if now is not False:
                continue
            d = devices[key]
            reasons = []
            rate = hist_driven.get(key)
            if rate is not None and rate[1] >= MIN_HISTORY and rate[0] / rate[1] >= DRIVEN_RATE:
                reasons.append(f"driven in {rate[0]} of {rate[1]} earlier enabled logs")
            partners = [devices[k] for k in by_sub.get(d["subsystem"] or "", [])
                        if k != key and driven_now.get(k)]
            if partners:
                reasons.append(f"{_label(partners[0])} in the same subsystem "
                               f"({d['subsystem']}) was driven")
            if reasons:
                out.append({"kind": "not_driven", "severity": "fault", "device": _label(d),
                            "device_type": key[0], "can_id": key[1], "subsystem": d["subsystem"],
                            "enabled_s": _r(enabled_s),
                            "detail": "on the bus but never commanded while the robot was "
                                      f"enabled ({round(enabled_s)} s): "
                                      + "; ".join(reasons)
                                      + ". Check it's created, configured and given output in "
                                        "the robot code (a follower that was never set?)"})

    # ── out of normal range ─────────────────────────────────────────────
    if len(hist_ids) >= MIN_HISTORY:
        for key, d in devices.items():
            for signal, side, margin in RANGES:
                now = _extreme(db, [me["id"]], d["id"], signal, side)
                if not now:
                    continue
                past = _extreme(db, hist_ids, d["id"], signal, side)
                if len(past) < MIN_HISTORY:
                    continue
                med = statistics.median(past)
                mad = statistics.median(abs(x - med) for x in past) * 1.4826
                spread = max(4 * mad, margin)
                value = now[0]
                beyond = value > med + spread if side != "min" else value < med - spread
                if beyond:
                    out.append({"kind": "out_of_range", "severity": "warn", "device": _label(d),
                                "device_type": key[0], "can_id": key[1],
                                "subsystem": d["subsystem"], "signal": signal,
                                "value": _r(value),
                                "normal": {"median": _r(med), "low": _r(min(past)),
                                           "high": _r(max(past)), "logs": len(past)},
                                "detail": f"{signal} {'fell to' if side == 'min' else 'peaked at'} "
                                          f"{_r(value)}; across {len(past)} earlier logs it "
                                          f"{'bottomed' if side == 'min' else 'peaked'} between "
                                          f"{_r(min(past))} and {_r(max(past))} (median {_r(med)})"})

    # ── configuration changed ───────────────────────────────────────────
    if len(hist_ids) >= MIN_HISTORY:
        for key, d in devices.items():
            now = _config(db, [me["id"]], d["id"])
            past = _config(db, hist_ids, d["id"])
            for name in CONFIG:
                if name not in now or not past.get(name):
                    continue
                values = past[name]
                usual = max(set(values), key=values.count)
                if now[name][0] != usual and values.count(usual) >= MIN_HISTORY:
                    out.append({"kind": "config_changed",
                                "severity": "fault" if name == "AppliedRotorPolarity" else "warn",
                                "device": _label(d), "device_type": key[0], "can_id": key[1],
                                "subsystem": d["subsystem"], "signal": name,
                                "value": now[name][0], "usual": usual,
                                "detail": f"{name} is {now[name][0]!s}; it was {usual!s} in "
                                          f"{values.count(usual)} of {len(values)} earlier logs"
                                          + (" (a motor's direction flipped: check inversion "
                                             "in the code)" if name == "AppliedRotorPolarity"
                                             else "")})

    # ── a fault never latched before ────────────────────────────────────
    if len(hist_ids) >= MIN_HISTORY:
        for key, d in devices.items():
            now = {r["name"] for r in db.fetchall(
                """SELECT DISTINCT g.name FROM fault_event f JOIN signal g ON g.id = f.signal_id
                   WHERE f.session_id = ? AND f.device_id = ? AND f.sticky = 1""",
                (me["id"], d["id"]))}
            if not now:
                continue
            marks = ",".join("?" * len(hist_ids))
            before = {r["name"] for r in db.fetchall(
                f"""SELECT DISTINCT g.name FROM fault_event f JOIN signal g ON g.id = f.signal_id
                    WHERE f.session_id IN ({marks}) AND f.device_id = ? AND f.sticky = 1""",
                (*hist_ids, d["id"]))}
            for name in sorted(now - before):
                if name in QUIET_FAULTS or name.endswith("Field"):
                    continue
                out.append({"kind": "new_fault", "severity": "warn", "device": _label(d),
                            "device_type": key[0], "can_id": key[1], "subsystem": d["subsystem"],
                            "signal": name, "fault": name.replace("StickyFault_", ""),
                            "detail": f"first time in {len(hist_ids)} logs this device latched "
                                      f"{name.replace('StickyFault_', '')}"})

    order = {"fault": 0, "warn": 1}
    out.sort(key=lambda a: (order.get(a["severity"], 2), a["kind"], a["device"]))
    return {"bus": bus, "history_logs": len(hist_ids), "enabled_s": _r(enabled_s),
            "anomalies": out,
            "note": None if len(hist_ids) >= MIN_HISTORY else
            f"only {len(hist_ids)} earlier log(s) on this bus: history checks need "
            f"{MIN_HISTORY}; partner checks still ran"}


# ── helpers ─────────────────────────────────────────────────────────────

def _series_ids(db, session_id: int, device_id: int, names) -> dict[str, int]:
    marks = ",".join("?" * len(names))
    return {r["name"]: r["id"] for r in db.fetchall(
        f"""SELECT se.id, g.name FROM series se JOIN signal g ON g.id = se.signal_id
            WHERE se.session_id = ? AND se.device_id = ? AND g.name IN ({marks})""",
        (session_id, device_id, *names))}


def _driven(db, session_id: int, device_id: int, windows) -> bool | None:
    """Was this motor commanded while the robot was enabled? None: can't tell."""
    ids = _series_ids(db, session_id, device_id, list(DRIVE_SIGNALS) + ["ControlMode"])
    told = False
    for name, limit in DRIVE_SIGNALS.items():
        sid = ids.get(name)
        if sid is None:
            # A signal that never moved is a session constant.
            c = db.fetchone(
                """SELECT sc.v FROM session_constant sc JOIN signal g ON g.id = sc.signal_id
                   WHERE sc.session_id = ? AND sc.device_id = ? AND g.name = ?""",
                (session_id, device_id, name))
            if c is not None:
                told = True
                if c["v"] is not None and abs(c["v"]) > limit:
                    return True
            continue
        told = True
        for a, b in windows:
            r = db.fetchone(
                """SELECT MAX(ABS(v_max)) AS hi, MAX(ABS(v_min)) AS lo FROM samples.sample_1s
                   WHERE series_id = ? AND t_s >= ? AND t_s <= ?""", (sid, int(a), int(b) + 1))
            peak = max((x for x in ((r["hi"], r["lo"]) if r is not None else ()) if x is not None),
                       default=None)
            if peak is not None and peak > limit:
                return True
    sid = ids.get("ControlMode")
    if sid is not None:
        for a, b in windows:
            labels = [r["label"] for r in db.fetchall(
                """SELECT DISTINCT e.label FROM samples.sample s
                   JOIN series se ON se.id = s.series_id
                   JOIN signal_enum e ON e.signal_id = se.signal_id AND e.code = s.v
                   WHERE s.series_id = ? AND s.t_ms >= ? AND s.t_ms <= ?""",
                (sid, int(a * 1000), int(b * 1000)))]
            if any(lb and not lb.startswith(IDLE_MODES) for lb in labels):
                return True
        told = True
    return False if told else None


def _history_driven(db, hist_ids, devices, phases_of) -> dict[tuple, tuple[int, int]]:
    """(driven, enabled logs) per device across earlier logs that were enabled."""
    out: dict[tuple, list[int]] = {}
    for sid in hist_ids:
        uid = db.fetchone("SELECT uid FROM log_session WHERE id = ?", (sid,))["uid"]
        ph = phases_of(uid) if uid else {}
        windows = [(p["start_s"], p["end_s"]) for p in (ph or {}).get("phases") or []
                   if p.get("phase") in ("auto", "teleop", "test")]
        if sum(b - a for a, b in windows) < MIN_ENABLED_S:
            continue
        for key in devices:
            if key[0] not in ("TalonFX", "TalonFXS"):
                continue
            d = db.fetchone("SELECT id FROM device WHERE device_type = ? AND can_id = ?", key)
            got = _driven(db, sid, d["id"], windows) if d else None
            if got is None:
                continue
            tally = out.setdefault(key, [0, 0])
            tally[0] += 1 if got else 0
            tally[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def _extreme(db, session_ids, device_id: int, signal: str, side: str) -> list[float]:
    """Per-session max, min, or largest magnitude ("abs") of one signal, from
    the series rollup or a constant."""
    if not session_ids:
        return []
    marks = ",".join("?" * len(session_ids))
    if side == "abs":
        col, where = "MAX(ABS(se.v_max), ABS(se.v_min))", "se.v_max IS NOT NULL AND se.v_min IS NOT NULL"
        const = "ABS(sc.v)"
    else:
        col = "se.v_max" if side == "max" else "se.v_min"
        where, const = f"{col} IS NOT NULL", "sc.v"
    vals = [r["x"] for r in db.fetchall(
        f"""SELECT {col} AS x FROM series se JOIN signal g ON g.id = se.signal_id
            WHERE se.session_id IN ({marks}) AND se.device_id = ? AND g.name = ?
              AND {where}""", (*session_ids, device_id, signal))]
    vals += [r["x"] for r in db.fetchall(
        f"""SELECT {const} AS x FROM session_constant sc JOIN signal g ON g.id = sc.signal_id
            WHERE sc.session_id IN ({marks}) AND sc.device_id = ? AND g.name = ?
              AND sc.v IS NOT NULL""", (*session_ids, device_id, signal))]
    return [float(v) for v in vals]


def _config(db, session_ids, device_id: int) -> dict[str, list]:
    """Configuration constants per signal, one value per session."""
    marks = ",".join("?" * len(session_ids))
    names = ",".join("?" * len(CONFIG))
    out: dict[str, list] = {}
    for r in db.fetchall(
            f"""SELECT g.name, COALESCE(sc.v_text, sc.v) AS x FROM session_constant sc
                JOIN signal g ON g.id = sc.signal_id
                WHERE sc.session_id IN ({marks}) AND sc.device_id = ? AND g.name IN ({names})""",
            (*session_ids, device_id, *CONFIG)):
        out.setdefault(r["name"], []).append(r["x"])
    return out
