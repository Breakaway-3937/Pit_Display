"""
`.wpilog` → the same `(t_ms, device, can id, signal, value)` stream the hoot
text grammar produces.

This is the second stage of the pipeline. `owlet.py` turns a `.hoot` into a
`.wpilog`; this reads a `.wpilog` — either one — into records the importer
stores. The robot's own DataLogManager file is a `.wpilog` too, so the same
reader covers both sources, which is the whole reason the pipeline converts to
wpilog rather than owlet's default mcap.

## Two kinds of signal live in here

A hoot-derived wpilog carries **device signals**, named for the CAN device that
produced them:

    /Phoenix6/TalonFX-2/MotorVoltage   →  ("TalonFX", 2, "MotorVoltage")

so every log lands on the same `device` row for that controller and the CAN-id
names the crew typed still apply.

A DataLogManager wpilog carries **application signals** — robot states, PDH
currents, shooter setpoints — which have no CAN address at all:

    /RealOutputs/Shooter/Setpoint      →  ("Robot", -1, "RealOutputs/Shooter/Setpoint")

Those go to one reserved pseudo-device, `Robot` at CAN id −1, with the full path
as the signal name. One row, created pre-named, and the CAN-map screen filters
it out: that table answers "which motor is CAN 11", and a fake CAN id in it is a
lie. Everything downstream — series, rollups, constants — treats it as an
ordinary device, which is the point.

## What is dropped, and why

* **String and msgpack *arrays*, and raw bytes.** There is nowhere to put them:
  `sample.v` is a REAL, and a JSON blob is not a measurement.
* **A string signal past `MAX_ENUM_LABELS` distinct values.** Strings are
  interned to integer codes, which works for `Open`/`Closed`/`KrakenX60` and
  falls apart for a status message that is different every cycle. Past the cap
  the signal is abandoned rather than allowed to grow an unbounded dictionary
  table; `Reader.enum_overflow` names the ones that hit it.

Numeric arrays are **not** dropped — a `double[]` pose is split into
`Pose[0]`, `Pose[1]`, `Pose[2]`, up to `MAX_ARRAY_WIDTH` elements, so it charts.
"""

import mmap
import re
from pathlib import Path
from typing import Iterator

from app.robot.datalog import DataLogReader

# The pseudo-device every signal without a CAN address is filed under.
APP_DEVICE_TYPE = "Robot"
APP_CAN_ID = -1
APP_DEVICE_LABEL = "Robot Code"
APP_DEVICE_SUBSYSTEM = "Application"

# A double[] wider than this is a matrix, not a measurement, and expanding it
# would create hundreds of series nobody will ever chart.
#
# **32 because the PDH has 24 channels.** `/PowerDistribution/ChannelCurrent` is
# one 24-wide `double[]`, and per-channel current is one of the things the pit
# actually looks at; a 16 here silently dropped channels 16-23. Swerve module
# states (4 modules × 2) and a Pose3d fit well under it.
MAX_ARRAY_WIDTH = 32

# Past this many distinct strings a signal is not an enumeration.
MAX_ENUM_LABELS = 256

# One path segment naming a CAN device: 'TalonFX-2', 'CANcoder-31'.
_CAN_SEGMENT = re.compile(r"^([A-Za-z][A-Za-z0-9_ ]*)-(\d+)$")

# Types worth storing, and how to get a number out of each.
_NUMERIC = {"double", "float", "int64", "boolean"}
_TEXT = {"string", "json"}
_ARRAY = {"double[]": "getDoubleArray", "float[]": "getFloatArray",
          "int64[]": "getIntegerArray", "boolean[]": "getBooleanArray"}


def entry_identity(name: str) -> tuple[str, int, str] | None:
    """
    ('TalonFX', 2, 'MotorVoltage') for a CAN signal, or the `Robot` pseudo-device
    for an application signal. None if there is no signal name at all.

    Scans the path **right to left** for a `<Type>-<digits>` segment, and takes
    everything after it as the signal name. Right to left because the device is
    the last addressable thing in the path — a bus or namespace above it may
    itself contain a hyphen and digits, and the innermost match is the device.

    A trailing segment is required, so `/TalonFX-2` alone is an application
    signal rather than a device with an empty signal name.
    """
    raw = name.strip()
    # Only the *leading* separator is dropped. Empty inner segments are kept —
    # AdvantageKit really does log `/SystemStats/NTClients//QuestNav@1/Connected`,
    # and collapsing the double slash would silently merge it with a different
    # entry that has one slash there.
    parts = (raw[1:] if raw.startswith("/") else raw).split("/")
    if not any(parts):
        return None
    for i in range(len(parts) - 2, -1, -1):
        m = _CAN_SEGMENT.match(parts[i])
        if m:
            return m.group(1).strip(), int(m.group(2)), "/".join(parts[i + 1:])
    return APP_DEVICE_TYPE, APP_CAN_ID, "/".join(parts)


class _Entry:
    """A started entry, with its identity resolved once instead of per record."""

    __slots__ = ("device_type", "can_id", "signal", "kind", "getter")

    def __init__(self, device_type: str, can_id: int, signal: str,
                 kind: str, getter: str | None):
        self.device_type = device_type
        self.can_id = can_id
        self.signal = signal
        self.kind = kind                 # 'num' | 'text' | 'array' | 'skip'
        self.getter = getter


def _entry_for(name: str, type_: str) -> _Entry:
    ident = entry_identity(name)
    if ident is None:
        return _Entry("", 0, "", "skip", None)
    device_type, can_id, signal = ident

    if type_ in _NUMERIC:
        kind, getter = "num", {
            "double": "getDouble", "float": "getFloat",
            "int64": "getInteger", "boolean": "getBoolean"}[type_]
    elif type_ in _TEXT:
        kind, getter = "text", "getString"
    elif type_ in _ARRAY:
        kind, getter = "array", _ARRAY[type_]
    else:
        kind, getter = "skip", None
    return _Entry(device_type, can_id, signal, kind, getter)


class Reader:
    """
    Streams one `.wpilog` as `(t_ms, device_type, can_id, signal, num, label)`.

    Exactly one of `num` / `label` is set — the importer cannot tell which
    source a record came from, which is what lets one storage path serve both.

    `fraction()` is the read position, for the progress bar. `skipped` counts
    records dropped for an unstorable type, so an import that quietly ignored
    half the file says so.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.skipped = 0
        self.enum_overflow: set[str] = set()
        self._fh = None
        self._mm: mmap.mmap | None = None
        self._it = None
        self._size = 1
        self._labels: dict[tuple[str, int, str], set[str]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def open(self) -> "Reader":
        self._fh = open(self.path, "rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self._size = max(len(self._mm), 1)
        reader = DataLogReader(self._mm)
        if not reader.isValid():
            self.close()
            raise ValueError(
                f"{self.path.name} is not a WPILOG file (no WPILOG header). "
                f"If it came from a .hoot, the conversion did not finish.")
        self._it = iter(reader)
        return self

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._it = None

    def __enter__(self) -> "Reader":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def fraction(self) -> float:
        return min(1.0, self._it.pos / self._size) if self._it else 0.0

    # ── The stream ────────────────────────────────────────────────────────

    def rows(self) -> Iterator[tuple[int, str, int, str, float | None, str | None]]:
        if self._it is None:
            raise RuntimeError("Reader.open() first")

        entries: dict[int, _Entry] = {}
        t0: int | None = None

        for record in self._it:
            if record.entry == 0:
                # Control record: an entry starting, finishing, or renaming.
                if record.isStart():
                    try:
                        data = record.getStartData()
                    except TypeError:
                        continue
                    entries[data.entry] = _entry_for(data.name, data.type)
                elif record.isFinish():
                    try:
                        entries.pop(record.getFinishEntry(), None)
                    except TypeError:
                        pass
                continue

            e = entries.get(record.entry)
            if e is None or e.kind == "skip":
                if e is not None:
                    self.skipped += 1
                continue

            # Timestamps are microseconds since robot boot, not since the log
            # started, so they are normalised against the first data record —
            # `t_ms` is defined as milliseconds from session start.
            if t0 is None:
                t0 = record.timestamp
            t_ms = (record.timestamp - t0) // 1000
            if t_ms < 0:
                t_ms = 0

            try:
                value = getattr(record, e.getter)()
            except (TypeError, ValueError):
                self.skipped += 1
                continue

            if e.kind == "num":
                yield (t_ms, e.device_type, e.can_id, e.signal,
                       float(value), None)
            elif e.kind == "text":
                key = (e.device_type, e.can_id, e.signal)
                seen = self._labels.setdefault(key, set())
                if value not in seen:
                    if len(seen) >= MAX_ENUM_LABELS:
                        self.enum_overflow.add(f"{e.device_type} {e.signal}")
                        e.kind = "skip"        # abandon it for the whole file
                        self.skipped += 1
                        continue
                    seen.add(value)
                yield (t_ms, e.device_type, e.can_id, e.signal, None, value)
            else:                                            # array
                for i, item in enumerate(value):
                    if i >= MAX_ARRAY_WIDTH:
                        break
                    yield (t_ms, e.device_type, e.can_id,
                           f"{e.signal}[{i}]", float(item), None)
