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
* **not_driven**: the robot was enabled **and in use** (another motor on the
  same bus was driven), this motor was on the bus, and it was never
  commanded: its ControlMode stayed neutral/disabled and it put out no
  voltage or current. Flagged against its own history ("normally driven") and
  against its partners ("another Shooter motor ran, this one didn't"), which
  needs no history at all. Last year's shooter. An enabled robot nobody drove
  flags nothing.
* **out_of_range**: a device's peak (temperature, current) or low (supply
  voltage) beyond **anything it reached before** plus a margin, judged only
  against comparable logs (an enabled log against earlier enabled logs; a pit
  recording against pit recordings). When one signal is out on several devices
  of one bus at once, it's reported once, for the bus.

Tuned on the team's 63 real logs (2026-10-05, `tools/anomaly_report.py`): a
first version flagged 150 ranges and whole drivetrains as "never driven";
comparing pit logs with matches and an enabled-but-idle robot were the noise.
* **config_changed**: firmware version, inversion (AppliedRotorPolarity),
  attached motor type, licence differ from what the device usually reports.
* **new_fault**: a device latched a fault it has never latched before.

"Earlier logs" are the same bus's (`bus_of`: the AdvantageKit logs, the Rio
hoots, each CANivore's hoots), newest `HISTORY` of them. Thresholds are named
constants, not tuned per log. No Qt, any `db` with fetchall/fetchone.

**Home runs this module unchanged over SQL Server** (home `pit/pitdb.py`
translates its SQL; R14, 2026-10-05). A new table, a new column, or
SQLite-only syntax here needs a dated note in home/REQUESTS.md: R1 parity
will catch the drift, but home asked for the warning first.
"""

from __future__ import annotations

import statistics
from typing import Any

HISTORY = 30                 # earlier logs of the same bus to learn from
MIN_HISTORY = 3              # fewer than this: no history-based judgement
MIN_RANGE_HISTORY = 5        # ranges need more: early logs were all light running
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
        in_use = any(v for v in driven_now.values())
        for key, now in driven_now.items():
            if now is not False or not in_use:
                continue                      # enabled but nobody drove it: not a fault
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
                modes = sorted(m for m in _modes(db, me["id"], d["id"], windows)
                               if not m.startswith(IDLE_MODES))
                if modes:
                    what = (f"commanded ({', '.join(modes)}) but never moved or drew current "
                            f"while the robot was enabled ({round(enabled_s)} s)")
                    check = ("Check the setpoints the code sent it: it held its starting point "
                             "the whole time (a target never updated, a state that never ran?)")
                else:
                    what = ("on the bus but never commanded while the robot was enabled "
                            f"({round(enabled_s)} s)")
                    check = ("Check it's created, configured and given output in the robot "
                             "code (a follower that was never set?)")
                out.append({"kind": "not_driven", "severity": "fault", "device": _label(d),
                            "device_type": key[0], "can_id": key[1], "subsystem": d["subsystem"],
                            "enabled_s": round(enabled_s), "control_modes": modes,
                            "detail": f"{what}: " + "; ".join(reasons) + f". {check}"})

    # ── out of normal range ─────────────────────────────────────────────
    # Comparable history only: an enabled log against earlier enabled logs.
    was_enabled = enabled_s >= MIN_ENABLED_S
    like = [sid for sid in hist_ids if _enabled_s(db, sid, phases_of) >= MIN_ENABLED_S] \
        if was_enabled else [sid for sid in hist_ids if _enabled_s(db, sid, phases_of) == 0]
    ranged: list[dict] = []
    if len(like) >= MIN_RANGE_HISTORY:
        for key, d in devices.items():
            for signal, side, margin in RANGES:
                now = _extreme(db, [me["id"]], d["id"], signal, side)
                if not now:
                    continue
                past = _extreme(db, like, d["id"], signal, side)
                if len(past) < MIN_RANGE_HISTORY:
                    continue
                value = now[0]
                edge = max(past) if side != "min" else min(past)
                beyond = value > edge + margin if side != "min" else value < edge - margin
                if beyond:
                    ranged.append({"kind": "out_of_range", "severity": "warn",
                                   "device": _label(d), "device_type": key[0], "can_id": key[1],
                                   "subsystem": d["subsystem"], "signal": signal,
                                   "value": _r(value),
                                   "normal": {"median": _r(statistics.median(past)),
                                              "low": _r(min(past)), "high": _r(max(past)),
                                              "logs": len(past)},
                                   "detail": f"{signal} {'fell to' if side == 'min' else 'peaked at'} "
                                             f"{_r(value)}, beyond anything in {len(past)} comparable "
                                             f"earlier logs ({_r(min(past))} to {_r(max(past))}, "
                                             f"median {_r(statistics.median(past))})"})
    # One signal out on several devices of the bus at once: one finding, for the bus.
    by_signal: dict[str, list[dict]] = {}
    for a in ranged:
        by_signal.setdefault(a["signal"], []).append(a)
    for signal, group in by_signal.items():
        if len(group) >= 3:
            worst = max(group, key=lambda a: abs(a["value"] - a["normal"]["median"]))
            out.append({"kind": "out_of_range", "severity": "warn",
                        "device": f"{len(group)} devices on this bus", "device_type": None,
                        "can_id": None, "subsystem": None, "signal": signal,
                        "value": worst["value"], "normal": worst["normal"],
                        "devices": [a["device"] for a in group],
                        "detail": f"{signal} beyond its usual range on {len(group)} devices at "
                                  f"once (worst {worst['device']}: {worst['value']} against "
                                  f"{worst['normal']['low']} to {worst['normal']['high']}): "
                                  "a robot-wide cause, e.g. battery, a harder session"})
        else:
            out.extend(group)

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
                                "usual_in": values.count(usual), "history_logs": len(values),
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


def _modes(db, session_id: int, device_id: int, windows) -> set[str]:
    """The ControlMode labels this motor reported while enabled (a constant counts)."""
    out: set[str] = set()
    c = db.fetchone(
        """SELECT sc.v_text FROM session_constant sc JOIN signal g ON g.id = sc.signal_id
           WHERE sc.session_id = ? AND sc.device_id = ? AND g.name = 'ControlMode'""",
        (session_id, device_id))
    if c is not None and c["v_text"]:
        out.add(c["v_text"])
    sid = _series_ids(db, session_id, device_id, ["ControlMode"]).get("ControlMode")
    if sid is not None:
        for a, b in windows:
            out.update(r["label"] for r in db.fetchall(
                """SELECT DISTINCT e.label FROM samples.sample s
                   JOIN series se ON se.id = s.series_id
                   JOIN signal_enum e ON e.signal_id = se.signal_id AND e.code = s.v
                   WHERE s.series_id = ? AND s.t_ms >= ? AND s.t_ms <= ?""",
                (sid, int(a * 1000), int(b * 1000))) if r["label"])
    return out


def _driven(db, session_id: int, device_id: int, windows) -> bool | None:
    """Did this motor produce output while the robot was enabled? None: can't tell."""
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
    if not told:
        # No output signals at all: fall back to whether anything commanded it.
        modes = _modes(db, session_id, device_id, windows)
        if modes:
            return any(not m.startswith(IDLE_MODES) for m in modes)
        return None
    return False


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
    per: dict[int, float] = {}
    pick = (max if side != "min" else min)
    for r in db.fetchall(
            f"""SELECT se.session_id AS s, {col} AS x FROM series se
                JOIN signal g ON g.id = se.signal_id
                WHERE se.session_id IN ({marks}) AND se.device_id = ? AND g.name = ?
                  AND {where}""", (*session_ids, device_id, signal)):
        per[r["s"]] = pick(per[r["s"]], r["x"]) if r["s"] in per else r["x"]
    for r in db.fetchall(
            f"""SELECT sc.session_id AS s, {const} AS x FROM session_constant sc
                JOIN signal g ON g.id = sc.signal_id
                WHERE sc.session_id IN ({marks}) AND sc.device_id = ? AND g.name = ?
                  AND sc.v IS NOT NULL""", (*session_ids, device_id, signal)):
        per[r["s"]] = pick(per[r["s"]], r["x"]) if r["s"] in per else r["x"]
    return [float(v) for v in per.values()]   # one value per session


_ENABLED_CACHE: dict[str, float] = {}       # by uid: unique across databases


def _enabled_s(db, session_id: int, phases_of) -> float:
    """Seconds a session spent enabled (auto + teleop + test)."""
    uid = db.fetchone("SELECT uid FROM log_session WHERE id = ?", (session_id,))["uid"]
    if not uid:
        return 0.0
    if uid not in _ENABLED_CACHE:
        ph = phases_of(uid)
        _ENABLED_CACHE[uid] = sum(
            p["end_s"] - p["start_s"] for p in (ph or {}).get("phases") or []
            if p.get("phase") in ("auto", "teleop", "test"))
    return _ENABLED_CACHE[uid]


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
