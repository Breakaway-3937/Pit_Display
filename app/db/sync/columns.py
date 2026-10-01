"""
Robot telemetry as columns: how a log bundle gets ~1000x smaller than the log.

A sample table stored row by row (series, time, ord, value) hides every
pattern a compressor could use. Stored as columns, per series, each one is
nearly nothing:

* **time** as the step from the previous sample (a 20 ms loop is `20, 20, 20`),
* **ord** is almost always 0,
* **value** on the series' own **exact quantum**. Logged values
  often carry 1-2 decimals (a quantum of 0.01); CTRE positions are fixed-point
  (`0.45263671875` is 927/2048, a quantum of 2^-11). A value that is exactly
  `k * quantum` is stored as the step in `k`, a small integer. A series with
  no exact quantum keeps its float64s, split into byte planes (all the sign
  and exponent bytes together), which compress well on their own.

**Lossless, bit for bit.** A quantum is used only if `round(v / q) * q`
reproduces *every* value of the series exactly, sign of zero included;
otherwise the series stays float. NULL is kept apart from NaN.

Measured on the real 3.85 GB Phoenix export (2026-09-28): 3,263,267 samples,
67 MB as SQLite, 2.6 MB as these columns after zstd. 98% of samples landed on
a quantum.

Pure standard library, on purpose: the home server decodes bundles with this
same file (home/HANDOFF.md). **Change the layout only with a new bundle
format** (`bundle.FORMAT`).
"""

from __future__ import annotations

import math
import struct
from array import array
from typing import Iterable, Iterator

# qcode 0 = float64 (no quantum); 1..10 = 10**-(qcode-1); 11..40 = 2**-(qcode-10)
_QUANTA: list[float] = [10.0 ** -k for k in range(0, 10)] + [2.0 ** -k for k in range(1, 31)]
_LIMIT = 2 ** 53


# ── varints ───────────────────────────────────────────────────────────────────

def _put(out: bytearray, n: int) -> None:
    n = n * 2 if n >= 0 else -n * 2 - 1          # zigzag: 0, -1, 1, -2 → 0, 1, 2, 3
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def _zig(n: int) -> int:
    return -((n >> 1) + 1) if n & 1 else n >> 1


class _Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def int(self) -> int:
        buf, pos = self.buf, self.pos
        shift = result = 0
        while True:
            b = buf[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if b < 0x80:
                break
            shift += 7
        self.pos = pos
        return _zig(result)

    def take(self, n: int) -> bytes:
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out


# ── one column of floats ──────────────────────────────────────────────────────

def _bits(v: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", v))[0]


def quantum_code(values: list) -> int:
    """The coarsest exact quantum for every value, as a qcode; 0 = none."""
    if not values or any(v is None or v != v or math.isinf(v) for v in values):
        return 0
    for code, q in enumerate(_QUANTA, start=1):
        for v in values:
            i = round(v / q)
            if abs(i) >= _LIMIT or i * q != v or (v == 0.0 and math.copysign(1.0, v) < 0):
                break
        else:
            return code
    return 0


class _FloatColumn:
    """Accumulates one float column across many series; four streams out."""

    def __init__(self):
        self.codes = bytearray()     # one byte per series
        self.ints = bytearray()      # quantized series: zigzag varint steps
        self.floats = array("d")     # the rest: raw values (planes on output)
        self.nulls = bytearray()     # the rest: 1 = NULL, one byte per value

    def add(self, values: list, base: list | None = None) -> None:
        """One series. With `base`, quantized values are stored relative to it."""
        code = quantum_code(values if base is None else values + base)
        self.codes.append(code)
        if code:
            q = _QUANTA[code - 1]
            prev = 0
            if base is None:
                for v in values:
                    i = round(v / q)
                    _put(self.ints, i - prev)
                    prev = i
            else:
                for v, b in zip(values, base):
                    _put(self.ints, round(v / q) - round(b / q))
        else:
            for v in values:
                if v is None:
                    self.nulls.append(1)
                    self.floats.append(0.0)
                else:
                    self.nulls.append(0)
                    self.floats.append(v)

    def streams(self, name: str) -> dict[str, bytes]:
        raw = self.floats.tobytes()
        return {f"{name}.q": bytes(self.codes), f"{name}.i": bytes(self.ints),
                f"{name}.f": b"".join(raw[i::8] for i in range(8)),
                f"{name}.n": bytes(self.nulls)}


class _FloatReader:

    def __init__(self, streams: dict[str, bytes], name: str):
        self.codes = streams[f"{name}.q"]
        self.ints = _Reader(streams[f"{name}.i"])
        planes = streams[f"{name}.f"]
        n = len(planes) // 8
        raw = bytearray(len(planes))
        for i in range(8):
            raw[i::8] = planes[i * n:(i + 1) * n]
        self.floats = array("d")
        self.floats.frombytes(bytes(raw))
        self.nulls = streams[f"{name}.n"]
        self.fpos = 0
        self.series = 0

    def take(self, count: int, base: list | None = None) -> list:
        code = self.codes[self.series]
        self.series += 1
        if code:
            q = _QUANTA[code - 1]
            out = []
            if base is None:
                cur = 0
                for _ in range(count):
                    cur += self.ints.int()
                    out.append(cur * q)
            else:
                for b in base:
                    out.append((self.ints.int() + round(b / q)) * q)
            return out
        start, self.fpos = self.fpos, self.fpos + count
        return [None if self.nulls[start + k] else self.floats[start + k] for k in range(count)]


# ── the two tables ────────────────────────────────────────────────────────────

def _grouped(rows: Iterable[tuple]) -> Iterator[tuple[int, list[tuple]]]:
    key, group = None, []
    for r in rows:
        if r[0] != key:
            if group:
                yield key, group
            key, group = r[0], []
        group.append(r)
    if group:
        yield key, group


def encode_samples(rows: Iterable[tuple]) -> dict[str, bytes]:
    """(series, t_ms, ord, v), sorted by series, t_ms, ord → named streams."""
    head, dt, ords = bytearray(), bytearray(), bytearray()
    v = _FloatColumn()
    for key, group in _grouped(rows):
        _put(head, key)
        _put(head, len(group))
        prev = 0
        for _k, t, o, _v in group:
            _put(dt, t - prev)
            prev = t
            _put(ords, o)
        v.add([r[3] for r in group])
    return {"s.head": bytes(head), "s.dt": bytes(dt), "s.ord": bytes(ords), **v.streams("s.v")}


def decode_samples(streams: dict[str, bytes]) -> Iterator[tuple]:
    head, dt, ords = _Reader(streams["s.head"]), _Reader(streams["s.dt"]), _Reader(streams["s.ord"])
    v = _FloatReader(streams, "s.v")
    while head.pos < len(head.buf):
        key, count = head.int(), head.int()
        t = 0
        times, os_ = [], []
        for _ in range(count):
            t += dt.int()
            times.append(t)
            os_.append(ords.int())
        for t, o, val in zip(times, os_, v.take(count)):
            yield key, t, o, val


def encode_rollup(rows: Iterable[tuple]) -> dict[str, bytes]:
    """(series, t_s, v_min, v_max, v_avg, n), sorted by series, t_s → streams.
    v_max is stored relative to v_min (same quantum, usually a small step)."""
    head, dt, ns = bytearray(), bytearray(), bytearray()
    vmin, vmax, vavg = _FloatColumn(), _FloatColumn(), _FloatColumn()
    for key, group in _grouped(rows):
        _put(head, key)
        _put(head, len(group))
        prev = 0
        for r in group:
            _put(dt, r[1] - prev)
            prev = r[1]
            _put(ns, r[5] if r[5] is not None else -1)
        mins = [r[2] for r in group]
        vmin.add(mins)
        maxs = [r[3] for r in group]
        vmax.add(maxs, base=mins if quantum_code(mins) else None)
        vavg.add([r[4] for r in group])
    return {"r.head": bytes(head), "r.dt": bytes(dt), "r.n": bytes(ns),
            **vmin.streams("r.min"), **vmax.streams("r.max"), **vavg.streams("r.avg")}


def decode_rollup(streams: dict[str, bytes]) -> Iterator[tuple]:
    head, dt, ns = _Reader(streams["r.head"]), _Reader(streams["r.dt"]), _Reader(streams["r.n"])
    vmin, vmax, vavg = (_FloatReader(streams, "r.min"), _FloatReader(streams, "r.max"),
                        _FloatReader(streams, "r.avg"))
    while head.pos < len(head.buf):
        key, count = head.int(), head.int()
        t = 0
        times, counts = [], []
        for _ in range(count):
            t += dt.int()
            times.append(t)
            n = ns.int()
            counts.append(None if n == -1 else n)
        mins = vmin.take(count)
        min_coded = vmin.codes[vmin.series - 1] != 0
        maxs = vmax.take(count, base=mins if min_coded and vmax.codes[vmax.series] else None)
        avgs = vavg.take(count)
        for row in zip(times, mins, maxs, avgs, counts):
            yield (key, *row)
