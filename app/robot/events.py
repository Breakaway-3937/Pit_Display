"""
The moments a CSA looks for, read out of every log: timed events.

Brayden, 2026-11-03: "a totally fluent CSA essentially going through each log
and finding all the potential problems." A CSA doesn't read a match; they find
the moment it went wrong and look at everything at that moment. This is the
first half: every log of a recording turned into events with a time, a kind,
a device and the figure behind it. `incidents.py` lines the logs up on one
clock and groups events that happened together.

Each kind is grounded in the documentation it comes from:

* **power**: `brownout` (roboRIO `SystemStats/BrownedOut`, the PDH's brownout,
  the DS's brownout flag): the roboRIO disabled outputs (stage 2: 6.3 V on a
  roboRIO 1, 6.75 V default on a roboRIO 2; WPILib "roboRIO Brownout");
  `voltage_dip` (battery under `DIP_V` while enabled); `device_voltage_dip`
  (a controller's own supply under `DIP_V`); `rail_fault` (a 3.3/5/6 V rail's
  current-fault counter rose: a short on sensor wiring).
* **CAN**: `can_bus_off`, `can_tx_full`, `can_errors` (the roboRIO's CAN
  counters rising), `can_busy` (utilization over `CAN_BUSY_PCT`);
  `device_reset` (a controller's ResetCount rose: it rebooted);
  `device_fault` (a live, non-sticky fault the robot raised, by name).
* **comms**: `comms_lost` (the DS had no battery reading: no robot), `radio_lost`
  (`RadioStatus/Connected` fell), `packet_loss`, `high_trip_time` (the DS log),
  `comms_disable` (the roboRIO's `CommsDisableCount` rose: it disabled itself
  for lost comms), `watchdog` (the DS's watchdog flag), `disabled_mid_match`
  (enabled, FMS attached, then disabled with match time left), `estop`.
* **the DS's messages** (`.dsevents`, WPILib "Driver Station Errors/Warnings"):
  44004 lost comms, 44007 time since boot (a small one is a roboRIO reboot:
  `roborio_reboot`), 44008 radio lost/seen, 44009 a joystick unplugged while
  enabled, 44002 ping results (which link is bad), 44003 no robot code;
  and the roboRIO's own status lines: `low_memory` (its "Memory Free" under
  `LOW_MEMORY_MB`), `console_spam` (one line printed over and over:
  printing is slow on a roboRIO and eats loop time), `radio_firmware` (the
  DS says the radio's firmware needs updating: an inspection item).
  A comms loss counts (`comms_lost`, `lost_comms_msg`) only while enabled or
  just after: in the pit or the queue the robot is switched off between
  matches, and the team's real DS logs lost it in 47 of 90 recordings.
* **code**: `code_restart` ("Robot program starting" after the first: the
  code crashed and the roboRIO restarted it); what the console printed, by
  meaning (`classify`): `config_failed` (a controller's configuration never
  applied: it runs on defaults), `missing_file` (a deploy file, e.g. a
  PathPlanner auto, isn't on the robot), `can_timeout` (a CAN read timed out,
  e.g. reading the PDH every loop), `exception`, else `code_error`; `loop_stall` (a cycle ≥ 100 ms
  while enabled), `gc_pause` (Java's garbage collector took ≥ `GC_MS`).
* **mechanism**: `motor_stall` (≥ `STALL_A` stator current with the rotor
  under `STALL_RPS` for ≥ `STALL_S` while enabled: jammed, against a hard
  stop, or a setpoint it can't reach).

Plain SQL; reads `samples.sample` (every change point), `samples.sample_1s`,
`fault_event`, `signal_enum`. No Qt. Thresholds are named constants.
"""

from __future__ import annotations

import re
from typing import Any

DIP_V = 7.0                 # battery / controller supply under this while enabled
CAN_BUSY_PCT = 90.0
STALL_A, STALL_RPS, STALL_S = 40.0, 0.5, 2
GC_MS = 50.0
LOOP_STALL_MS = 100.0
PACKET_LOSS = 0.2           # a 20 ms DS record losing this share of packets
TRIP_MS = 50.0              # round trip DS ↔ robot
REBOOT_UPTIME_S = 120.0     # "time since robot boot" under this: it just rebooted
FIELD_GAP_S = 3.5           # the field disables 3 s between auto and teleop; past that, lost
COMMS_SPAN_S = 120.0        # the longest comms gap an incident spans
REBOOT_GAP_S = 20.0         # comms gone this long, then a fresh code start: a reboot
COMMS_GRACE_S = 3.0         # a comms loss this soon after a disable caused the disable
LOW_MEMORY_MB = 20          # the roboRIO's free memory under this (its usual: 150-220 MB)
SPAM_LINES = 200            # one console line this often in a log, at least
SPAM_PER_MIN = 20.0         # …and this fast while it lasted (the team's: up to 150/min)

SEVERITY = {
    "brownout": "fault", "roborio_reboot": "fault", "code_restart": "fault",
    "comms_lost": "fault", "comms_disable": "fault", "disabled_mid_match": "fault",
    "can_bus_off": "fault", "device_reset": "fault", "watchdog": "fault",
    "radio_lost": "fault", "estop": "warn", "rail_fault": "fault",
    "voltage_dip": "warn", "device_voltage_dip": "warn", "can_tx_full": "warn",
    "can_errors": "warn", "can_busy": "warn", "packet_loss": "warn",
    "high_trip_time": "warn", "code_error": "warn", "loop_stall": "warn",
    "gc_pause": "warn", "motor_stall": "warn", "device_fault": "warn",
    "joystick_unplugged": "warn", "ping_status": "info", "radio_events": "info",
    "config_failed": "fault", "missing_file": "fault", "can_timeout": "warn",
    "exception": "fault",
    "no_robot_code": "warn", "lost_comms_msg": "fault",
    "low_memory": "warn", "console_spam": "warn", "radio_firmware": "warn",
    "ds_overloaded": "warn", "fms_lost": "fault", "joystick_missing": "warn",
    "ping_radio_bad": "info", "ping_rio_bad": "info", "ping_ds_radio_bad": "info",
}
# Live faults worth an event (the rest are current limits or summaries).
FAULTS = ("BootDuringEnable", "BridgeBrownout", "Undervoltage", "Hardware", "DeviceTemp",
          "ProcTemp", "RemoteSensorDataInvalid", "FusedSensorOutOfSync", "MissingRemoteSensor",
          "MissingDifferentialFX", "OverSupplyV", "UnstableSupplyV", "UnlicensedFeatureInUse",
          "BadMagnet", "Undervoltage")
_RESTART = "Robot program starting"
_ERROR = re.compile(r"(Exception|Error at |ERROR|Unhandled|Traceback|Fatal|"
                    r"CAN frame not received|could not|failed to)", re.I)
_QUIET = re.compile(r"overrun|Tracer|NT: |Joystick Button|\[AdvantageKit\]|Shuffleboard|"
                    r"LiveWindow|SmartDashboard|persistent file", re.I)
REPEAT_S = 3.0              # the same event again within this is the same event, counted


# What the code printed, by what it means (the team's real consoles,
# 2026-11-03): a controller whose config never applied runs on defaults; a
# missing deploy file is an auto that can't run; a CAN read timing out every
# loop slows the loop; an exception is a bug.
_CLASSES = (
    ("config_failed", re.compile(r"failed config|config(?:uration)? failed|"
                                 r"Config.*(?:TxFailed|Timeout|Error)", re.I)),
    ("missing_file", re.compile(r"FileNotFound|FileInputStream\.open|No such file.*deploy|"
                                r"/deploy/\S+", re.I)),
    ("can_timeout", re.compile(r"CAN Receive has Timed Out|CAN frame not received|"
                               r"CAN.*timed out|TxFailed", re.I)),
    ("exception", re.compile(r"Exception|Traceback|Unhandled|Fatal|NullPointer", re.I)),
)
# Messages from tools on the robot that aren't the robot's problem (QuestNav's
# ADB watcher looking for a headset that isn't plugged in).
_NOISE = re.compile(r"no devices/emulators found|Network is unreachable|No route to host|"
                    r"adb not found", re.I)


def classify(line: str) -> str | None:
    if _NOISE.search(line):
        return None
    for kind, rx in _CLASSES:
        if rx.search(line):
            return kind
    return "code_error"


def _series(db, session_id: int, name: str, device_type: str = "Robot") -> list[int]:
    return [r["id"] for r in db.fetchall(
        """SELECT se.id FROM series se JOIN signal g ON g.id = se.signal_id
           JOIN device d ON d.id = se.device_id
           WHERE se.session_id = ? AND g.name = ? AND d.device_type = ?""",
        (session_id, name, device_type))]


def _points(db, series_id: int) -> list[tuple[float, float]]:
    return [(r["t_ms"] / 1000.0, r["v"]) for r in db.fetchall(
        "SELECT t_ms, v FROM samples.sample WHERE series_id = ? ORDER BY t_ms, ord",
        (series_id,)) if r["v"] is not None]


def _seconds(db, series_id: int, col: str = "v_max") -> list[tuple[int, float]]:
    return [(r["t_s"], r["v"]) for r in db.fetchall(
        f"SELECT t_s, {col} AS v FROM samples.sample_1s WHERE series_id = ? ORDER BY t_s",
        (series_id,)) if r["v"] is not None]


def _inside(t: float, windows) -> bool:
    return any(a <= t <= b for a, b in windows)


def _ev(t, kind, device="robot", value=None, unit="", detail="", **more) -> dict:
    return {"t_s": round(float(t), 3), "kind": kind, "device": device,
            "value": None if value is None else round(float(value), 4), "unit": unit,
            "detail": detail, "severity": SEVERITY.get(kind, "info"), **more}


def _rises(points, min_step: float = 0.5):
    """Each time a counter went up: (t, new value, increase)."""
    last = None
    for t, v in points:
        if last is not None and v - last >= min_step:
            yield t, v, v - last
        last = v


def _edges(points, on: bool):
    """Each time a 0/1 flag turned on (`on`) or off."""
    last = None
    for t, v in points:
        now = v >= 0.5
        if last is not None and now != last and now == on:
            yield t
        last = now


def _runs(seconds, test, min_len: int = 1):
    """Stretches of consecutive seconds where test(v) holds: (start, end, values)."""
    run: list[tuple[int, float]] = []
    for t, v in seconds:
        if test(v) and (not run or t == run[-1][0] + 1):
            run.append((t, v))
            continue
        if len(run) >= min_len:
            yield run[0][0], run[-1][0], [x for _t, x in run]
        run = [(t, v)] if test(v) else []
    if len(run) >= min_len:
        yield run[0][0], run[-1][0], [x for _t, x in run]


def _labels(db, series_id: int) -> list[tuple[float, str]]:
    sig = db.fetchone("SELECT signal_id FROM series WHERE id = ?", (series_id,))["signal_id"]
    names = {r["code"]: r["label"] for r in db.fetchall(
        "SELECT code, label FROM signal_enum WHERE signal_id = ?", (sig,))}
    return [(t, names.get(int(v), "")) for t, v in _points(db, series_id)]


# ── per kind of log ──────────────────────────────────────────────────────

def _robot(db, sid: int, windows) -> list[dict]:
    out: list[dict] = []

    def first(name):
        ids = _series(db, sid, name)
        return ids[0] if ids else None

    s = first("SystemStats/BrownedOut")
    if s:
        out += [_ev(t, "brownout", detail="the roboRIO browned out: it disabled motor outputs")
                for t in _edges(_points(db, s), True)]
    s = first("SystemStats/BatteryVoltage")
    if s:
        for a, b, vals in _runs(_seconds(db, s, "v_min"), lambda v: v < DIP_V):
            if _inside(a, windows):
                out.append(_ev(a, "voltage_dip", value=min(vals), unit="V", end_s=b,
                               detail=f"battery at {min(vals):.2f} V while enabled"))
    for name, kind, step, what in (
            ("SystemStats/CANBus/OffCount", "can_bus_off", 0.5, "the CAN bus went bus-off"),
            ("SystemStats/CANBus/TxFullCount", "can_tx_full", 1, "the roboRIO's CAN transmit "
             "buffer was full"),
            ("SystemStats/CANBus/ReceiveErrorCount", "can_errors", 20, "CAN receive errors rose"),
            ("SystemStats/CANBus/TransmitErrorCount", "can_errors", 20, "CAN transmit errors rose"),
            ("SystemStats/3v3Rail/CurrentFaults", "rail_fault", 0.5, "the 3.3 V rail faulted"),
            ("SystemStats/5vRail/CurrentFaults", "rail_fault", 0.5, "the 5 V rail faulted"),
            ("SystemStats/6vRail/CurrentFaults", "rail_fault", 0.5, "the 6 V rail faulted"),
            ("SystemStats/CommsDisableCount", "comms_disable", 0.5,
             "the roboRIO disabled itself for lost comms")):
        s = first(name)
        if s:
            out += [_ev(t, kind, value=inc, unit="", detail=f"{what} (+{inc:g}, {name})")
                    for t, _v, inc in _rises(_points(db, s), step)]
    s = first("SystemStats/CANBus/Utilization")
    if s:
        for a, b, vals in _runs(_seconds(db, s, "v_avg"), lambda v: v * (100 if v <= 1 else 1)
                                >= CAN_BUSY_PCT, 2):
            pct = max(vals) * (100 if max(vals) <= 1 else 1)
            out.append(_ev(a, "can_busy", value=pct, unit="%", end_s=b,
                           detail=f"CAN bus {pct:.0f}% busy for {b - a + 1} s"))
    s = first("RadioStatus/Connected")
    if s:
        out += [_ev(t, "radio_lost", detail="the robot radio reported disconnected")
                for t in _edges(_points(db, s), False)]
    s = first("DriverStation/EmergencyStop")
    if s:
        out += [_ev(t, "estop", detail="emergency stop") for t in _edges(_points(db, s), True)]
    s = first("PowerDistribution/Faults")
    if s:
        out += [_ev(t, "device_fault", device="PDH", value=v,
                    detail=f"the power distribution reported faults (bits {int(v)})")
                for t, v, _i in _rises(_points(db, s), 1)]
    # Disabled with match time left while the FMS was attached: not the match ending.
    en, fms, mt = first("DriverStation/Enabled"), first("DriverStation/FMSAttached"), \
        first("DriverStation/MatchTime")
    if en and fms:
        fms_pts, mt_pts = _points(db, fms), _points(db, mt) if mt else []

        def at(points, t):
            v = None
            for pt, pv in points:
                if pt > t:
                    break
                v = pv
            return v

        for t in _edges(_points(db, en), False):
            left = at(mt_pts, t - 0.05)
            if (at(fms_pts, t) or 0) >= 0.5 and left is not None and left > 1.0:
                out.append(_ev(t, "disabled_mid_match", value=left, unit="s",
                               detail=f"disabled with {left:.0f} s of match time left"))
    s = first("RealOutputs/LoggedRobot/GCTimeMS")
    if s:
        for t, v in _seconds(db, s):
            if v >= GC_MS and _inside(t, windows):
                out.append(_ev(t, "gc_pause", value=v, unit="ms",
                               detail=f"Java garbage collection took {v:.0f} ms"))
    s = first("RealOutputs/LoggedRobot/FullCycleMS")
    if s:
        for a, b, vals in _runs(_seconds(db, s), lambda v: v >= LOOP_STALL_MS):
            if _inside(a, [(x + 1, y) for x, y in windows]):
                out.append(_ev(a, "loop_stall", value=max(vals), unit="ms", end_s=b,
                               detail=f"a robot loop cycle took {max(vals):.0f} ms"))
    s = first("RealOutputs/Console")
    if s:
        starts = 0
        for t, text in _labels(db, s):
            if _RESTART in text:
                starts += 1
                if starts > 1:
                    out.append(_ev(t, "code_restart", detail="the robot program started again: "
                                   "the code crashed or was restarted"))
            line = next((ln.strip() for ln in text.splitlines()
                         if _ERROR.search(ln) and not _QUIET.search(ln)), None)
            if line:
                kind = classify(line)
                if kind:
                    out.append(_ev(t, kind, detail=line[:160]))
    return out


def _ds(db, sid: int, windows) -> list[dict]:
    out: list[dict] = []

    def first(name):
        ids = _series(db, sid, name)
        return ids[0] if ids else None

    for name, kind, what in (("DS/Brownout", "brownout", "the DS log shows a brownout"),
                             ("DS/Watchdog", "watchdog", "the DS log shows the watchdog "
                                                         "(the robot disabled for lack of "
                                                         "fresh control packets)")):
        s = first(name)
        if s:
            # While enabled (or ending it). A disabled robot draws next to
            # nothing: the DS's brownout flag while disabled is the battery
            # being unplugged, its voltage fading before comms go (every
            # power-off in the team's DS logs, 2026-11-05).
            out += [_in_match(_ev(t, kind, detail=what, source="ds"), windows)
                    for t in _edges(_points(db, s), True)]
    s = first("DS/Comms")
    if s:
        pts = _points(db, s)
        for t in _edges(pts, False):
            back = next((pt for pt, pv in pts if pt > t and pv >= 0.5), None)
            # It spans the gap (up to COMMS_SPAN_S), so what ended it, a fresh
            # code start after a reboot, is the same incident.
            more = {"end_s": min(back, t + COMMS_SPAN_S)} if back else {}
            out.append(_in_match(_ev(t, "comms_lost", value=(back - t) if back else None,
                                     unit="s", detail="the DS lost the robot" + (
                                         f" for {back - t:.1f} s" if back
                                         else " (it never came back)"), source="ds", **more),
                                 windows))
    s = first("DS/PacketLossPct")
    if s:
        for a, b, vals in _runs(_seconds(db, s, "v_avg"), lambda v: v >= PACKET_LOSS * 100):
            if _inside(a, windows):
                out.append(_ev(a, "packet_loss", value=max(vals), unit="%", end_s=b,
                               detail=f"up to {max(vals):.0f}% of packets lost", source="ds"))
    s = first("DS/TripTimeMS")
    if s:
        for a, b, vals in _runs(_seconds(db, s, "v_avg"), lambda v: v >= TRIP_MS):
            if _inside(a, windows):
                out.append(_ev(a, "high_trip_time", value=max(vals), unit="ms", end_s=b,
                               detail=f"round trip up to {max(vals):.0f} ms", source="ds"))
    s = first("DS/CANUtilPct")
    if s:
        # Each second's average: a single 20 ms record at 100% (they happen,
        # every log has some) isn't a busy bus. Real saturation never lasted
        # half a second in the team's 70 DS logs; normal is ~45%.
        for a, b, vals in _runs(_seconds(db, s, "v_avg"), lambda v: v >= CAN_BUSY_PCT, 2):
            out.append(_ev(a, "can_busy", value=max(vals), unit="%", end_s=b,
                           detail=f"CAN bus {max(vals):.0f}% busy (DS log)", source="ds"))
    s = first("DS/Events")
    status = _Status()
    if s:
        comms = first("DS/Comms")
        seen_at = next((pt for pt, pv in _points(db, comms) if pv >= 0.5), None) \
            if comms else None
        starts: list[float] = []
        pads: set[str] = set()
        for t, text in _labels(db, s):
            m = re.match(r"\[(\d+)\]\s*(.*)", text, re.S)
            code, body = (int(m.group(1)), m.group(2)) if m else (None, text)
            # After the DS first saw the robot: everything before is it booting.
            # (Not 44007: this team's DS never logs it, 2026-11-05.)
            seen_comms = seen_at is not None and t > seen_at
            if code == 44004:
                if seen_comms:          # one at DS start is normal
                    out.append(_in_match(_ev(t, "lost_comms_msg", detail=body[:160],
                                             source="ds"), windows))
                continue
            if code is None or "update Radio firmware" in body:
                status(t, body)             # the radio note comes with the DS's code
            if "Robot program starting" in body:
                starts.append(t)
            if code == 44007:
                up = (re.search(r"secondsSinceReboot\D{0,3}(\d+(?:\.\d+)?)", body)
                      or re.search(r"(\d+(?:\.\d+)?)\s*$", body.split("|")[-1].strip())
                      or re.search(r"(\d+(?:\.\d+)?)", body))
                if up and float(up.group(1)) < REBOOT_UPTIME_S and t > 30:
                    out.append(_ev(t, "roborio_reboot", value=float(up.group(1)), unit="s",
                                   detail=f"the roboRIO had been up only {up.group(1)} s",
                                   source="ds"))
            elif code == 44009 and _inside(t, windows):
                out.append(_ev(t, "joystick_unplugged", detail=body[:160], source="ds"))
            elif code == 44002 and seen_comms:
                out.append(_ping(t, body))
            elif code == 44008 and seen_comms:
                out.append(_ev(t, "radio_events", detail=body[:200], source="ds"))
            elif code == 44003 and seen_comms:
                # At every boot until the code is up: only news in a match.
                out.append(_in_match(_ev(t, "no_robot_code", detail=body[:160], source="ds"),
                                     windows))
            elif code == 44000 and "not keeping up" in body:
                out.append(_in_match(_ev(t, "ds_overloaded", detail=body[:160], source="ds"),
                                     windows))
            elif code == 44005:
                out.append(_in_match(_ev(t, "fms_lost", detail=body[:160], source="ds"),
                                     windows))
            elif code in _CAN_CODES:
                # Devices still booting say this too: in a match, it's news.
                out.append(_in_match(_ev(t, _CAN_CODES[code], device=_ds_device(body),
                                         detail=body.split(" | ")[0][:160], source="ds"),
                                     windows))
            elif code in (None, 0, 1) and seen_comms:
                pad = re.search(r"Joystick Button \d+ on port (\d+) not available", body)
                if pad:
                    if pad.group(1) not in pads:  # printed every loop: once per port
                        pads.add(pad.group(1))
                        out.append(_ev(t, "joystick_missing", detail=body.split(" | ")[0][:160],
                                       source="ds"))
                    continue
                line = body.split(" | ")[0]
                if _ERROR.search(line) and not _QUIET.search(line):
                    kind = classify(line)
                    if kind:
                        out.append(_ev(t, kind, detail=line[:160], source="ds"))
        out += _starts(starts, out, windows)
        out += status.done()
    key = (db.fetchone("SELECT match_key FROM log_session WHERE id = ?", (sid,))
           or {"match_key": None})["match_key"]
    if key:                            # on the field: every disable but the planned one
        out += _lost_time(windows)
    return out


_CAN_CODES = {1000: "can_timeout", 1003: "can_timeout", 1154: "can_timeout",
              1001: "can_tx_full"}     # Phoenix / HAL codes in the DS's messages


def _ds_device(body: str) -> str:
    """'talon fx 15 ("") Status Signal …' in a message's location → 'TalonFX 15'."""
    m = re.search(r"(talon ?fx|cancoder|pigeon ?2|talon ?fxs|candi|canrange)\s*(\d+)",
                  body, re.I)
    if not m:
        return "robot"
    names = {"talonfx": "TalonFX", "cancoder": "CANcoder", "pigeon2": "Pigeon2",
             "talonfxs": "TalonFXS", "candi": "CANdi", "canrange": "CANrange"}
    kind = names.get(m.group(1).lower().replace(" ", ""), m.group(1))
    return f"{kind} {m.group(2)}"


def _ping(t: float, body: str) -> dict:
    """The DS's ping results, by which hop failed: a CSA's first split of a
    comms loss. DS radio bad: the DS side or the field; robot radio bad: the
    robot's radio (power, or the wireless link); roboRIO bad with the robot
    radio good: the radio-to-roboRIO Ethernet, or the roboRIO itself."""
    bad = lambda name: re.search(name + r"\([^)]*\)-bad", body, re.I) is not None  # noqa: E731
    kind = ("ping_ds_radio_bad" if bad("DS radio") else
            "ping_radio_bad" if bad("robot radio") else
            "ping_rio_bad" if bad("roboRIO") else "ping_status")
    return _ev(t, kind, detail=body[:200], source="ds")


def _starts(starts: list[float], evts: list[dict], windows) -> list[dict]:
    """"Robot program starting" read against the comms. After a long comms
    gap it's the roboRIO booting again (`roborio_reboot`: the team's qm2,
    comms gone 58 s and 61 s, each ending in a fresh start); without one,
    inside the match, the code crashed and restarted (`code_restart`). The
    first start, before any enable, is the robot being switched on."""
    out = []
    first = min((a for a, _ in windows), default=None)
    last = max((b for _, b in windows), default=None)
    for t in starts:
        if first is None or not (first <= t <= last + COMMS_GRACE_S):
            continue
        gap = next((e for e in reversed(evts) if e["kind"] == "comms_lost"
                    and e["t_s"] < t and t - e["t_s"] <= (e.get("value") or 0) + 15), None)
        if gap and (gap.get("value") or 0) >= REBOOT_GAP_S:
            out.append(_ev(t, "roborio_reboot", value=gap["value"], unit="s", source="ds",
                           detail=f"comms gone {gap['value']:.0f} s, then the robot code "
                                  "started from scratch: the roboRIO rebooted"))
        else:
            out.append(_ev(t, "code_restart", source="ds",
                           detail="the robot code started again mid-match"))
    return out


def _lost_time(windows) -> list[dict]:
    """In an FMS match, the robot disabled between enables for longer than
    the field's own auto-to-teleop gap (3 s): match time lost. The team's DS
    logs: qm4 lost 13 s of auto with comms up the whole time, qm6 2.5 s."""
    out = []
    ws = sorted(windows)
    for (_a, end), (start, _b) in zip(ws, ws[1:]):
        gap = start - end
        if gap > FIELD_GAP_S:
            out.append(_ev(end, "disabled_mid_match", value=round(gap, 1), unit="s",
                           end_s=start, source="ds",
                           detail=f"disabled for {gap:.1f} s mid-match (the field's own gap "
                                  f"between auto and teleop is 3 s)"))
    return out


def _in_match(e: dict, windows) -> dict:
    """A comms loss while enabled (or one that ended the enable) keeps its
    severity; one while the robot sat disabled is `info`: switched off in
    the pit or the queue, which incidents leave out."""
    t = e["t_s"]
    if not any(a <= t <= b + COMMS_GRACE_S for a, b in windows):
        e["severity"] = "info"
    return e


class _Status:
    """The roboRIO's and the DS's own lines in `.dsevents` (no code): free
    memory, a console line printed over and over, the radio firmware note."""

    def __init__(self):
        self.low: list[tuple[float, int]] = []      # (t, MB) under LOW_MEMORY_MB
        self.lines: dict[str, list] = {}            # normalised line → [first t, last t, n, text]
        self.firmware: tuple[float, str] | None = None

    def __call__(self, t: float, body: str) -> None:
        m = re.search(r"Disk Free (\d+) MB, Memory Free (\d+) MB", body)
        if m:
            disk, mem = int(m.group(1)), int(m.group(2))
            if mem < LOW_MEMORY_MB and disk > 0:     # both 0: no report, not no memory
                self.low.append((t, mem))
            return
        if "update Radio firmware" in body:
            if self.firmware is None:
                self.firmware = (t, body[:160])
            return
        m = re.match(r"\[repeated (\d+) times over (\d+) s\] (.*)", body, re.S)
        if m:                       # dslog.repeats(): counted where every line was seen
            n, span, text = int(m.group(1)), float(m.group(2)), m.group(3)
            self.lines[text] = [t, t + span, n, text[:120]]

    def done(self) -> list[dict]:
        out = []
        runs: list[list[tuple[float, int]]] = []
        for t, mb in self.low:
            if runs and t - runs[-1][-1][0] <= 30:   # reports come every ~10 s
                runs[-1].append((t, mb))
            else:
                runs.append([(t, mb)])
        for run in runs:
            low = min(mb for _, mb in run)
            # A moment, not a span: an event lasting minutes would pull every
            # other event of the match into its incident (incidents.GAP_S).
            out.append(_ev(run[0][0], "low_memory", value=low, unit="MB", source="ds",
                           detail=f"the roboRIO had {low} MB of memory free (usually 150-220)"))
        if self.firmware:
            out.append(_ev(self.firmware[0], "radio_firmware", detail=self.firmware[1],
                           source="ds"))
        for first, last, n, text in self.lines.values():
            per_min = n / max((last - first) / 60.0, 1.0)
            if n >= SPAM_LINES and per_min >= SPAM_PER_MIN:
                out.append(_ev(first, "console_spam", value=n, unit="lines",
                               source="ds", detail=f"printed {n} times ({per_min:.0f} a "
                               f"minute): {text}"))
        return out


def _motors(db, sid: int, windows, labels) -> list[dict]:
    out: list[dict] = []
    devs = db.fetchall("""SELECT DISTINCT d.id, d.device_type, d.can_id, d.label FROM device d
                          JOIN series se ON se.device_id = d.id
                          WHERE se.session_id = ? AND d.device_type <> 'Robot'""", (sid,))
    for d in devs:
        name = d["label"] or f"{d['device_type']} {d['can_id']}"
        ids = {r["name"]: r["id"] for r in db.fetchall(
            """SELECT g.name, se.id FROM series se JOIN signal g ON g.id = se.signal_id
               WHERE se.session_id = ? AND se.device_id = ?""", (sid, d["id"]))}
        if "ResetCount" in ids:
            out += [_ev(t, "device_reset", device=name, value=v,
                        detail=f"{name} rebooted (ResetCount {v:g})")
                    for t, v, _i in _rises(_points(db, ids["ResetCount"]), 0.5)]
        if "SupplyVoltage" in ids:
            for a, b, vals in _runs(_seconds(db, ids["SupplyVoltage"], "v_min"),
                                    lambda v: 0.5 < v < DIP_V):
                if _inside(a, windows):
                    out.append(_ev(a, "device_voltage_dip", device=name, value=min(vals),
                                   unit="V", end_s=b,
                                   detail=f"{name}'s supply fell to {min(vals):.2f} V"))
        if "StatorCurrent" in ids and "RotorVelocity" in ids:
            cur = dict(_seconds(db, ids["StatorCurrent"], "v_avg"))
            vel = dict(_seconds(db, ids["RotorVelocity"], "v_avg"))
            secs = [(t, abs(cur[t])) for t in sorted(cur) if t in vel
                    and abs(vel[t]) < STALL_RPS and _inside(t, windows)]
            for a, b, vals in _runs(secs, lambda v: v >= STALL_A, STALL_S):
                out.append(_ev(a, "motor_stall", device=name, value=max(vals), unit="A",
                               end_s=b, detail=f"{name} pushed {max(vals):.0f} A without "
                                               f"turning for {b - a + 1} s"))
    for r in db.fetchall(
            """SELECT f.t_ms_start, g.name, d.label, d.device_type, d.can_id
               FROM fault_event f JOIN signal g ON g.id = f.signal_id
               JOIN device d ON d.id = f.device_id
               WHERE f.session_id = ? AND f.sticky = 0""", (sid,)):
        fault = r["name"].replace("Fault_", "")
        if fault not in FAULTS:
            continue
        name = r["label"] or f"{r['device_type']} {r['can_id']}"
        out.append(_ev(r["t_ms_start"] / 1000.0, "device_fault", device=name, fault=fault,
                       detail=f"{name} raised {fault}"))
    return out


def find(db, session_id: int, windows) -> list[dict]:
    """Every event in one log, by time. `windows`: its enabled (start_s, end_s)."""
    kind = (db.fetchone("SELECT source_kind FROM log_session WHERE id = ?", (session_id,))
            or {"source_kind": ""})["source_kind"]
    out: list[dict] = []
    if kind == "dslog":
        out += _ds(db, session_id, windows)
    else:
        out += _robot(db, session_id, windows)
        out += _motors(db, session_id, windows, None)
    out.sort(key=lambda e: e["t_s"])
    return collapse(out)


def collapse(evts: list[dict]) -> list[dict]:
    """One event per burst: the same kind on the same device again within
    `REPEAT_S` of the last is counted on the first (`count`, `end_s`). A
    BridgeBrownout re-raised every second made 1,876 events in 28 real logs."""
    out: list[dict] = []
    open_: dict[tuple, dict] = {}
    for e in evts:
        key = (e["kind"], e["device"], e.get("fault"))
        last = open_.get(key)
        if last is not None and e["t_s"] - last.get("end_s", last["t_s"]) <= REPEAT_S:
            last["count"] = last.get("count", 1) + 1
            last["end_s"] = max(last.get("end_s", last["t_s"]), e.get("end_s", e["t_s"]))
            if e["value"] is not None and last["value"] is not None and e.get("unit") == "V":
                last["value"] = min(last["value"], e["value"])
            elif e["value"] is not None and last["value"] is not None:
                last["value"] = max(last["value"], e["value"])
            continue
        e = dict(e)
        open_[key] = e
        out.append(e)
    return out


def summary(evts: list[dict]) -> dict[str, Any]:
    by: dict[str, int] = {}
    for e in evts:
        by[e["kind"]] = by.get(e["kind"], 0) + 1
    return by
