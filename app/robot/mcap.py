"""
Read a `.mcap` that owlet extracted from a `.hoot`, as the importer's rows.

**Why mcap and not owlet's `.wpilog`.** owlet 26.3.0's wpilog writer cuts the
end off most conversions while reporting 100% and exiting 0: 4 of 15 runs of
the same hoot were complete (2026-10-02). Tested and ruled out: reading too
early (sizes don't change after exit), a reused output path, the hoot itself,
the Pro licence check (same with networking blocked), an Apple Silicon race
(same under Rosetta). Its mcap writer is byte-identical every run and holds
the whole log (`tools/import_check.py` pins it). So the hoot path is
`owlet → .mcap → this → database`.

**The rows match the wpilog path's exactly**, so device and signal identities
(and the CAN names typed against them) carry over: topic `TalonFX-16` with a
table field `MotorVoltage` is `("TalonFX", 16, "MotorVoltage")`, a topic with
no device (`RobotEnable`) is the `Robot` pseudo-device, booleans are numbers,
enums and strings are labels, and `t_ms` is milliseconds from the first
record, as `wpilog.Reader` defines it.

**Speed (2026-10-03).** Decoding every message whole through reflection took
15.7 s per MB of hoot: a 769 MB CANivore log, three hours. owlet repeats
every signal in every frame, and ~92% of those values are repeats, so the
fast path (`_FastTable`) is compiled once per message type: per signal it
follows two offsets and reads the 8-byte `timestampSec`, and decodes the
value only when that time moved. No dicts per message, identities cached.
The rows are identical to the reflection decoder's (`rows(fast=False)`
keeps it as the reference; `tools/import_check.py` pins the totals).

**The format** (mcap.dev spec): an 8-byte magic, then records of
`opcode u8, length u64, content`. Schemas carry each message type's binary
FlatBuffers schema (decoded by `flatbuf.py`); messages live in chunks, LZ4
compressed by owlet at every level (`lz4`, the one dependency; zstd through
the standard library in case a newer owlet uses it).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator

from app.robot import flatbuf
from app.robot.wpilog import MAX_ENUM_LABELS, entry_identity

MAGIC = b"\x89MCAP0\r\n"
OP_SCHEMA, OP_CHANNEL, OP_MESSAGE, OP_CHUNK, OP_DATA_END = 0x03, 0x04, 0x05, 0x06, 0x0F

Row = tuple[int, str, int, str, "float | None", "str | None"]


class McapError(Exception):
    """Not an mcap, or one this reader can't decompress."""


def _string(b: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from("<I", b, p)[0]
    return b[p + 4:p + 4 + n].decode("utf-8", "replace"), p + 4 + n


def _decompress(kind: str, data: bytes, size: int) -> bytes:
    if kind == "":
        return data
    if kind == "lz4":
        import lz4.frame
        return lz4.frame.decompress(data)
    if kind == "zstd":
        from compression import zstd
        return zstd.decompress(data)
    raise McapError(f"chunks compressed with {kind!r}, which this build can't read")


def _records(b: bytes, p: int, end: int) -> Iterator[tuple[int, bytes]]:
    while p + 9 <= end:
        op = b[p]
        n = struct.unpack_from("<Q", b, p + 1)[0]
        yield op, b[p + 9:p + 9 + n]
        p += 9 + n


_U16 = struct.Struct("<H").unpack_from
_I32 = struct.Struct("<i").unpack_from
_U32 = struct.Struct("<I").unpack_from
_F64 = struct.Struct("<d").unpack_from


class _FastTable:
    """
    A device message type (a table whose fields are owlet `*Entry` tables of
    `value`, `timestampSec`, `units`), compiled for `_fast_items`. None of a
    schema's root fields may be anything else, or the type takes the slow path.
    """

    def __init__(self, schema: flatbuf.Schema):
        self.fields = []           # (slot, name, value_slot, ts_slot, reader)
        root = schema.objects[schema.root]
        for f in root.fields:
            if f.type.base != flatbuf.OBJ:
                raise ValueError("not a device table")
            sub = schema.objects[f.type.index]
            by = {x.name: x for x in sub.fields}
            if sub.is_struct or "value" not in by or "timestampSec" not in by:
                raise ValueError("not an entry table")
            ts = by["timestampSec"]
            if ts.type.base != flatbuf.DOUBLE:
                raise ValueError("timestampSec isn't a double")
            read = _value_reader(schema, by["value"])
            self.fields.append((4 + 2 * f.id, f.name, 4 + 2 * by["value"].id,
                                4 + 2 * ts.id, ts.default_real, read,
                                by["value"].type.base == flatbuf.STRING))


def _value_reader(schema: flatbuf.Schema, fld: flatbuf.FField):
    """(b, pos or 0) -> the field's value, as flatbuf.decode would give it."""
    base = fld.type.base
    if base == flatbuf.STRING:
        def read(b, pos):
            if not pos:
                return None
            s = pos + _U32(b, pos)[0]
            return b[s + 4:s + 4 + _U32(b, s)[0]].decode("utf-8", "replace")
        return read
    if base not in flatbuf._SCALAR:
        raise ValueError("value isn't a scalar or string")
    unpack = struct.Struct(flatbuf._SCALAR[base]).unpack_from
    is_real = base in (flatbuf.FLOAT, flatbuf.DOUBLE)
    default = fld.default_real if is_real else fld.default_integer
    if base == flatbuf.BOOL:
        default = bool(default)
    names = None
    if fld.type.index >= 0 and not is_real and fld.type.index < len(schema.enums):
        names = schema.enums[fld.type.index].values

    def read(b, pos):
        v = unpack(b, pos)[0] if pos else default
        if names is not None:
            label = names.get(int(v))
            if label is not None:
                return label
        return v
    return read


class Reader:
    """Streams one owlet `.mcap` as `(t_ms, device_type, can_id, signal, num, label)`."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._size = max(1, self._path.stat().st_size)
        self._pos = 0
        self._f = None
        self.skipped = 0
        self.enum_overflow: set[str] = set()

    def open(self) -> "Reader":
        self._f = open(self._path, "rb")
        if self._f.read(8) != MAGIC:
            self._f.close()
            raise McapError(f"{self._path.name} is not an mcap file")
        return self

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def fraction(self) -> float:
        return min(1.0, self._pos / self._size)

    def _top(self) -> Iterator[tuple[int, bytes]]:
        """Top-level records, read one at a time (a big log never sits whole
        in memory: each chunk is a few MB)."""
        f = self._f
        while True:
            head = f.read(9)
            if len(head) < 9:
                return
            op = head[0]
            n = struct.unpack_from("<Q", head, 1)[0]
            body = f.read(n)
            self._pos = f.tell()
            yield op, body
            if op == OP_DATA_END:
                return

    def rows(self, fast: bool = True) -> Iterator[Row]:
        if self._f is None:
            raise RuntimeError("Reader.open() first")
        schemas: dict[int, flatbuf.Schema] = {}
        compiled: dict[int, _FastTable | None] = {}
        names: dict[tuple[int, str], tuple[str, tuple | None]] = {}
        channels: dict[int, tuple[str, int]] = {}
        labels: dict[tuple, set] = {}
        # owlet's mcap repeats every signal's last value in every frame; each
        # value carries its own sample time, so a sample is new only when that
        # time moved (which is all the wpilog writer ever wrote).
        last_ts: dict[str, float] = {}
        dead: set[tuple] = set()
        t0: int | None = None

        def handle(op: int, body: bytes) -> Iterator[Row]:
            nonlocal t0
            if op == OP_SCHEMA:
                sid = struct.unpack_from("<H", body, 0)[0]
                _name, p = _string(body, 2)
                encoding, p = _string(body, p)
                n = struct.unpack_from("<I", body, p)[0]
                if encoding == "flatbuffer":
                    schemas[sid] = flatbuf.load_schema(body[p + 4:p + 4 + n])
                    compiled.pop(sid, None)
            elif op == OP_CHANNEL:
                cid, sid = struct.unpack_from("<HH", body, 0)
                topic, _p = _string(body, 4)
                channels[cid] = (topic, sid)
            elif op == OP_MESSAGE:
                cid = struct.unpack_from("<H", body, 0)[0]
                ch = channels.get(cid)
                schema = schemas.get(ch[1]) if ch else None
                if schema is None:
                    self.skipped += 1
                    return
                topic = ch[0]
                if fast:
                    if ch[1] not in compiled:
                        try:
                            compiled[ch[1]] = _FastTable(schema)
                        except ValueError:
                            compiled[ch[1]] = None          # an entry topic, or unusual
                    table = compiled[ch[1]]
                    if table is not None:
                        yield from fast_rows(cid, topic, table, body[22:])
                        return
                msg = flatbuf.decode(schema, body[22:])
                # A device topic's table holds one entry per signal that
                # changed; an entry topic (BooleanEntry, StringEntry) is one
                # signal whose name is the topic.
                entry_topic = "value" in msg and "timestampSec" in msg
                items = ([(topic, msg)] if entry_topic
                         else [(f"{topic}/{k}", v) for k, v in msg.items()
                               if isinstance(v, dict) and "value" in v])
                if not entry_topic and items:
                    # The device's clock, one sample per frame: owlet's wpilog
                    # writes it as a `Timestamp` signal (the importer keeps one
                    # clock series per session as a latency diagnostic). The
                    # frame's time is its newest sample's.
                    frame = max(float(v.get("timestampSec") or 0) for _n, v in items)
                    items.append((f"{topic}/Timestamp", {"timestampSec": frame, "value": frame,
                                                         "_clock": True}))
                for name, entry in items:
                    ident = entry_identity(name)
                    if ident is None:
                        continue
                    ts = entry.get("timestampSec")
                    if ts is None:
                        continue
                    if not entry.get("_clock"):
                        if last_ts.get(name) == ts:
                            continue                 # a repeat, not a sample
                        last_ts[name] = ts
                    us = round(float(ts) * 1_000_000)
                    if t0 is None:
                        t0 = us
                    t_ms = max(0, (us - t0) // 1000)
                    device_type, can_id, signal = ident
                    value = entry["value"]
                    if isinstance(value, (bool, int, float)):
                        yield (t_ms, device_type, can_id, signal, float(value), None)
                        continue
                    if not isinstance(value, str):
                        self.skipped += 1
                        continue
                    key = (device_type, can_id, signal)
                    if key in dead:
                        self.skipped += 1
                        continue
                    seen = labels.setdefault(key, set())
                    if value not in seen:
                        if len(seen) >= MAX_ENUM_LABELS:
                            self.enum_overflow.add(f"{device_type} {signal}")
                            dead.add(key)
                            self.skipped += 1
                            continue
                        seen.add(value)
                    yield (t_ms, device_type, can_id, signal, None, value)

        def emit(name: str, ident, ts: float, value) -> Iterator[Row]:
            nonlocal t0
            us = round(float(ts) * 1_000_000)
            if t0 is None:
                t0 = us
            t_ms = max(0, (us - t0) // 1000)
            device_type, can_id, signal = ident
            if isinstance(value, (bool, int, float)):
                yield (t_ms, device_type, can_id, signal, float(value), None)
                return
            if not isinstance(value, str):
                self.skipped += 1
                return
            key = (device_type, can_id, signal)
            if key in dead:
                self.skipped += 1
                return
            seen = labels.setdefault(key, set())
            if value not in seen:
                if len(seen) >= MAX_ENUM_LABELS:
                    self.enum_overflow.add(f"{device_type} {signal}")
                    dead.add(key)
                    self.skipped += 1
                    return
                seen.add(value)
            yield (t_ms, device_type, can_id, signal, None, value)

        def ident_of(cid: int, topic: str, field: str):
            got = names.get((cid, field))
            if got is None:
                full = f"{topic}/{field}"
                got = names[(cid, field)] = (full, entry_identity(full))
            return got

        per_channel: dict[int, list] = {}

        def fast_rows(cid: int, topic: str, table: _FastTable, b: bytes) -> Iterator[Row]:
            # One pass: each signal's identity is looked up once per channel
            # (by field position), its time compared, its value read only
            # when that time moved. The frame clock is written last, as the
            # reflection path writes it.
            idents = per_channel.get(cid)
            if idents is None:
                idents = per_channel[cid] = [ident_of(cid, topic, f[1]) for f in table.fields]
            root = _U32(b, 0)[0]
            vt = root - _I32(b, root)[0]
            vts = _U16(b, vt)[0]
            frame = None
            for i, (slot, _field, vslot, tsslot, tsdef, read, is_string) in enumerate(table.fields):
                if slot >= vts:
                    continue
                off = _U16(b, vt + slot)[0]
                if not off:
                    continue
                pos = root + off
                t = pos + _U32(b, pos)[0]
                svt = t - _I32(b, t)[0]
                svs = _U16(b, svt)[0]
                tso = _U16(b, svt + tsslot)[0] if tsslot < svs else 0
                ts = _F64(b, t + tso)[0] if tso else tsdef
                vo = _U16(b, svt + vslot)[0] if vslot < svs else 0
                if not vo and is_string:
                    continue                     # a string that isn't there: no entry (as decode)
                fts = float(ts or 0)
                if frame is None or fts > frame:
                    frame = fts
                full, ident = idents[i]
                if ident is None or last_ts.get(full) == ts:
                    continue                     # not a device signal / a repeat, not a sample
                last_ts[full] = ts
                yield from emit(full, ident, ts, read(b, t + vo if vo else 0))
            if frame is None:
                return
            full, ident = ident_of(cid, topic, "Timestamp")
            if ident is not None:
                yield from emit(full, ident, frame, frame)

        for op, body in self._top():
            if op == OP_CHUNK:
                _start, _end, size, _crc = struct.unpack_from("<QQQI", body, 0)
                kind, p = _string(body, 28)
                n = struct.unpack_from("<Q", body, p)[0]
                inner = _decompress(kind, body[p + 8:p + 8 + n], size)
                for iop, ibody in _records(inner, 0, len(inner)):
                    yield from handle(iop, ibody)
            else:
                yield from handle(op, body)
