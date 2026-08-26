"""
Grammar for CTRE Phoenix 6 "detailed" hoot exports.

Pure functions over strings — no database, no Qt, no I/O — so the format can be
exercised without importing anything.

Every line of the export is one sample:

    [0.685s] ('TalonFX', '2', 'MotorVoltage') = 0.0

The only other line in a real 62-million-row file is the `--- All Log Records ---`
header.

Two properties of this format that the storage layer has to respect:

* **Timestamps are milliseconds.** Exactly three decimal places. (The
  microsecond-looking numbers in the file are values of the device's own
  `Timestamp` *signal*, which is a different thing and is discarded.)
* **Timestamps collide.** One signal can report two different values in the same
  millisecond — measured at 40,153 rows per session, 0.065%. Any storage keyed
  on (signal, timestamp) alone silently loses them.
"""

import re
from dataclasses import dataclass

LINE_RE = re.compile(r"^\[([0-9.]+)s\] \('([^']*)', '([^']*)', '([^']*)'\) = (.*)$")

HEADER_RE = re.compile(r"^---.*---\s*$")

# Three filename shapes reach this app, and each carries something the file
# itself does not. Tried in this order; the first match wins.
#
#   Phoenix hoot     06C94CC6…_2026-07-29_14-28-18.hoot     — device serial
#   DataLogManager   FRC_20260729_142818_TNKN_Q15.wpilog     — event + match
#   named, 4-digit    rio_2026-08-17_03-42-54.hoot           — start time
#   AdvantageKit     akit_26-08-17_02-43-54.wpilog           — start time
FILENAME_RE = re.compile(
    r"^(?P<serial>[0-9A-Fa-f]{8,})_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<time>\d{2}-\d{2}-\d{2})"
)

AKIT_RE = re.compile(
    r"^(?P<who>[A-Za-z0-9]+)_"
    r"(?P<y>\d{2})-(?P<mo>\d{2})-(?P<d>\d{2})_"
    r"(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})"
    r"(?:_(?P<event>[A-Za-z0-9]+))?"
    r"(?:_(?P<mtype>qf|sf|q|f|e|p)(?P<mnum>\d+))?",
    re.IGNORECASE,
)

# Same shape, four-digit year: 'rio_2026-08-17_03-42-54.hoot'. A separate
# pattern rather than making the year `\d{2,4}`, because `\d{2,4}` is greedy and
# would eat '2026-0' out of a two-digit-year name before backtracking somewhere
# plausible-looking and wrong.
NAMED_RE = re.compile(
    r"^(?P<who>[A-Za-z0-9]+)_"
    r"(?P<Y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})_"
    r"(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})"
    r"(?:_(?P<event>[A-Za-z0-9]+))?"
    r"(?:_(?P<mtype>qf|sf|q|f|e|p)(?P<mnum>\d+))?",
    re.IGNORECASE,
)

DLM_RE = re.compile(
    r"^FRC_"
    r"(?P<Y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})_"
    r"(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})"
    r"(?:_(?P<event>[A-Za-z0-9]+))?"
    r"(?:_(?P<mtype>QF|SF|Q|F|E|P)(?P<mnum>\d+))?",
    re.IGNORECASE,
)

# Short match codes as the operator writes them on the whiteboard.
MATCH_TYPES = {"q": "qm", "qf": "qf", "sf": "sf", "f": "f", "e": "e", "p": "p"}

# The device's own clock. Every device on the bus reports it, so a 10-motor log
# carries ten near-identical copies — 7.4% of every file measured. **One is
# kept, the rest are dropped**: the clock itself is a real diagnostic (the gap
# between a device's clock and the log's own timestamp is CAN latency and drift),
# but the tenth copy of it says nothing the first did not.
CLOCK_SIGNALS = {"Timestamp"}


@dataclass(frozen=True)
class Sample:
    t_ms: int
    device_type: str
    can_id: int
    signal: str
    raw: str


def classify(signal: str) -> str:
    """
    Storage class for a signal name. Drives which table it lands in.

    sticky_fault — latched since last clear; one row per session
    fault        — live bit; stored as intervals
    config       — firmware/motor constants; effectively immutable per session
    clock        — the device's own clock; **one series per session, rest dropped**
    telemetry    — everything else

    **The names below are the Phoenix catalogue, and only that.** A signal whose
    name carries a path is an application signal off the `Robot` pseudo-device
    (see `wpilog.py`), written by the team's own robot code, and nothing about
    CTRE's naming applies to it — a robot-code `Faults` bitfield turned into
    intervals would throw the value away and store a meaningless interval. Those
    are always telemetry, and a constant one still ends up in `session_constant`
    on its own, because the importer decides that from the data.
    """
    if "/" in signal:
        return "telemetry"
    if signal in CLOCK_SIGNALS:
        return "clock"
    # `FaultField` and `StickyFaultField` are the whole fault word packed into
    # one number, not a single bit. They start with "Fault"/"StickyFault" and
    # are the only two signals in the catalogue that do without being a bit —
    # folding them into intervals would store "a fault was set from t1 to t2"
    # and throw away *which*, which is the only thing they carry.
    if signal.endswith("Field"):
        return "telemetry"
    if signal.startswith("StickyFault"):
        return "sticky_fault"
    if signal.startswith("Fault"):
        return "fault"
    if signal.startswith("Version") or signal in {
        "IsProLicensed", "MotorKT", "MotorKV", "MotorStallCurrent",
        "ResetCount", "ConnectedMotor", "AppliedRotorPolarity",
    }:
        return "config"
    return "telemetry"


def parse_line(line: str) -> Sample | None:
    """One sample, or None for the header and anything unrecognised."""
    m = LINE_RE.match(line)
    if m is None:
        return None
    ts, dtype, can_id, signal, raw = m.groups()
    try:
        cid = int(can_id)
    except ValueError:
        return None
    # round(), not int() — int(0.685 * 1000) is 684 on some values because the
    # float is a hair under. That would shift samples into the wrong millisecond.
    return Sample(round(float(ts) * 1000), dtype, cid, signal, raw.strip())


@dataclass(frozen=True)
class LogMeta:
    """What the filename says. Every field is optional — names get typed by hand."""

    serial: str | None = None      # controller serial, hoot exports only
    started: str | None = None     # 'YYYY-MM-DD HH:MM:SS'
    match_key: str | None = None   # 'qm14' — the operator's own shorthand


def parse_log_name(name: str) -> LogMeta:
    """
    Pull serial / start time / match out of a log's filename.

    The filename is the only place any of this exists — a hoot knows its
    controller serial but not which match it was, and a DataLogManager wpilog
    knows the match but has no serial. Neither is inside the file.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name

    m = FILENAME_RE.match(stem)
    if m is not None:
        return LogMeta(serial=m.group("serial"),
                       started=f"{m.group('date')} {m.group('time').replace('-', ':')}")

    m = DLM_RE.match(stem)
    if m is not None:
        return LogMeta(
            started=f"{m.group('Y')}-{m.group('mo')}-{m.group('d')} "
                    f"{m.group('h')}:{m.group('mi')}:{m.group('s')}",
            match_key=_match_key(m))

    m = NAMED_RE.match(stem)
    if m is not None:
        return LogMeta(
            started=f"{m.group('Y')}-{m.group('mo')}-{m.group('d')} "
                    f"{m.group('h')}:{m.group('mi')}:{m.group('s')}",
            match_key=_match_key(m))

    m = AKIT_RE.match(stem)
    if m is not None:
        return LogMeta(
            started=f"20{m.group('y')}-{m.group('mo')}-{m.group('d')} "
                    f"{m.group('h')}:{m.group('mi')}:{m.group('s')}",
            match_key=_match_key(m))

    return LogMeta()


def _match_key(m: re.Match) -> str | None:
    """'qm14' from the match-type and number groups, if the pattern caught them."""
    mtype, mnum = m.group("mtype"), m.group("mnum")
    if not mtype or not mnum:
        return None
    return f"{MATCH_TYPES.get(mtype.lower(), mtype.lower())}{int(mnum)}"


def parse_filename(name: str) -> tuple[str | None, str | None]:
    """('06C94CC6…', '2026-07-29 14:28:18'). Kept as the two-value shorthand."""
    meta = parse_log_name(name)
    return meta.serial, meta.started


def coerce(raw: str) -> tuple[float | None, str | None]:
    """
    (numeric, enum_label). Exactly one is non-None.

    Numeric strings become floats; everything else is an enum label the caller
    interns to an integer code.
    """
    try:
        return float(raw), None
    except ValueError:
        return None, raw
