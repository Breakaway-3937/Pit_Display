"""
Playing With Fusion's Battery Fuel Gauge (BFG, ROB-70001) on CAN: every frame
it sends, decoded into one `Battery` per device.

**Source: the BFG FRC User Manual, rev 2026-02-27, "CAN Messages" (pp. 22-28)**,
https://www.playingwithfusion.com/files/rob70001_frc_usermanual_r02.pdf.
Nothing here is inferred from the vendor library; if a field disagrees with a
real BFG, the manual (or this file) is wrong, and a real frame settles it.

* Extended 29-bit ids, little-endian fields. Telemetry ids are `0x0A0B<api><dev>`;
  the heartbeat is `0x1F0B01<dev>`. `<dev>` is the device ID the team sets on
  each BFG, **0 from the factory**, so two new BFGs on one bus collide until
  one is renumbered (`Battery.clash` notices, from their serial numbers).
* Current is positive for **discharge**; a battery on the charger reads
  negative. This module keeps that sign; the panel says "in" / "out".
* **One known ambiguity:** "Battery Health" lists capacity as bytes 4-6 and
  cycles as bytes 6-7 (byte 6 twice). Read here as capacity = bytes 4-5 (u16
  mAh, like every other mAh field) and cycles = bytes 6-7. Check against a
  real BFG before trusting either.

The only thing this app ever *sends* a BFG is **Identify** (its OLED shows an
identification page): nothing that unlocks non-volatile memory, recalibrates
or resets statistics. Those stay with PWF's tools.

`encode_*` exist so the simulated cart (`sim.py`) produces the same bytes a
real BFG would, and the decoder is exercised either way.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

# ── Ids ──────────────────────────────────────────────────────────────────────

TELEMETRY_BASE = 0x0A0B0000
HEARTBEAT_BASE = 0x1F0B0100
CONFIG_ID = 0x1F0B03FF
PART_NUMBER = 0x130

API_POWER = 0x00          # every 10 ms
API_HEALTH = 0x01         # 1 s
API_MATCH_CURRENT = 0x02  # 1 s
API_MATCH_CHARGE = 0x03   # 1 s
API_MATCH_TIME = 0x05     # 1 s
API_RMS = 0x06            # 1 s
API_SOC = 0x07            # every 10 ms
API_CYCLE_CURRENT = 0x08  # 1 s
API_CYCLE_CHARGE = 0x09   # 1 s
API_CYCLE_TIME = 0x0A     # 1 s
API_CYCLE_ENERGY = 0x0C   # 1 s
API_MATCH_ENERGY = 0x0D   # 1 s
API_MATCH_DELTA = 0x0E    # 1 s
API_NICK1 = 0x0F          # 1 s
API_NICK2 = 0x10          # 1 s

CMD_IDENTIFY = 0x0D

# Byte 4 of State of Charge.
STATES = {
    0: "unknown",
    1: "discharging",
    2: "charging_cc",      # constant current: the bulk of a charge
    3: "charging_cv",      # constant voltage: current tapering off
    4: "charging_trickle",
    5: "charged",          # "may be removed from the charger"
    6: "resting",          # open circuit 10+ h; SoC from resting voltage
}
CHARGING = ("charging_cc", "charging_cv", "charging_trickle")

MANUFACTURERS = {0: "Duracell", 1: "Energizer", 2: "Interstate", 3: "Mighty Max",
                 4: "MK Powered", 5: "Power Sonic"}

JOULES_PER_BIT = 128      # match / cycle energy fields


# ── One battery ──────────────────────────────────────────────────────────────

@dataclass
class Battery:
    device_id: int
    # Battery Power (10 ms)
    current_a: float | None = None       # + discharge, − charging
    voltage_v: float | None = None
    dod_mah: int | None = None           # depth of discharge since full
    # State of Charge (10 ms)
    soc_mah: int | None = None           # charge remaining
    effective_mah: int | None = None     # what it could hold at this rate
    state: str = "unknown"
    dod_j: int | None = None
    # Battery Health (1 s)
    age_s: int | None = None
    capacity_mah: int | None = None
    cycles: int | None = None
    # Last match (1 s)
    match_min_v: float | None = None
    match_peak_a: float | None = None
    match_rms_a: float | None = None
    # Identity (heartbeat, nicknames)
    serial: int | None = None
    firmware: int | None = None
    nickname: str = ""
    manufacturer: str = ""
    design_mah: int | None = None
    # Bookkeeping
    last_seen: float = 0.0
    frames: int = 0
    serials_seen: set[int] = field(default_factory=set)
    _nick: list[bytes] = field(default_factory=lambda: [b"", b""])

    @property
    def name(self) -> str:
        return self.nickname or f"BFG {self.device_id}"

    @property
    def charging(self) -> bool:
        return self.state in CHARGING

    @property
    def clash(self) -> bool:
        """Two BFGs answering to one device ID: their frames are interleaved."""
        return len(self.serials_seen) > 1

    @property
    def fraction(self) -> float | None:
        """Charge remaining as a fraction of what it could hold now."""
        if self.soc_mah is None or not self.effective_mah:
            return None
        return max(0.0, min(1.0, self.soc_mah / self.effective_mah))

    @property
    def charge_rate_a(self) -> float:
        """Amps going *in*, as a positive number; 0 when not charging."""
        return max(0.0, -(self.current_a or 0.0))

    @property
    def power_w(self) -> float | None:
        if self.current_a is None or self.voltage_v is None:
            return None
        return abs(self.current_a) * self.voltage_v

    def minutes_to_full(self) -> float | None:
        """
        A rough ETA at today's rate. In constant-current it's honest; in
        constant-voltage the current keeps falling, so this is only a lower
        bound (the panel shows "% to go" there instead). None when it can't
        be estimated.
        """
        if not self.charging or self.soc_mah is None or not self.effective_mah:
            return None
        rate = self.charge_rate_a
        if rate < 0.05:
            return None
        missing_ah = max(0, self.effective_mah - self.soc_mah) / 1000.0
        return missing_ah / rate * 60.0

    @property
    def health(self) -> float | None:
        """Capacity against a new battery of its make (the BFG's own figures)."""
        if not self.capacity_mah or not self.design_mah:
            return None
        return self.capacity_mah / self.design_mah

    def age_since(self, now: float) -> float:
        return now - self.last_seen if self.last_seen else float("inf")


# ── Decoding ─────────────────────────────────────────────────────────────────

def _i32(b: bytes, o: int) -> int:
    return struct.unpack_from("<i", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _i16(b: bytes, o: int) -> int:
    return struct.unpack_from("<h", b, o)[0]


def _u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def apply(batteries: dict[int, Battery], arb_id: int, data: bytes,
          now: float | None = None) -> Battery | None:
    """Fold one frame into the right Battery. Returns it, or None if not a BFG frame."""
    now = time.time() if now is None else now
    dev = arb_id & 0xFF
    if (arb_id & 0x1FFFFF00) == HEARTBEAT_BASE:
        if len(data) < 8 or _u16(data, 4) != PART_NUMBER:
            return None
        b = batteries.setdefault(dev, Battery(dev))
        b.serial = _u24(data, 1)
        b.serials_seen.add(b.serial)
        b.firmware = _u16(data, 6)
    elif (arb_id & 0x1FFF0000) == TELEMETRY_BASE and len(data) >= 8:
        api = (arb_id >> 8) & 0xFF
        b = batteries.setdefault(dev, Battery(dev))
        if api == API_POWER:
            b.current_a = _i32(data, 0) / 1000.0
            b.voltage_v = _u16(data, 4) / 1000.0
            b.dod_mah = _i16(data, 6)
        elif api == API_SOC:
            b.soc_mah = _u16(data, 0)
            b.effective_mah = _u16(data, 2)
            b.state = STATES.get(data[4], "unknown")
            b.dod_j = _u24(data, 5)
        elif api == API_HEALTH:
            b.age_s = _u32(data, 0)
            b.capacity_mah = _u16(data, 4)       # see the module note on bytes 4-6
            b.cycles = _u16(data, 6)
        elif api == API_MATCH_CURRENT:
            b.match_peak_a = _i32(data, 4) / 1000.0
        elif api == API_MATCH_CHARGE:
            b.match_min_v = _u16(data, 0) / 1000.0
        elif api == API_RMS:
            b.match_rms_a = _u32(data, 0) / 1000.0
        elif api == API_MATCH_ENERGY:
            b.manufacturer = MANUFACTURERS.get(data[6], "")
        elif api == API_CYCLE_ENERGY:
            b.design_mah = _u16(data, 6)
        elif api in (API_NICK1, API_NICK2):
            b._nick[api - API_NICK1] = bytes(data[:8] if api == API_NICK1 else data[:3])
            b.nickname = (b"".join(b._nick).split(b"\0")[0]
                          .decode("ascii", "replace").strip())
        # other 1 s frames (times, cycle min/max, calibration) are decoded
        # when something shows them
    else:
        return None
    b.last_seen = now
    b.frames += 1
    return b


# ── Encoding (the simulator; and Identify, the one command we send) ─────────

def telemetry_id(api: int, dev: int) -> int:
    return TELEMETRY_BASE | (api << 8) | (dev & 0xFF)


def encode_heartbeat(dev: int, serial: int, firmware: int) -> tuple[int, bytes]:
    return (HEARTBEAT_BASE | (dev & 0xFF),
            bytes([0]) + serial.to_bytes(3, "little") + struct.pack("<HH", PART_NUMBER, firmware))


def encode_power(dev: int, current_a: float, voltage_v: float, dod_mah: int) -> tuple[int, bytes]:
    return (telemetry_id(API_POWER, dev),
            struct.pack("<iHh", round(current_a * 1000), round(voltage_v * 1000), dod_mah))


def encode_soc(dev: int, soc_mah: int, effective_mah: int, state: str, dod_j: int) -> tuple[int, bytes]:
    code = {v: k for k, v in STATES.items()}[state]
    return (telemetry_id(API_SOC, dev),
            struct.pack("<HHB", soc_mah, effective_mah, code)
            + max(0, dod_j).to_bytes(3, "little"))


def encode_health(dev: int, age_s: int, capacity_mah: int, cycles: int) -> tuple[int, bytes]:
    return telemetry_id(API_HEALTH, dev), struct.pack("<IHH", age_s, capacity_mah, cycles)


def encode_cycle_energy(dev: int, design_mah: int) -> tuple[int, bytes]:
    return telemetry_id(API_CYCLE_ENERGY, dev), struct.pack("<HHHH", 0, 0, 0, design_mah)


def encode_nickname(dev: int, name: str) -> list[tuple[int, bytes]]:
    raw = name.encode("ascii", "replace")[:11].ljust(11, b"\0")
    return [(telemetry_id(API_NICK1, dev), raw[:8]),
            (telemetry_id(API_NICK2, dev), raw[8:11] + bytes(5))]


def encode_identify(serial: int) -> tuple[int, bytes]:
    """Device Configuration → Identify, addressed by serial (manual p. 27)."""
    return (CONFIG_ID, bytes([CMD_IDENTIFY]) + serial.to_bytes(3, "little")
            + struct.pack("<H", PART_NUMBER) + bytes(2))
