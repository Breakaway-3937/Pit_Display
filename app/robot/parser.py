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

# Filename: <serial>_<YYYY-MM-DD>_<HH-MM-SS>_detailed.txt
FILENAME_RE = re.compile(
    r"^(?P<serial>[0-9A-Fa-f]{8,})_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<time>\d{2}-\d{2}-\d{2})"
)

# The device's own timestamp echo — redundant with the line timestamp, and the
# single highest-volume signal in the file. Never stored.
DROP_SIGNALS = {"Timestamp"}


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
    meta         — dropped
    telemetry    — everything else
    """
    if signal in DROP_SIGNALS:
        return "meta"
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


def parse_filename(name: str) -> tuple[str | None, str | None]:
    """('06C94CC6…', '2026-07-29 14:28:18') from the export's filename."""
    m = FILENAME_RE.match(name)
    if m is None:
        return None, None
    started = f"{m.group('date')} {m.group('time').replace('-', ':')}"
    return m.group("serial"), started


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
