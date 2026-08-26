"""
WPILib DataLog (`.wpilog`) reader — vendored.

Copyright (c) FIRST and other WPILib contributors.
Open Source Software; you can modify and/or share it under the terms of the
WPILib BSD license file in the root directory of that project.

Taken from WPILib's `datalog.py` (the same file the team's converter carries as
`datalog_breakaway.py`). Vendored rather than depended on because WPILib does
not publish it to PyPI — `robotpy` would pull the entire robot-side stack in for
one 300-line binary reader.

Three deliberate differences from upstream:

* **`msgpack` is imported lazily**, inside `getMsgPack()`. Upstream imports it at
  module scope, which would make it a hard dependency of the whole app for a
  record type `wpilog.py` skips anyway.
* **The `__main__` pretty-printer is gone.** It printed every record to stdout;
  62 million of those is not a thing this app ever wants.
* **`_readVarInt` is gone, folded into `__next__` as `int.from_bytes`** instead
  of a per-byte shift loop. It ran three times per record over a multi-gigabyte
  file, so it was the hottest line in the importer. Same little-endian unsigned
  result.
* **`getIntegerArray` uses `array("q")`, not `array("l")`.** Upstream's `"l"` is
  4 bytes on Windows (8 on macOS/Linux), so an int64 array read there comes back
  with twice the elements and garbage values — and the pit machine is the
  Windows one. `"q"` is 8 bytes everywhere.

Everything else — the record grammar, the control-record layout, the string
encoding — is upstream's and must stay that way.
"""

import array
import struct
from typing import List, SupportsBytes

__all__ = ["StartRecordData", "MetadataRecordData", "DataLogRecord",
           "DataLogReader", "DataLogIterator"]

floatStruct = struct.Struct("<f")
doubleStruct = struct.Struct("<d")

kControlStart = 0
kControlFinish = 1
kControlSetMetadata = 2


class StartRecordData:
    """
    Data contained in a start control record, as created by `DataLog.start()`.

    entry: Entry ID; used for this entry in every later record.
    name: Entry name.
    type: Type of the stored data, as a string, e.g. "double".
    metadata: Initial metadata.
    """

    __slots__ = ("entry", "name", "type", "metadata")

    def __init__(self, entry: int, name: str, type: str, metadata: str):
        self.entry = entry
        self.name = name
        self.type = type
        self.metadata = metadata


class MetadataRecordData:
    """Data contained in a set-metadata control record."""

    __slots__ = ("entry", "metadata")

    def __init__(self, entry: int, metadata: str):
        self.entry = entry
        self.metadata = metadata


class DataLogRecord:
    """A record in the data log: a control record (entry == 0) or a data record."""

    __slots__ = ("entry", "timestamp", "data")

    def __init__(self, entry: int, timestamp: int, data: SupportsBytes):
        self.entry = entry
        self.timestamp = timestamp
        self.data = data

    def isControl(self) -> bool:
        return self.entry == 0

    def _getControlType(self) -> int:
        return self.data[0]

    def isStart(self) -> bool:
        return (self.entry == 0 and len(self.data) >= 17
                and self._getControlType() == kControlStart)

    def isFinish(self) -> bool:
        return (self.entry == 0 and len(self.data) == 5
                and self._getControlType() == kControlFinish)

    def isSetMetadata(self) -> bool:
        return (self.entry == 0 and len(self.data) >= 9
                and self._getControlType() == kControlSetMetadata)

    def getStartData(self) -> StartRecordData:
        if not self.isStart():
            raise TypeError("not a start record")
        entry = int.from_bytes(self.data[1:5], byteorder="little", signed=False)
        name, pos = self._readInnerString(5)
        type, pos = self._readInnerString(pos)
        metadata = self._readInnerString(pos)[0]
        return StartRecordData(entry, name, type, metadata)

    def getFinishEntry(self) -> int:
        if not self.isFinish():
            raise TypeError("not a finish record")
        return int.from_bytes(self.data[1:5], byteorder="little", signed=False)

    def getSetMetadataData(self) -> MetadataRecordData:
        if not self.isSetMetadata():
            raise TypeError("not a set-metadata record")
        entry = int.from_bytes(self.data[1:5], byteorder="little", signed=False)
        metadata = self._readInnerString(5)[0]
        return MetadataRecordData(entry, metadata)

    def getBoolean(self) -> bool:
        if len(self.data) != 1:
            raise TypeError("not a boolean")
        return self.data[0] != 0

    def getInteger(self) -> int:
        if len(self.data) != 8:
            raise TypeError("not an integer")
        return int.from_bytes(self.data, byteorder="little", signed=True)

    def getFloat(self) -> float:
        if len(self.data) != 4:
            raise TypeError("not a float")
        return floatStruct.unpack(self.data)[0]

    def getDouble(self) -> float:
        if len(self.data) != 8:
            raise TypeError("not a double")
        return doubleStruct.unpack(self.data)[0]

    def getString(self) -> str:
        return str(self.data, encoding="utf-8")

    def getMsgPack(self):
        import msgpack                     # lazy — see the module docstring
        return msgpack.unpackb(self.data)

    def getBooleanArray(self) -> List[bool]:
        return [x != 0 for x in self.data]

    def getIntegerArray(self) -> array.array:
        if (len(self.data) % 8) != 0:
            raise TypeError("not an integer array")
        arr = array.array("q")
        arr.frombytes(self.data)
        return arr

    def getFloatArray(self) -> array.array:
        if (len(self.data) % 4) != 0:
            raise TypeError("not a float array")
        arr = array.array("f")
        arr.frombytes(self.data)
        return arr

    def getDoubleArray(self) -> array.array:
        if (len(self.data) % 8) != 0:
            raise TypeError("not a double array")
        arr = array.array("d")
        arr.frombytes(self.data)
        return arr

    def getStringArray(self) -> List[str]:
        size = int.from_bytes(self.data[:4], byteorder="little", signed=False)
        if size > ((len(self.data) - 4) / 4):
            raise TypeError("not a string array")
        arr = []
        pos = 4
        for _ in range(size):
            val, pos = self._readInnerString(pos)
            arr.append(val)
        return arr

    def _readInnerString(self, pos: int) -> tuple[str, int]:
        size = int.from_bytes(self.data[pos:pos + 4], byteorder="little",
                              signed=False)
        end = pos + 4 + size
        if end > len(self.data):
            raise TypeError("invalid string size")
        return str(self.data[pos + 4:end], encoding="utf-8"), end


class DataLogIterator:
    """
    DataLogReader iterator.

    `pos` is the byte offset of the next record, and the importer reads it to
    drive the progress bar — it is part of this class's contract, not an
    implementation detail.
    """

    __slots__ = ("buf", "pos")

    def __init__(self, buf: SupportsBytes, pos: int):
        self.buf = buf
        self.pos = pos

    def __iter__(self):
        return self

    def __next__(self) -> DataLogRecord:
        buf = self.buf
        pos = self.pos
        if len(buf) < (pos + 4):
            raise StopIteration
        head = buf[pos]
        entryLen = (head & 0x3) + 1
        sizeLen = ((head >> 2) & 0x3) + 1
        timestampLen = ((head >> 4) & 0x7) + 1
        headerLen = 1 + entryLen + sizeLen + timestampLen
        if len(buf) < (pos + headerLen):
            raise StopIteration
        a = pos + 1
        b = a + entryLen
        c = b + sizeLen
        d = c + timestampLen
        entry = int.from_bytes(buf[a:b], "little", signed=False)
        size = int.from_bytes(buf[b:c], "little", signed=False)
        timestamp = int.from_bytes(buf[c:d], "little", signed=False)
        if len(buf) < (d + size):
            raise StopIteration
        record = DataLogRecord(entry, timestamp, buf[d:d + size])
        self.pos = d + size
        return record


class DataLogReader:
    """Reads logs written by WPILib's DataLog class."""

    __slots__ = ("buf",)

    def __init__(self, buf: SupportsBytes):
        self.buf = buf

    def __bool__(self):
        return self.isValid()

    def isValid(self) -> bool:
        """True if the buffer carries a valid WPILOG header."""
        return (len(self.buf) >= 12
                and self.buf[:6] == b"WPILOG"
                and self.getVersion() >= 0x0100)

    def getVersion(self) -> int:
        """Version number; high byte major, low byte minor (1.0 == 0x0100)."""
        if len(self.buf) < 12:
            return 0
        return int.from_bytes(self.buf[6:8], byteorder="little", signed=False)

    def getExtraHeader(self) -> str:
        if len(self.buf) < 12:
            return ""
        size = int.from_bytes(self.buf[8:12], byteorder="little", signed=False)
        return str(self.buf[12:12 + size], encoding="utf-8")

    def __iter__(self) -> DataLogIterator:
        extraHeaderSize = int.from_bytes(self.buf[8:12], byteorder="little",
                                         signed=False)
        return DataLogIterator(self.buf, 12 + extraHeaderSize)
