"""
What the pit needs to know from the last log, in one screen's worth of numbers.

`fun_facts.py` is for visitors — one number that makes someone say "wait,
really?". **This is the opposite**: it is for the crew between matches, and
every row is something you would act on. Two consumers, one database.

## Where "OK" and "fault" come from

**The robot's own latched faults, not thresholds invented here.** A Talon knows
its own temperature limit and its own current limit, and it sets
`StickyFault_DeviceTemp` / `StickyFault_StatorCurrLimit` / `StickyFault_
BridgeBrownout` when it crosses one. Reading those is honest; picking a number
out of the air and colouring a tile red because a motor hit it is not, and in a
pit it is worse than useless — it teaches the crew to ignore the colour.

Three thresholds *are* used, and each is a published figure rather than a
preference, named where it appears: the **6.8 V** roboRIO brownout floor, the
**20 ms** WPILib loop period, and **100%** CAN bus utilisation.

## Reading both sources

A `.hoot` gives per-motor Phoenix signals (`DeviceTemp`, `StatorCurrent`,
`SupplyVoltage`); an AdvantageKit `.wpilog` gives the team's own subsystem
signals (`RealOutputs/Shooter Lead Stator Motor Current`, `… Temp F`) and the
roboRIO's `SystemStats`. **Neither is required.** Every function returns what it
can find and an empty list otherwise, because on any given day the crew may have
pulled one file and not the other.

Everything here reads `series`, `session_constant` and `fault_event` — the
rolled-up columns — and never touches `samples.sample`. Measured under 2 ms for
the whole dashboard.
"""

from dataclasses import dataclass, field

from app.db import db
from app.robot.wpilog import APP_DEVICE_TYPE

# ── Published figures, not preferences ───────────────────────────────────
BROWNOUT_V = 6.8        # roboRIO 2 brownout floor (WPILib docs)
LOOP_PERIOD_MS = 20.0   # TimedRobot default period
CAN_FULL_PCT = 100.0

# Faults worth pulling to the top of a pit screen, in the order a crew would
# work through them. Anything latched but not listed still appears, below these.
FAULT_PRIORITY = [
    "StickyFault_Hardware",
    "StickyFault_BridgeBrownout",
    "StickyFault_DeviceTemp",
    "StickyFault_ProcTemp",
    "StickyFault_StatorCurrLimit",
    "StickyFault_SupplyCurrLimit",
    "StickyFault_OverSupplyV",
    "StickyFault_BootDuringEnable",
    "StickyFault_FusedSensorOutOfSync",
    "StickyFault_RemoteSensorDataInvalid",
]

# How the team's AdvantageKit signals are named. The subsystem is whatever comes
# before the suffix, so this reads the names the robot code actually writes
# instead of hardcoding a subsystem list that goes stale every build season.
_APP_SUFFIXES = {
    " Stator Motor Current": "stator_a",
    " Supply Motor Current": "supply_a",
    " PDH Current":          "pdh_a",
    " Temp F":               "temp_f",
}

OK, WARN, FAULT, IDLE = "ok", "warn", "fault", "idle"


@dataclass
class Reading:
    """One tile: a label, a number, and whether anyone should care."""

    label: str
    value: str
    unit: str = ""
    status: str = IDLE
    detail: str = ""
    # The figure's shape across the match, for the tile's trace. A sag that
    # dipped once reads differently from one that sat low all match — that is
    # the difference between "carry on" and "change the battery", and a single
    # number cannot say it. Empty when the signal has no per-second rollup.
    shape: tuple[float, ...] = field(default_factory=tuple)


@dataclass
class Subsystem:
    """One mechanism, as the robot code names it."""

    name: str
    stator_a: float | None = None
    supply_a: float | None = None
    pdh_a: float | None = None
    temp_f: float | None = None
    status: str = IDLE
    note: str = ""


@dataclass
class MotorRow:
    """One CAN device, named if anyone has named it."""

    label: str
    can_id: int
    device_type: str
    temp_c: float | None = None
    stator_a: float | None = None
    supply_v: float | None = None
    rps: float | None = None
    faults: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f in ("StickyFault_Hardware", "StickyFault_DeviceTemp")
               for f in self.faults):
            return FAULT
        return WARN if self.faults else OK


@dataclass
class Dashboard:
    session_id: int | None = None
    source_name: str = ""
    started_at: str = ""
    match_key: str = ""
    duration_s: float = 0.0
    source_kind: str = ""
    # The pit usually pulls two files off one match — the controller's `.hoot`
    # and the roboRIO's `.wpilog` — and each holds half the picture. The board
    # reads the newest of *each* and names both, rather than showing whichever
    # was imported last and silently omitting the other half.
    sources: list[str] = field(default_factory=list)
    vitals: list[Reading] = field(default_factory=list)
    subsystems: list[Subsystem] = field(default_factory=list)
    motors: list[MotorRow] = field(default_factory=list)
    faults: list[Reading] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.session_id is None

    @property
    def worst(self) -> str:
        """The single worst status on the board — drives the header."""
        every = ([r.status for r in self.vitals] + [s.status for s in self.subsystems]
                 + [m.status for m in self.motors] + [f.status for f in self.faults])
        for level in (FAULT, WARN, OK):
            if level in every:
                return level
        return IDLE


# ── Session ──────────────────────────────────────────────────────────────

def latest_session_id() -> int | None:
    row = db.fetchone(
        """SELECT id FROM log_session WHERE raw_rows IS NOT NULL
           ORDER BY imported_at DESC LIMIT 1""")
    return row["id"] if row else None


def latest_motor_session() -> int | None:
    """Newest session that actually carries per-motor Phoenix signals."""
    row = db.fetchone(
        """SELECT se.session_id id FROM series se
           JOIN device d ON d.id = se.device_id
           WHERE d.can_id >= 0
           ORDER BY se.session_id DESC LIMIT 1""")
    return row["id"] if row else None


def latest_app_session() -> int | None:
    """
    Newest session carrying the team's own application signals.

    Matched on the signal *paths*, not on the `Robot` pseudo-device: owlet emits
    a handful of non-CAN entries too (`RobotMode`, `AllianceStation`), so a hoot
    also lands rows on that device and would win this query while having no
    subsystem data at all.
    """
    row = db.fetchone(
        """SELECT se.session_id id FROM series se
           JOIN signal s ON s.id = se.signal_id
           WHERE s.device_type = ?
             AND (s.name LIKE 'RealOutputs/%' OR s.name LIKE 'SystemStats/%'
                  OR s.name LIKE 'PowerDistribution/%')
           ORDER BY se.session_id DESC LIMIT 1""",
        (APP_DEVICE_TYPE,))
    return row["id"] if row else None


def _num(session_id: int, name: str, col: str = "v_max") -> float | None:
    """One rolled-up figure for a signal, across every device that reports it."""
    agg = "MIN" if col == "v_min" else "MAX"
    row = db.fetchone(
        f"""SELECT {agg}(se.{col}) x FROM series se
            JOIN signal s ON s.id = se.signal_id
            WHERE se.session_id = ? AND s.name = ?""", (session_id, name))
    return row["x"] if row and row["x"] is not None else None


def _mean(session_id: int, name: str) -> float | None:
    """
    The typical value, not the worst one.

    Loop time and CAN utilisation both spike hard on the first cycle after boot
    and again out of a disable — a 10-second `FullCycleMS` is real, and it is
    also the single least informative number in the log. The tile carries the
    average and names the peak underneath it.
    """
    row = db.fetchone(
        """SELECT AVG(se.v_mean) x FROM series se
           JOIN signal s ON s.id = se.signal_id
           WHERE se.session_id = ? AND s.name = ?""", (session_id, name))
    return row["x"] if row and row["x"] is not None else None


# How many points a tile trace carries. Enough to show a dip, few enough that
# a 55" panel draws it as a line rather than a smear.
SHAPE_POINTS = 52


def shape(session_id: int, name: str, col: str = "v_max",
          points: int = SHAPE_POINTS) -> tuple[float, ...]:
    """
    The signal's shape across the session, as `points` evenly spaced values.

    Read from `sample_1s`, never from `sample`: the per-second rollup is what it
    is for, and a full-resolution read of a nine-minute log to draw a 52-point
    line would be several hundred thousand rows for something two centimetres
    wide. The aggregate matches the tile's own figure — a tile showing the
    lowest value traces the lows — so the number and the line agree.
    """
    agg = {"v_min": "MIN(r.v_min)", "v_max": "MAX(r.v_max)"}.get(
        col, "AVG(r.v_avg)")
    rows = db.fetchall(
        f"""SELECT r.t_s ts, {agg} v FROM samples.sample_1s r
            JOIN series se ON se.id = r.series_id
            JOIN signal s ON s.id = se.signal_id
            WHERE se.session_id = ? AND s.name = ?
            GROUP BY r.t_s ORDER BY r.t_s""", (session_id, name))
    values = [r["v"] for r in rows if r["v"] is not None]
    if len(values) < 2:
        return ()
    if len(values) <= points:
        return tuple(values)
    # Even decimation rather than averaging: the board is showing the shape of
    # the worst case, and averaging buckets would flatten the dip that matters.
    step = (len(values) - 1) / (points - 1)
    return tuple(values[int(round(i * step))] for i in range(points))


def _fmt(v: float | None, places: int = 1) -> str:
    if v is None:
        return "—"
    if v == 0:
        v = 0.0          # a measured -0.0 must not render as "-0.0"
    return f"{v:,.{places}f}"


# ── The rows ─────────────────────────────────────────────────────────────

def vitals(session_id: int) -> list[Reading]:
    """Battery, power, bus and loop — the things that end a match."""
    out: list[Reading] = []

    # Battery. `SupplyVoltage` is the Phoenix per-motor reading; `BatteryVoltage`
    # is the roboRIO's. Either answers the question, so take whichever exists.
    sag = _num(session_id, "SupplyVoltage", "v_min")
    if sag is None:
        sag = _num(session_id, "SystemStats/BatteryVoltage", "v_min")
    rest = _num(session_id, "SupplyVoltage") or _num(
        session_id, "SystemStats/BatteryVoltage")
    if sag is not None:
        # Brownout is the published roboRIO floor; "close to it" is the metric
        # that matters in a pit, so the warn band is the volt above it.
        status = (FAULT if sag <= BROWNOUT_V
                  else WARN if sag <= BROWNOUT_V + 1.0 else OK)
        out.append(Reading("Battery sag", _fmt(sag, 2), "V", status,
                           f"rest {_fmt(rest, 2)} V · brownout at {BROWNOUT_V} V",
                           shape(session_id, "SupplyVoltage", "v_min")
                           or shape(session_id, "SystemStats/BatteryVoltage",
                                    "v_min")))

    brown = _num(session_id, "SystemStats/BrownedOut")
    if brown is not None:
        out.append(Reading("Browned out", "YES" if brown > 0 else "no", "",
                           FAULT if brown > 0 else OK,
                           "roboRIO dropped below the brownout floor"))

    peak_a = _num(session_id, "StatorCurrent")
    if peak_a is not None:
        out.append(Reading("Peak motor current", _fmt(peak_a, 0), "A", IDLE,
                           "highest stator current on any one motor",
                           shape(session_id, "StatorCurrent")))

    total_a = _num(session_id, "RealOutputs/Robot Total Current Amps")
    if total_a is None:
        total_a = _num(session_id, "PowerDistribution/TotalCurrent")
    if total_a is not None:
        out.append(Reading("Peak total draw", _fmt(total_a, 0), "A", IDLE,
                           "every channel on the PDH at once",
                           shape(session_id,
                                 "RealOutputs/Robot Total Current Amps")
                           or shape(session_id,
                                    "PowerDistribution/TotalCurrent")))

    watts = _num(session_id, "RealOutputs/Robot Total Power Watts")
    if watts is not None:
        out.append(Reading("Peak power", _fmt(watts / 1000, 1), "kW", IDLE))

    hot_c = _num(session_id, "DeviceTemp")
    if hot_c is not None:
        out.append(Reading("Hottest motor", _fmt(hot_c, 0), "°C", IDLE,
                           f"{_fmt(hot_c * 9 / 5 + 32, 0)} °F",
                           shape(session_id, "DeviceTemp")))

    can = _mean(session_id, "SystemStats/CANBus/Utilization")
    can_hi = _num(session_id, "SystemStats/CANBus/Utilization")
    if can is not None:
        scale = 100 if (can_hi or can) <= 1.0 else 1
        pct, hi = can * scale, (can_hi or can) * scale
        out.append(Reading("CAN bus", _fmt(pct, 0), "%",
                           WARN if pct >= 70 else OK,
                           f"average · peaked at {_fmt(hi, 0)}%",
                           shape(session_id,
                                 "SystemStats/CANBus/Utilization", "v_avg")))

    errs = 0.0
    for n in ("SystemStats/CANBus/ReceiveErrorCount",
              "SystemStats/CANBus/TransmitErrorCount",
              "SystemStats/CANBus/OffCount"):
        errs += _num(session_id, n) or 0.0
    if errs:
        out.append(Reading("CAN errors", _fmt(errs, 0), "", WARN,
                           "receive + transmit + bus-off"))

    loop = _mean(session_id, "RealOutputs/LoggedRobot/FullCycleMS")
    loop_hi = _num(session_id, "RealOutputs/LoggedRobot/FullCycleMS")
    if loop is not None:
        out.append(Reading("Loop time", _fmt(loop, 1), "ms",
                           WARN if loop > LOOP_PERIOD_MS else OK,
                           f"average of a {LOOP_PERIOD_MS:.0f} ms budget · "
                           f"worst {_fmt(loop_hi, 0)} ms",
                           shape(session_id,
                                 "RealOutputs/LoggedRobot/FullCycleMS",
                                 "v_avg")))

    cpu = _num(session_id, "SystemStats/CPUTempCelsius")
    if cpu is not None:
        out.append(Reading("roboRIO CPU", _fmt(cpu, 0), "°C", IDLE))

    return out


def subsystems(session_id: int) -> list[Subsystem]:
    """
    The team's own mechanisms, grouped out of the AdvantageKit signal names.

    Driven by the names the robot code writes rather than a hardcoded list, so
    renaming a mechanism in robot code renames it here with no edit.
    """
    found: dict[str, Subsystem] = {}
    for r in db.fetchall(
            """SELECT s.name, se.v_max, se.v_min FROM series se
               JOIN signal s ON s.id = se.signal_id
               WHERE se.session_id = ? AND s.name LIKE 'RealOutputs/%'""",
            (session_id,)):
        name = r["name"][len("RealOutputs/"):]
        for suffix, field_name in _APP_SUFFIXES.items():
            if name.endswith(suffix):
                sub = name[: -len(suffix)].strip()
                if not sub or "/" in sub:
                    break
                row = found.setdefault(sub, Subsystem(sub))
                setattr(row, field_name, r["v_max"])
                break

    # Status from the robot's own words: a mechanism whose motor latched a
    # thermal or current fault is flagged, nothing else is.
    flagged = _flagged_subsystems(session_id)
    for sub in found.values():
        if sub.name in flagged:
            sub.status, sub.note = WARN, flagged[sub.name]
        elif any(v is not None for v in (sub.stator_a, sub.temp_f)):
            sub.status = OK
    return sorted(found.values(), key=lambda s: s.name)


def _flagged_subsystems(session_id: int) -> dict[str, str]:
    """Subsystem name → why, for devices carrying a latched fault."""
    out: dict[str, str] = {}
    for r in db.fetchall(
            """SELECT DISTINCT d.subsystem, s.name FROM fault_event f
               JOIN device d ON d.id = f.device_id
               JOIN signal s ON s.id = f.signal_id
               WHERE f.session_id = ? AND f.sticky = 1
                 AND d.subsystem IS NOT NULL AND d.subsystem != ''""",
            (session_id,)):
        out.setdefault(r["subsystem"], r["name"].replace("StickyFault_", ""))
    return out


def motors(session_id: int) -> list[MotorRow]:
    """Per-CAN-device vitals, worst first."""
    rows: dict[int, MotorRow] = {}
    for r in db.fetchall(
            """SELECT d.id, d.device_type, d.can_id, d.label, s.name, se.v_max, se.v_min
               FROM series se JOIN signal s ON s.id = se.signal_id
               JOIN device d ON d.id = se.device_id
               WHERE se.session_id = ? AND d.can_id >= 0
                 AND s.name IN ('DeviceTemp','StatorCurrent','SupplyVoltage','Velocity')""",
            (session_id,)):
        row = rows.setdefault(r["id"], MotorRow(
            r["label"] or f"{r['device_type']} {r['can_id']}",
            r["can_id"], r["device_type"]))
        if r["name"] == "DeviceTemp":
            row.temp_c = r["v_max"]
        elif r["name"] == "StatorCurrent":
            row.stator_a = r["v_max"]
        elif r["name"] == "SupplyVoltage":
            row.supply_v = r["v_min"]
        elif r["name"] == "Velocity":
            row.rps = max(abs(r["v_max"] or 0), abs(r["v_min"] or 0))

    for r in db.fetchall(
            """SELECT f.device_id, s.name FROM fault_event f
               JOIN signal s ON s.id = f.signal_id
               WHERE f.session_id = ? AND f.sticky = 1""", (session_id,)):
        if r["device_id"] in rows:
            rows[r["device_id"]].faults.append(r["name"])

    order = {FAULT: 0, WARN: 1, OK: 2, IDLE: 3}
    return sorted(rows.values(),
                  key=lambda m: (order[m.status], -(m.temp_c or 0), m.can_id))


def faults(session_id: int) -> list[Reading]:
    """Latched faults, grouped by name, the ones a crew acts on first."""
    rows = db.fetchall(
        """SELECT s.name, COUNT(DISTINCT f.device_id) n,
                  GROUP_CONCAT(DISTINCT COALESCE(d.label, d.device_type || ' ' || d.can_id)) who
           FROM fault_event f JOIN signal s ON s.id = f.signal_id
           JOIN device d ON d.id = f.device_id
           WHERE f.session_id = ? AND f.sticky = 1
           GROUP BY s.name""", (session_id,))

    def rank(name: str) -> int:
        return FAULT_PRIORITY.index(name) if name in FAULT_PRIORITY else 99

    out = []
    for r in sorted(rows, key=lambda x: (rank(x["name"]), -x["n"])):
        pretty = r["name"].replace("StickyFault_", "")
        pretty = "".join(f" {c}" if c.isupper() and i else c
                         for i, c in enumerate(pretty)).strip()
        out.append(Reading(pretty, str(r["n"]),
                           "motor" if r["n"] == 1 else "motors",
                           FAULT if rank(r["name"]) < 4 else WARN,
                           (r["who"] or "").replace(",", ", ")))
    return out


def _name_of(session_id: int | None) -> str:
    if session_id is None:
        return ""
    row = db.fetchone("SELECT source_name FROM log_session WHERE id = ?",
                      (session_id,))
    return row["source_name"] if row else ""


def dashboard(session_id: int | None = None) -> Dashboard:
    """
    Everything the two overlays render. One call, one screen.

    With no argument it assembles the newest of each kind — motors from the
    latest log that has them, subsystems from the latest that has those — so a
    crew that imported both files sees both halves. Pass a `session_id` to pin
    the whole board to one log instead.
    """
    if session_id is not None:
        motor_sid = app_sid = head_sid = session_id
    else:
        head_sid = latest_session_id()
        motor_sid = latest_motor_session()
        app_sid = latest_app_session()
    if head_sid is None:
        return Dashboard()

    s = db.fetchone(
        """SELECT source_name, started_at, match_key, duration_s, source_kind
           FROM log_session WHERE id = ?""", (head_sid,))
    if s is None:
        return Dashboard()

    # Vitals come from both halves: the roboRIO's SystemStats live in the
    # wpilog, per-motor voltage and temperature in the hoot. Whichever is
    # missing simply contributes nothing.
    rows: list[Reading] = []
    seen: set[str] = set()
    for sid in dict.fromkeys(x for x in (app_sid, motor_sid, head_sid)
                             if x is not None):
        for r in vitals(sid):
            if r.label not in seen:
                seen.add(r.label)
                rows.append(r)

    names = [n for n in dict.fromkeys(
        _name_of(x) for x in (motor_sid, app_sid) if x is not None) if n]
    return Dashboard(
        session_id=head_sid, source_name=s["source_name"],
        started_at=s["started_at"] or "", match_key=s["match_key"] or "",
        duration_s=s["duration_s"] or 0.0, source_kind=s["source_kind"] or "",
        sources=names, vitals=rows,
        subsystems=subsystems(app_sid) if app_sid else [],
        motors=motors(motor_sid) if motor_sid else [],
        faults=faults(motor_sid) if motor_sid else [],
    )
