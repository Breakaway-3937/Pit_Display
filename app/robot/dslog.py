"""
The Driver Station's own logs: `.dslog` (50 records a second) and its
`.dsevents` sibling (the DS's messages), the CSA's first tool.

Brayden, 2026-11-03: "I'll find a way to bring the dslog to the app, so assume
it's coming in." The robot's logs can't see the link to the robot: trip time,
lost packets, whether the DS had comms, the watchdog and brownout flags the DS
recorded, the radio's signal, and the DS's messages (lost communication, ping
results, time since robot boot, radio lost/seen, joystick unplugged, the FMS
connection with the match). These can.

**The format** (version 4, big-endian; no official spec exists). Decoded as
AdvantageScope does (`DSLogReader.ts`, `DSEventsReader.ts`, `DSUtil.ts`,
Littleton Robotics, BSD; after orangelight's DSLOG-Reader), cross-checked
against Greg McKaskle's description on Chief Delphi:

* header, 20 bytes: int32 version (4), then LabVIEW time: int64 seconds since
  1904-01-01 and uint64 fraction of 2^64.
* a `.dslog` record every 20 ms: trip time uint8 × 0.5 ms; packet loss int8 ×
  4 % (clamped 0-100); battery uint16 / 256 V (> 20 V is "no reading": the DS
  had no comms); roboRIO CPU uint8 × 0.5 %; a status byte whose flags are set
  when their bit is **0** (7 brownout, 6 watchdog, 5 DS teleop, 3 DS
  disabled, 2 robot teleop, 1 robot auto, 0 robot disabled); CAN
  utilization uint8 × 0.5 %; Wi-Fi uint8 × 0.5 dB; bandwidth uint16 / 256
  Mb/s. Then 4 bytes whose last names the power distribution (33 REV PDH,
  25 CTRE PDP, else none) and its currents: PDH, a CAN id byte, 27 bytes
  holding 20 10-bit channels (LSB first, bit `i//3*32 + i%3*10`, / 8 A),
  4 bytes of channels 20-23 (/ 16 A), 1 byte; PDP, a CAN id byte, 21 bytes
  holding 16 channels, 8 bits read from each 10-bit slot (bit
  `i//6*64 + i%6*10`, / 8 A), 3 bytes.
* LabVIEW time is seconds since 1904-01-01 UTC. (AdvantageScope adds a fixed
  local offset; this keeps UTC and `started` converts to this machine's local
  time, as the other logs' file names are.)
* a `.dsevents` record: LabVIEW time (16 bytes), int32 length, UTF-8 text,
  tagged (`<TagVersion>`, `<time>`, `<count>`, `<flags>`, `<Code>`,
  `<location>`, `<stack>`, `<message>`, `<details>`). The tags are kept
  apart here: the `<Code>` (44004…) is what a check matches on.

**Not yet verified on a real file from this team** (none was on the dev Mac,
2026-11-03): the decoding is AdvantageScope's, which is maintained against
real logs; `tools/dslog_check.py` round-trips this module's own writer.
When the first real pair arrives, import it and compare a few seconds
with the DS Log Viewer.

Rows go to the pseudo-device `Robot` (CAN id -1) as `DS/…` signals, so the
CAN map never shows them and storage can't tell them from any other log's.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

VERSION = 4
PERIOD_MS = 20
LV_EPOCH = -2082844800          # 1904-01-01T00:00:00Z in Unix seconds
HEADER = struct.Struct(">iqQ")
PD_REV, PD_CTRE = 33, 25
NO_READING_V = 20.0             # a battery reading above this means no comms
GLITCH_V = 1.0                  # below this, with comms, a bad sample, not a battery
                                # (one 0.0 V sample mid-match in qm11, 2026-10-01)

# The DS's message codes a CSA reads (WPILib "Driver Station Errors/Warnings").
CODES = {
    44002: "ping_status",        # Ping Results: which link in the chain is bad
    44003: "no_robot_code",
    44004: "lost_comms",         # the DS lost communication with the robot
    44007: "robot_boot_time",    # time since robot boot (small = it rebooted)
    44008: "radio_events",       # radio lost / seen times
    44009: "joystick_unplugged",
}
_TAG = re.compile(r"<(TagVersion|time|count|flags|Code|location|stack|message|details)>\s*")


def lv_to_unix(seconds: int, fraction: int) -> float:
    return LV_EPOCH + seconds + fraction / 2 ** 64


def unix_to_lv(t: float) -> tuple[int, int]:
    whole = int(t - LV_EPOCH)
    frac = int((t - LV_EPOCH - whole) * 2 ** 64) & (2 ** 64 - 1)
    return whole, frac


@dataclass
class Record:
    trip_ms: float
    packet_loss: float          # 0-1
    battery_v: float | None     # None: no reading (no comms)
    cpu: float                  # 0-1
    can: float                  # 0-1
    wifi_db: float
    wifi_mb: float
    brownout: bool
    watchdog: bool
    ds_teleop: bool
    ds_disabled: bool
    robot_teleop: bool
    robot_auto: bool
    robot_disabled: bool
    pd_type: int = 0
    comms: bool = True          # False: the DS had no reading from the robot
    currents: list[float] = field(default_factory=list)


@dataclass
class Event:
    t_s: float                  # since the log's start
    text: str                   # the message, tags removed
    code: int | None = None
    fields: dict = field(default_factory=dict)   # the tagged parts


# ── reading ──────────────────────────────────────────────────────────────

def read_header(data: bytes) -> tuple[int, float]:
    if len(data) < HEADER.size:
        raise ValueError("too short for a Driver Station log header")
    version, sec, frac = HEADER.unpack_from(data, 0)
    return version, lv_to_unix(sec, frac)


def _bits(raw: bytes) -> list[bool]:
    return [bool(b & (1 << i)) for b in raw for i in range(8)]


def _value(bits: list[bool], at: int, width: int) -> int:
    return sum(1 << i for i in range(width) if bits[at + i])


def records(data: bytes) -> Iterator[Record]:
    version, _start = read_header(data)
    if version != VERSION:
        raise ValueError(f"Driver Station log version {version}; this reads version {VERSION}")
    pos, n = HEADER.size, len(data)
    last_v: float | None = None
    while pos + 14 <= n:
        b = data[pos:pos + 10]
        trip = b[0] * 0.5
        loss = min(1.0, max(0.0, struct.unpack_from(">b", b, 1)[0] * 4 * 0.01))
        volts = struct.unpack_from(">H", b, 2)[0] / 256
        status = b[5]
        flag = lambda bit: not (status >> bit) & 1    # noqa: E731  set when the bit is 0
        rec = Record(
            trip_ms=trip, packet_loss=loss,
            battery_v=volts if GLITCH_V <= volts <= NO_READING_V else None,
            comms=volts <= NO_READING_V,
            cpu=b[4] * 0.5 * 0.01, can=b[6] * 0.5 * 0.01,
            wifi_db=b[7] * 0.5, wifi_mb=struct.unpack_from(">H", b, 8)[0] / 256,
            brownout=flag(7), watchdog=flag(6), ds_teleop=flag(5), ds_disabled=flag(3),
            robot_teleop=flag(2), robot_auto=flag(1), robot_disabled=flag(0))
        last_v = rec.battery_v if rec.battery_v is not None else last_v
        rec.pd_type = data[pos + 13]
        pos += 14
        if rec.pd_type == PD_REV:
            if pos + 33 > n:
                break
            bits = _bits(data[pos + 1:pos + 28])
            rec.currents = [_value(bits, (i // 3) * 32 + (i % 3) * 10, 10) / 8 for i in range(20)]
            rec.currents += [data[pos + 28 + i] / 16 for i in range(4)]
            pos += 1 + 27 + 4 + 1
        elif rec.pd_type == PD_CTRE:
            if pos + 25 > n:
                break
            bits = _bits(data[pos + 1:pos + 22])
            rec.currents = [_value(bits, (i // 6) * 64 + (i % 6) * 10, 8) / 8 for i in range(16)]
            pos += 1 + 21 + 3
        yield rec


def _parse_event(text: str) -> tuple[str, int | None, dict]:
    parts: dict[str, str] = {}
    pieces = _TAG.split(text)
    # split gives [before, tag1, value1, tag2, value2, …]
    for i in range(1, len(pieces) - 1, 2):
        parts[pieces[i].lower()] = pieces[i + 1].strip()
    message = " ".join(x for x in (parts.get("message"), parts.get("details")) if x) \
        or pieces[0].strip()
    code = None
    try:
        code = int(parts["code"]) if parts.get("code") else None
    except ValueError:
        code = None
    if code is None:
        m = re.search(r"\b(4400\d)\b", text)
        code = int(m.group(1)) if m else None
    return " ".join(message.split()), (abs(code) if code is not None else None), parts


def events(data: bytes) -> Iterator[Event]:
    version, start = read_header(data)
    if version != VERSION:
        raise ValueError(f"Driver Station events version {version}; this reads version {VERSION}")
    pos, n = HEADER.size, len(data)
    while pos + 20 <= n:
        sec, frac = struct.unpack_from(">qQ", data, pos)
        length = struct.unpack_from(">i", data, pos + 16)[0]
        pos += 20
        if length < 0 or pos + length > n:
            break
        raw = data[pos:pos + length].decode("utf-8", "replace")
        pos += length
        text, code, parts = _parse_event(raw)
        yield Event(t_s=lv_to_unix(sec, frac) - start, text=text, code=code, fields=parts)


# ── as rows for the importer (app/robot/ingest.py's Row) ─────────────────

FLAGS = ("brownout", "watchdog", "ds_teleop", "ds_disabled", "robot_teleop", "robot_auto",
         "robot_disabled")
SIGNAL = {"trip_ms": "DS/TripTimeMS", "packet_loss": "DS/PacketLossPct",
          "battery_v": "DS/BatteryVoltage", "cpu": "DS/CPUPct", "can": "DS/CANUtilPct",
          "wifi_db": "DS/WifiDb", "wifi_mb": "DS/WifiMb", "brownout": "DS/Brownout",
          "watchdog": "DS/Watchdog", "ds_teleop": "DS/DSTeleop", "ds_disabled": "DS/DSDisabled",
          "robot_teleop": "DS/RobotTeleop", "robot_auto": "DS/RobotAuto",
          "robot_disabled": "DS/RobotDisabled"}
PERCENT = ("packet_loss", "cpu", "can")
EVENTS = "DS/Events"
COMMS = "DS/Comms"               # 1 while the DS has a battery reading from the robot
TOTAL = "DS/TotalCurrent"        # the PDH/PDP channels summed
ENABLED = "DS/Enabled"           # the DS's command: what match_phases reads
AUTO = "DS/Autonomous"


BLIP_S = 1.0                     # a disable shorter than this, between enables, isn't one


def _enabled(recs: list[Record]) -> tuple[list[bool], list[bool]]:
    """Enabled, per record, as a match reads it. The DS's disabled bit also
    rises for the instant the roboRIO cuts outputs in a brownout: in the
    team's qm17 it blinked 40-odd times in teleop, and the match's phases
    ended at the first one, 100 s early (2026-11-05). So a disable under
    `BLIP_S` between two enabled stretches is ridden through; the 3 s FMS
    gap between auto and teleop is longer and stays."""
    on = [not r.ds_disabled for r in recs]
    tele = [r.ds_teleop for r in recs]      # its teleop bit drops in the blip too
    gap = int(BLIP_S * 1000 / PERIOD_MS)
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            i += 1
            continue
        j = i
        while j < n and not on[j]:
            j += 1
        if 0 < i and j < n and j - i < gap:
            on[i:j] = [True] * (j - i)
            tele[i:j] = [tele[i - 1]] * (j - i)
        i = j
    return on, tele


REPEATED = "[repeated"          # the summary line's mark, read by events.py
REPEAT_MIN = 50                  # a console line at least this often gets a summary


def repeats(evts: list[Event]) -> list[tuple[float, float, int, str]]:
    """(first, last, count, text) of each console line (no DS code) printed at
    least `REPEAT_MIN` times, figures in it ignored when comparing."""
    seen: dict[str, list] = {}
    for e in evts:
        if e.code is not None or e.text.startswith(("Info ", "FMS Connected", "NT:")):
            continue
        key = re.sub(r"\d+(?:\.\d+)?", "#", e.text)[:160]
        got = seen.setdefault(key, [e.t_s, e.t_s, 0, e.text[:160]])
        got[1], got[2] = e.t_s, got[2] + 1
    return [tuple(v) for v in seen.values() if v[2] >= REPEAT_MIN]


def match_key(evts: list[Event]) -> str | None:
    """'qm14' from the DS's FMS Connected message, when there is one."""
    kinds = {"qualification": "qm", "practice": "pm", "elimination": "sf", "playoff": "sf",
             "final": "f"}
    for e in evts:
        m = re.search(r"FMS Connected:\s*(\w+)\s*-\s*(\d+)", e.text, re.I)
        if m and m.group(1).lower() != "none":     # "None - 0:0": no match on the field
            return f"{kinds.get(m.group(1).lower(), m.group(1).lower()[:2])}{int(m.group(2))}"
    return None


class Reader:
    """A `.dslog` and, beside it, its `.dsevents`: rows as the importer wants them."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        version, self.start = read_header(self.data)
        if version != VERSION:
            raise ValueError(f"{self.path.name}: Driver Station log version {version}; this "
                             f"app reads version {VERSION}")
        ev = self.path.with_suffix(".dsevents")
        self.events: list[Event] = []
        if ev.is_file():
            try:
                self.events = list(events(ev.read_bytes()))
            except ValueError:
                self.events = []
        self.skipped = 0
        self.enum_overflow: set[str] = set()
        self._done = 0
        self._total = max(1, (len(self.data) - HEADER.size) // 14)

    @property
    def started(self) -> str:
        # UTC, as the roboRIO names its hoots and AdvantageKit its logs: the
        # DS laptop's local time put every DS log of an event hours away from
        # its match (the team's own logs, 2026-11-05: qm17 at 14:40 local,
        # 19:42 in the robot's).
        return datetime.fromtimestamp(self.start, UTC).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def match(self) -> str | None:
        return match_key(self.events)

    def rows(self) -> Iterator[tuple]:
        from app.robot.wpilog import APP_CAN_ID, APP_DEVICE_TYPE
        dev, can = APP_DEVICE_TYPE, APP_CAN_ID
        recs = list(records(self.data))
        enabled, teleop = _enabled(recs)
        for i, r in enumerate(recs):
            t = i * PERIOD_MS
            self._done = i
            for attr, name in SIGNAL.items():
                v = getattr(r, attr)
                if v is None:
                    continue
                if isinstance(v, bool):
                    v = 1.0 if v else 0.0
                elif attr in PERCENT:
                    v = round(v * 100, 4)
                yield (t, dev, can, name, float(v), None)
            yield (t, dev, can, COMMS, 1.0 if r.comms else 0.0, None)
            yield (t, dev, can, ENABLED, 1.0 if enabled[i] else 0.0, None)
            yield (t, dev, can, AUTO, 0.0 if (not enabled[i] or teleop[i]) else 1.0, None)
            for ch, amps in enumerate(r.currents):
                yield (t, dev, can, f"DS/PDCurrent[{ch}]", float(amps), None)
            if r.currents:
                # The whole robot's draw, as one reading: what a brownout's
                # diagnosis weighs (matches AdvantageKit's total to 0.8 A).
                yield (t, dev, can, TOTAL, round(sum(r.currents), 4), None)
        for e in self.events:
            # Every tagged part but the bookkeeping: the uptime of a "time since
            # robot boot" (secondsSinceReboot) isn't in its message.
            extra = " ".join(v for k, v in e.fields.items()
                             if k not in ("tagversion", "time", "count", "flags", "code",
                                          "message", "details") and v)
            label = (f"[{e.code}] " if e.code else "") + e.text + (f" | {extra}" if extra else "")
            yield (max(0, int(e.t_s * 1000)), dev, can, EVENTS, None, label[:2000])
        # A line printed over and over is stored once (storage keeps changes),
        # so its count is written here, once, at its first time: the team's
        # robot printed "Indexer setState called with: FIRE" 1,165 times in
        # qm17, and the stored log showed it as one line (events.console_spam).
        for first, last, n, text in repeats(self.events):
            yield (max(0, int(first * 1000)), dev, can, EVENTS, None,
                   f"{REPEATED} {n} times over {last - first:.0f} s] {text}"[:2000])

    def fraction(self) -> float:
        return min(1.0, self._done / self._total)

    def close(self) -> None:
        pass


# ── writing (for checks: a log whose contents are known) ─────────────────

def write(path: Path, start: float, recs: list[Record],
          evts: list[tuple[float, str]] | None = None) -> None:
    """A `.dslog` (and `.dsevents` when `evts`) this module reads back exactly."""
    out = bytearray(HEADER.pack(VERSION, *unix_to_lv(start)))
    for r in recs:
        status = 0xFF
        for bit, on in ((7, r.brownout), (6, r.watchdog), (5, r.ds_teleop), (3, r.ds_disabled),
                        (2, r.robot_teleop), (1, r.robot_auto), (0, r.robot_disabled)):
            if on:
                status &= ~(1 << bit)
        volts = 0xFFFF if r.battery_v is None else int(round(r.battery_v * 256))
        out += bytes([int(r.trip_ms * 2) & 0xFF])
        out += struct.pack(">b", int(round(r.packet_loss * 25)))
        out += struct.pack(">H", volts)
        out += bytes([int(r.cpu * 200), status, int(r.can * 200), int(r.wifi_db * 2) & 0xFF])
        out += struct.pack(">H", int(r.wifi_mb * 256))
        out += bytes([0, 0, 0, r.pd_type])
        if r.pd_type == PD_REV:
            bits = [False] * (27 * 8)
            for i, amps in enumerate((r.currents + [0.0] * 24)[:20]):
                v, at = int(round(amps * 8)) & 0x3FF, (i // 3) * 32 + (i % 3) * 10
                for k in range(10):
                    bits[at + k] = bool(v >> k & 1)
            out += bytes([1]) + bytes(sum(1 << k for k in range(8) if bits[j * 8 + k])
                                      for j in range(27))
            out += bytes(int(round(a * 16)) & 0xFF for a in (r.currents + [0.0] * 24)[20:24])
            out += bytes([0])
    Path(path).write_bytes(bytes(out))
    if evts is not None:
        ev = bytearray(HEADER.pack(VERSION, *unix_to_lv(start)))
        for t, text in evts:
            raw = text.encode("utf-8")
            ev += struct.pack(">qQi", *unix_to_lv(start + t), len(raw)) + raw
        Path(path).with_suffix(".dsevents").write_bytes(bytes(ev))
