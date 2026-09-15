"""
Wire protocol for the pit LED controller.

Pure Python — no Qt, no serial, no I/O. Everything here is a function of its
arguments so the codec can be exercised without hardware attached.

Frame layout on the wire:

    COBS( seq | opcode | payload... | crc8 ) + 0x00

COBS framing means a zero byte is *always* a frame boundary and never appears
inside a frame, so the link resynchronizes on its own after a partial write —
which is exactly what happens when the board resets mid-sentence (the Uno and
Nano both reset on DTR every time the port is opened).

CRC-8 is Dallas/Maxim, computed over the un-encoded seq|opcode|payload.
"""

from dataclasses import dataclass, field
from enum import IntEnum

# The safe ceiling for the CH340 clones most Nanos ship with. There is no
# reason to push it — commands are a handful of bytes and frames never cross
# the wire in this design.
BAUD = 115200

# Opening the port drops an Uno/Nano into the bootloader. Nothing sent during
# this window arrives, so the link waits it out before the HELLO handshake.
BOOT_DELAY_S = 2.0

# Host → device heartbeat. The firmware watchdog is set to 5s, so 1Hz gives us
# four missed pings of headroom before the strips fall back to their default.
HEARTBEAT_S = 1.0
WATCHDOG_S = 5.0

MAX_PAYLOAD = 192  # keeps a whole frame inside the ATmega328P's 64-byte serial
                   # buffer plus our own reassembly buffer, with room to spare


class Op(IntEnum):
    """Opcodes. 0x00–0x0F control, 0x10–0x7F host→device, 0x80+ device→host."""

    HELLO      = 0x01   # host → device: identify yourself
    ACK        = 0x02   # device → host: seq accepted
    NAK        = 0x03   # device → host: bad crc / unknown op / bad payload

    SET_MODE   = 0x10   # mode_id, speed
    SET_COLOR  = 0x11   # segment, r, g, b [, w]   (segment 0xFF = all; w = the
                        # white die, fw 2.2+, ignored by older firmware)
    SET_BRIGHT = 0x12   # brightness
    SET_PIXELS = 0x13   # offset_hi, offset_lo, r,g,b, r,g,b, ...
    SAVE       = 0x14   # persist current state as the boot default
    PING       = 0x15   # heartbeat
    OFF        = 0x16   # hard blank — distinct from brightness 0 so the
                        # firmware can skip its animation loop entirely

    INFO       = 0x80   # device → host: reply to HELLO
    LOG        = 0x81   # device → host: ascii diagnostic string


class Mode(IntEnum):
    """Animation modes the firmware implements. Keep in sync with the .ino."""

    SOLID    = 0
    BREATHE  = 1
    WIPE     = 2
    CHASE    = 3
    SPARKLE  = 4
    RAINBOW  = 5
    ALERT    = 6
    OFF      = 7


ALL_SEGMENTS = 0xFF


class ProtocolError(Exception):
    """Raised when a received frame cannot be decoded or fails its CRC."""


# ── CRC-8 (Dallas/Maxim, poly 0x31 reflected = 0x8C) ─────────────────────────

def crc8(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8C) if (crc & 0x01) else (crc >> 1)
    return crc


# ── COBS ─────────────────────────────────────────────────────────────────────

def cobs_encode(data: bytes) -> bytes:
    """Consistent Overhead Byte Stuffing — output contains no zero bytes."""
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            out.append(len(block) + 1)
            out.extend(block)
            block.clear()
        else:
            block.append(byte)
            if len(block) == 254:
                out.append(0xFF)
                out.extend(block)
                block.clear()
    out.append(len(block) + 1)
    out.extend(block)
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Inverse of cobs_encode. Raises ProtocolError on a malformed stream."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ProtocolError("zero byte inside COBS frame")
        i += 1
        end = i + code - 1
        if end > n:
            raise ProtocolError("COBS block overruns frame")
        out.extend(data[i:end])
        i = end
        if code < 0xFF and i < n:
            out.append(0)
    return bytes(out)


# ── Frame codec ──────────────────────────────────────────────────────────────

def encode_frame(seq: int, op: int, payload: bytes = b"") -> bytes:
    """Build one complete on-the-wire frame, delimiter included."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)}B exceeds {MAX_PAYLOAD}B")
    body = bytes([seq & 0xFF, int(op) & 0xFF]) + payload
    return cobs_encode(body + bytes([crc8(body)])) + b"\x00"


def decode_frame(encoded: bytes) -> tuple[int, int, bytes]:
    """
    Decode one COBS block (no trailing delimiter) into (seq, op, payload).
    Raises ProtocolError if it is too short or the CRC does not match.
    """
    raw = cobs_decode(encoded)
    if len(raw) < 3:
        raise ProtocolError(f"frame too short ({len(raw)}B)")
    body, received = raw[:-1], raw[-1]
    expected = crc8(body)
    if received != expected:
        raise ProtocolError(f"crc mismatch: got {received:#04x}, want {expected:#04x}")
    return body[0], body[1], body[2:]


class FrameReader:
    """
    Incremental reassembler. Feed it whatever bytes the port hands you and it
    yields complete frames as they arrive.

    Undecodable frames are dropped rather than raised — a corrupt frame is an
    expected event on a hot-plugged serial link, and the delimiter guarantees
    the next one starts clean. Count them instead so the UI can show a link
    that is technically up but quietly shedding traffic.
    """

    def __init__(self):
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, chunk: bytes):
        self._buf.extend(chunk)
        while True:
            try:
                idx = self._buf.index(0)
            except ValueError:
                # Runaway buffer means we are reading noise, not frames.
                if len(self._buf) > 4096:
                    del self._buf[:-1024]
                    self.dropped += 1
                return
            block = bytes(self._buf[:idx])
            del self._buf[:idx + 1]
            if not block:
                continue
            try:
                yield decode_frame(block)
            except ProtocolError:
                self.dropped += 1

    def reset(self):
        self._buf.clear()


# ── Device identity ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Segment:
    start: int
    length: int
    name: str = ""


@dataclass(frozen=True)
class DeviceInfo:
    """Parsed INFO reply. The app never hardcodes strip geometry — it asks."""

    fw_major: int
    fw_minor: int
    led_count: int
    segments: tuple[Segment, ...] = field(default_factory=tuple)

    @property
    def version(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}"

    def describe(self) -> str:
        seg = f", {len(self.segments)} segments" if len(self.segments) > 1 else ""
        return f"fw {self.version} · {self.led_count} px{seg}"


def parse_info(payload: bytes) -> DeviceInfo:
    """
    INFO payload: major | minor | count_hi | count_lo | n_seg | (start_hi,
    start_lo, len_hi, len_lo) * n_seg
    """
    if len(payload) < 5:
        raise ProtocolError(f"INFO payload too short ({len(payload)}B)")
    major, minor = payload[0], payload[1]
    led_count = (payload[2] << 8) | payload[3]
    n_seg = payload[4]
    segments = []
    off = 5
    for _ in range(n_seg):
        if off + 4 > len(payload):
            raise ProtocolError("INFO segment table truncated")
        start = (payload[off] << 8) | payload[off + 1]
        length = (payload[off + 2] << 8) | payload[off + 3]
        segments.append(Segment(start, length))
        off += 4
    return DeviceInfo(major, minor, led_count, tuple(segments))


# ── Payload builders ─────────────────────────────────────────────────────────

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """'#C82027' → (200, 32, 39). Accepts 3- or 6-digit hex, with or without #."""
    h = value.lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a hex color: {value!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def payload_mode(mode: int, speed: int = 128) -> bytes:
    return bytes([int(mode) & 0xFF, max(0, min(255, speed))])


SEG_CENTER = 0
SEG_SIDES = 1


def payload_color(rgb: tuple[int, int, int], segment: int = ALL_SEGMENTS,
                  white: int | None = None) -> bytes:
    """
    `white` is the strip's fourth channel — the dedicated white die — and is
    the only way to ask the strips for a real white: (255,255,255) on the
    RGB channels is tinted and three times the current. Sent as a fifth
    byte, which firmware before 2.2 ignores (that segment then shows the
    RGB part alone — black, for a white request — rather than anything
    unsafe).
    """
    r, g, b = (max(0, min(255, c)) for c in rgb)
    out = [segment & 0xFF, r, g, b]
    if white is not None:
        out.append(max(0, min(255, int(white))))
    return bytes(out)


def payload_brightness(value: int) -> bytes:
    return bytes([max(0, min(255, value))])


def payload_pixels(offset: int, pixels: list[tuple[int, int, int]]) -> bytes:
    out = bytearray([(offset >> 8) & 0xFF, offset & 0xFF])
    for r, g, b in pixels:
        out.extend((max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))
    return bytes(out)
