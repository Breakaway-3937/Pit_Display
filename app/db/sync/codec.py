"""
zstd for everything that leaves a pit machine: bundles, team files, raw logs.

Python 3.14's own `compression.zstd`, so nothing new to bundle (the
self-check's `sync` line fails if a build lost it). Streaming, so a 4 GB log
never sits in memory, and multi-threaded with two cores left for the UI.

Measured on the dev Mac (10 cores), level 19: the 360 MB CAD model → 58 MB in
26 s; the Phoenix text export → 35x smaller at ~5 MB/s. Level 19 is the
default because the uplink at an event is the scarce thing, not the CPU;
`LEVEL_FAST` is for inputs so big that 19 would take the better part of an
hour on a pit laptop.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from compression import zstd
from compression.zstd import CompressionParameter as P

LEVEL = 19
LEVEL_FAST = 15
FAST_ABOVE = 1024 * 1024 * 1024
# Not worth storing compressed below this saving (a JPG, a PNG, a zip).
MIN_SAVING = 0.10
MAGIC = b"\x28\xb5\x2f\xfd"
_CHUNK = 4 * 1024 * 1024


def temp_path(suffix: str, dir: Path) -> Path:
    """A fresh, empty file in `dir`, with no handle left open.

    `tempfile.mkstemp()` hands back an open descriptor as well as the name;
    dropping it leaves the file open, and on Windows an open file can't be
    replaced or deleted. That locked synced CAD models out of their own temp
    file (`get_blob` renames its download onto the path) on the first
    Windows test. Always take temp paths from here.
    """
    fd, name = tempfile.mkstemp(suffix=suffix, dir=dir)
    os.close(fd)
    return Path(name)


def _workers() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def is_zstd(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == MAGIC


def compress_file(src: Path, dst: Path, level: int | None = None) -> tuple[str, int]:
    """Write `src` zstd-compressed to `dst`. Returns (sha256 of dst, dst bytes)."""
    size = os.path.getsize(src)
    if level is None:
        level = LEVEL_FAST if size > FAST_ABOVE else LEVEL
    comp = zstd.ZstdCompressor(options={
        P.compression_level: level, P.nb_workers: _workers(),
        P.enable_long_distance_matching: 1, P.checksum_flag: 1})
    h = hashlib.sha256()
    written = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        for block in iter(lambda: fin.read(_CHUNK), b""):
            out = comp.compress(block)
            if out:
                h.update(out)
                fout.write(out)
                written += len(out)
        out = comp.flush()
        h.update(out)
        fout.write(out)
        written += len(out)
    return h.hexdigest(), written


def decompress_file(src: Path, dst: Path) -> tuple[str, int]:
    """Undo `compress_file`. Returns (sha256 of the output, bytes)."""
    dec = zstd.ZstdDecompressor()
    h = hashlib.sha256()
    written = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        for block in iter(lambda: fin.read(_CHUNK), b""):
            out = dec.decompress(block)
            if out:
                h.update(out)
                fout.write(out)
                written += len(out)
    if not dec.eof:
        raise ValueError(f"{Path(src).name} is truncated")
    return h.hexdigest(), written
