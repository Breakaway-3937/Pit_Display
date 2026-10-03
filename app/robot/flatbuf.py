"""
Decode a FlatBuffer by its binary schema (`.bfbs`), with no generated code.

owlet's `.mcap` carries each message as a FlatBuffer and its schema as a
binary FlatBuffers schema (reflection.fbs, itself a FlatBuffer). This reads
that schema and decodes any table by it: names, scalars, strings, enums,
nested tables, structs and vectors. Standard library only.

The layout follows FlatBuffers' own `reflection.fbs` (field ids in the
comments); the wire format is little-endian offsets into the buffer: a table
starts with a signed offset back to its vtable, whose entries (one u16 per
field id, after two u16 sizes) are each field's offset in the table, 0 =
absent (the default applies).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# reflection.BaseType
NONE, UTYPE, BOOL, BYTE, UBYTE, SHORT, USHORT, INT, UINT, LONG, ULONG, FLOAT, \
    DOUBLE, STRING, VECTOR, OBJ, UNION, ARRAY, VECTOR64 = range(19)
_SCALAR = {BOOL: "<?", BYTE: "<b", UBYTE: "<B", UTYPE: "<B", SHORT: "<h", USHORT: "<H",
           INT: "<i", UINT: "<I", LONG: "<q", ULONG: "<Q", FLOAT: "<f", DOUBLE: "<d"}
_SIZE = {k: struct.calcsize(v) for k, v in _SCALAR.items()}


class _Buf:
    """A table or struct position in a buffer, with the reads a decoder needs."""

    __slots__ = ("b",)

    def __init__(self, b: bytes):
        self.b = b

    def u8(self, p): return self.b[p]
    def u16(self, p): return struct.unpack_from("<H", self.b, p)[0]
    def i32(self, p): return struct.unpack_from("<i", self.b, p)[0]
    def u32(self, p): return struct.unpack_from("<I", self.b, p)[0]

    def field(self, table: int, fid: int) -> int:
        """Absolute position of field `fid` in the table at `table`, or 0."""
        vt = table - self.i32(table)
        vt_size = self.u16(vt)
        slot = 4 + 2 * fid
        if slot >= vt_size:
            return 0
        off = self.u16(vt + slot)
        return table + off if off else 0

    def deref(self, p: int) -> int:
        return p + self.u32(p)

    def string(self, p: int) -> str:
        s = self.deref(p)
        n = self.u32(s)
        return self.b[s + 4:s + 4 + n].decode("utf-8", "replace")

    def vector(self, p: int) -> tuple[int, int]:
        """(position of the first element, length) of the vector at field `p`."""
        v = self.deref(p)
        return v + 4, self.u32(v)

    def scalar(self, p: int, base: int):
        return struct.unpack_from(_SCALAR[base], self.b, p)[0]


# ── The schema (reflection.fbs) ──────────────────────────────────────────────

@dataclass
class FType:
    base: int
    element: int
    index: int
    fixed_length: int
    base_size: int
    element_size: int


@dataclass
class FField:
    name: str
    type: FType
    id: int
    offset: int
    default_integer: int
    default_real: float


@dataclass
class FObject:
    name: str
    fields: list[FField]
    is_struct: bool
    bytesize: int


@dataclass
class FEnum:
    name: str
    values: dict[int, str]


@dataclass
class Schema:
    objects: list[FObject]
    enums: list[FEnum]
    root: int                     # index into objects
    by_name: dict[str, int] = field(default_factory=dict)


def _type(r: _Buf, t: int) -> FType:
    def sc(fid, base, default):
        p = r.field(t, fid)
        return r.scalar(p, base) if p else default
    return FType(base=sc(0, BYTE, 0), element=sc(1, BYTE, 0), index=sc(2, INT, -1),
                 fixed_length=sc(3, USHORT, 0), base_size=sc(4, UINT, 4),
                 element_size=sc(5, UINT, 0))


def load_schema(bfbs: bytes) -> Schema:
    """Parse a binary FlatBuffers schema (reflection.fbs's `Schema`)."""
    r = _Buf(bfbs)
    root = r.deref(0)
    objects: list[FObject] = []
    p = r.field(root, 0)                                 # Schema.objects
    start, n = r.vector(p) if p else (0, 0)
    for i in range(n):
        o = r.deref(start + 4 * i)
        name = r.string(r.field(o, 0))                   # Object.name
        fields = []
        fp = r.field(o, 1)                               # Object.fields
        fs, fn = r.vector(fp) if fp else (0, 0)
        for j in range(fn):
            f = r.deref(fs + 4 * j)
            def sc(fid, base, default, f=f):
                q = r.field(f, fid)
                return r.scalar(q, base) if q else default
            fields.append(FField(
                name=r.string(r.field(f, 0)), type=_type(r, r.deref(r.field(f, 1))),
                id=sc(2, USHORT, 0), offset=sc(3, USHORT, 0),
                default_integer=sc(4, LONG, 0), default_real=sc(5, DOUBLE, 0.0)))
        is_struct = bool(r.scalar(r.field(o, 2), BOOL)) if r.field(o, 2) else False
        bytesize = r.scalar(r.field(o, 4), INT) if r.field(o, 4) else 0
        objects.append(FObject(name, fields, is_struct, bytesize))
    enums: list[FEnum] = []
    p = r.field(root, 1)                                 # Schema.enums
    start, n = r.vector(p) if p else (0, 0)
    for i in range(n):
        e = r.deref(start + 4 * i)
        vals = {}
        vp = r.field(e, 1)                               # Enum.values
        vs, vn = r.vector(vp) if vp else (0, 0)
        for j in range(vn):
            v = r.deref(vs + 4 * j)
            q = r.field(v, 1)
            vals[r.scalar(q, LONG) if q else 0] = r.string(r.field(v, 0))
        enums.append(FEnum(r.string(r.field(e, 0)), vals))
    rp = r.field(root, 4)                                # Schema.root_table
    root_name = r.string(r.field(r.deref(rp), 0)) if rp else ""
    by_name = {o.name: i for i, o in enumerate(objects)}
    return Schema(objects, enums, by_name.get(root_name, 0), by_name)


# ── Decoding a message ───────────────────────────────────────────────────────

def _enum_name(schema: Schema, ftype: FType, base: int, value):
    """An enum-typed scalar as its label (None if the schema has no name)."""
    if ftype.index >= 0 and base not in (FLOAT, DOUBLE) and ftype.index < len(schema.enums):
        return schema.enums[ftype.index].values.get(int(value))
    return None


def _scalar_value(schema: Schema, r: _Buf, p: int, ftype: FType, base: int):
    v = r.scalar(p, base)
    label = _enum_name(schema, ftype, base, v)
    return label if label is not None else v


def _struct(schema: Schema, r: _Buf, p: int, obj: FObject) -> dict:
    out = {}
    for f in obj.fields:
        q = p + f.offset
        b = f.type.base
        if b == OBJ:
            out[f.name] = _struct(schema, r, q, schema.objects[f.type.index])
        elif b in _SCALAR:
            out[f.name] = _scalar_value(schema, r, q, f.type, b)
    return out


def decode(schema: Schema, data: bytes, obj_index: int | None = None) -> dict:
    """The root table (or `obj_index`) of `data` as a dict: absent fields get
    their schema default; unions and arrays are skipped (owlet uses neither)."""
    r = _Buf(data)
    return _table(schema, r, r.deref(0), schema.objects[schema.root if obj_index is None
                                                        else obj_index])


def _table(schema: Schema, r: _Buf, t: int, obj: FObject) -> dict:
    out: dict[str, Any] = {}
    for f in obj.fields:
        b = f.type.base
        p = r.field(t, f.id)
        if b in _SCALAR:
            if p:
                out[f.name] = _scalar_value(schema, r, p, f.type, b)
            else:
                default = f.default_real if b in (FLOAT, DOUBLE) else f.default_integer
                if b == BOOL:
                    default = bool(default)
                label = _enum_name(schema, f.type, b, default)
                out[f.name] = label if label is not None else default
        elif not p:
            continue
        elif b == STRING:
            out[f.name] = r.string(p)
        elif b == OBJ:
            sub = schema.objects[f.type.index]
            out[f.name] = (_struct(schema, r, p, sub) if sub.is_struct
                           else _table(schema, r, r.deref(p), sub))
        elif b == VECTOR:
            start, n = r.vector(p)
            el = f.type.element
            if el in _SCALAR:
                out[f.name] = [r.scalar(start + _SIZE[el] * i, el) for i in range(n)]
            elif el == STRING:
                out[f.name] = [r.string(start + 4 * i) for i in range(n)]
            elif el == OBJ:
                sub = schema.objects[f.type.index]
                out[f.name] = ([_struct(schema, r, start + sub.bytesize * i, sub) for i in range(n)]
                               if sub.is_struct else
                               [_table(schema, r, r.deref(start + 4 * i), sub) for i in range(n)])
    return out
