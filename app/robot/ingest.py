"""
The robot-log data pipeline: raw file off the robot → rows in the database.

Two shapes of file arrive in the pit, and both land in the same tables:

```
robot.hoot   ──owlet -f wpilog──▶  .wpilog ──┐
robot.wpilog ───────────────────────────────┴──▶ (t_ms, device, can id, signal, value)
                                                                 │
                                                                 ▼
                             device · signal · series · sample · session_constant · fault_event
```

`owlet.py` runs the closed-format extractor into scratch space (deleted after
the import) and `wpilog.py` reads the binary log. Each is a `_Source` here,
and the storage code below cannot tell them apart — which is the point. Add a
format by writing a `_Source`, not by touching the loop. **Nothing in this
pipeline ever reads or writes a text file**; there is no text stage.

**Which file to import.** The `.hoot` is the one to reach for: it is what the
robot writes, it needs no preparation, and it is the only one that carries the
controller serial. The `.wpilog` is the robot's own DataLogManager file and is
where the team's application signals live — robot states, PDH currents, shooter
setpoints — none of which exist in a hoot.

## What it does, and why

One pass over the file, no intermediate storage. The robot samples every signal
at a fixed rate whether or not it moved, so ~92% of a log is repetition. The
importer keeps only *changes*, moves signals that never change into
`session_constant`, folds fault bits into intervals, and builds 1-second rollups
in the same pass.

Measured on a real 3.85 GB log: 62,118,776 raw rows in ~60 s, producing
4.87 M stored rows (105 MB). This is lossless for any "what was the value at
time T" question, which is the only question the data can answer.

## Threading

Opens its own SQLite connection rather than borrowing the shared `db` singleton:
a 60-second write transaction on the shared connection would stall every UI
read. WAL mode lets the app keep reading the old snapshot until this commits.
Always run `import_log()` off the GUI thread.
"""

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from app.robot import owlet, wpilog
from app.robot.naming import classify, parse_log_name
from app.robot.wpilog import (
    APP_CAN_ID, APP_DEVICE_LABEL, APP_DEVICE_SUBSYSTEM, APP_DEVICE_TYPE,
)

_BATCH = 100_000

# Every quarter-million rows. Low enough that a one-minute match log still shows
# movement, high enough that a 62-million-row import spends no real time on it.
_PROGRESS_EVERY = 250_000

# A hoot has to be extracted before a single row can be read, and on a
# multi-gigabyte log that is a real fraction of the wall clock. The bar gives it
# the first fifth rather than sitting at zero through it.
_CONVERT_SHARE = 0.20

# One record from any source: (t_ms, device_type, can_id, signal, num, label).
# Exactly one of `num` / `label` is set: a number, or an enum label to intern.
Row = tuple[int, str, int, str, "float | None", "str | None"]


class ImportError_(Exception):
    """Raised for problems the operator can act on."""


@dataclass
class ImportResult:
    session_id: int
    source_kind: str = "hoot"
    raw_rows: int = 0
    stored_rows: int = 0
    constants: int = 0
    faults: int = 0
    series: int = 0
    devices: int = 0
    signals: int = 0
    new_devices: list[str] = field(default_factory=list)
    # Records the source could not store — string arrays, msgpack, raw bytes.
    # Surfaced rather than swallowed: a log that is 40% unstorable is a log
    # somebody needs to know about.
    skipped: int = 0
    # Duplicate copies of the device clock, one per extra device on the bus.
    # Expected and healthy — see `classify()` — but counted so the row totals
    # add up when somebody audits them.
    clock_dropped: int = 0
    enum_overflow: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    elapsed_s: float = 0.0
    source_bytes: int = 0

    @property
    def compression(self) -> float:
        return self.raw_rows / self.stored_rows if self.stored_rows else 0.0


# ── Sources ──────────────────────────────────────────────────────────────
#
# Each turns one file into a stream of `Row`. `fraction()` is how far through
# the file it is, read only when the progress bar ticks; `close()` releases
# whatever it holds — a file handle, an mmap, or a scratch directory holding a
# gigabyte of converted log.

class _McapSource:
    """owlet's `.mcap` of a hoot (app/robot/mcap.py: why not its `.wpilog`)."""

    kind = "hoot"

    def __init__(self, path: Path):
        from app.robot import mcap
        self._reader = mcap.Reader(path)
        try:
            self._reader.open()
        except mcap.McapError as err:
            raise ImportError_(str(err)) from err

    def rows(self) -> Iterator[Row]:
        yield from self._reader.rows()

    def fraction(self) -> float:
        return self._reader.fraction()

    def close(self) -> None:
        self._reader.close()

    @property
    def skipped(self) -> int:
        return self._reader.skipped

    @property
    def enum_overflow(self) -> set[str]:
        return self._reader.enum_overflow


class _WpilogSource:
    """A WPILib DataLog — the robot's own, or one owlet extracted from a hoot."""

    kind = "wpilog"

    def __init__(self, path: Path):
        self._reader = wpilog.Reader(path)
        # Eagerly, not on the first row: the header check is the only thing that
        # can tell a truncated or mis-named file from a real log, and it has to
        # fail before `log_session` gains a row for it.
        try:
            self._reader.open()
        except ValueError as err:
            raise ImportError_(str(err)) from err

    def rows(self) -> Iterator[Row]:
        yield from self._reader.rows()

    def fraction(self) -> float:
        return self._reader.fraction()

    @property
    def skipped(self) -> int:
        return self._reader.skipped

    @property
    def enum_overflow(self) -> set[str]:
        return self._reader.enum_overflow

    def close(self) -> None:
        self._reader.close()


class _HootSource:
    """
    A `.hoot`: extracted to a wpilog in a scratch directory, then read.

    The scratch copy is deleted in `close()` whatever happens. It is the size of
    the hoot and nothing reads it twice, so keeping it would double the disk cost
    of every import for no benefit.
    """

    kind = "hoot"

    def __init__(self, path: Path, say: Callable[[str, float], None]):
        try:
            self._dir = owlet.scratch_dir(path)
        except owlet.OwletError as err:
            raise ImportError_(str(err)) from err
        self._inner: _McapSource | None = None
        try:
            say(f"Extracting {path.name} with owlet…", 0.01)
            out = owlet.convert(
                path, self._dir / (path.stem + ".mcap"),
                progress=lambda line: say(f"owlet: {line}", 0.02))
        except owlet.OwletError as err:
            owlet.clear_scratch(self._dir)
            raise ImportError_(str(err)) from err
        except Exception:
            owlet.clear_scratch(self._dir)
            raise
        self._inner = _McapSource(out)

    def rows(self) -> Iterator[Row]:
        assert self._inner is not None
        yield from self._inner.rows()

    def fraction(self) -> float:
        inner = self._inner.fraction() if self._inner else 0.0
        return _CONVERT_SHARE + (1.0 - _CONVERT_SHARE) * inner

    @property
    def skipped(self) -> int:
        return self._inner.skipped if self._inner else 0

    @property
    def enum_overflow(self) -> set[str]:
        return self._inner.enum_overflow if self._inner else set()

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
        owlet.clear_scratch(self._dir)


def _open_source(path: Path, say: Callable[[str, float], None]):
    """The right `_Source` for this file, chosen by extension."""
    suffix = path.suffix.lower()
    if suffix == ".hoot":
        return _HootSource(path, say)
    if suffix == ".wpilog":
        return _WpilogSource(path)
    raise ImportError_(
        f"Don't know how to read {path.name}. Import a .hoot from the "
        f"controller or a .wpilog from the roboRIO.")


def _connect(main_path: Path) -> sqlite3.Connection:
    samples = main_path.with_name(main_path.stem + "_samples" + main_path.suffix)
    c = sqlite3.connect(str(main_path), timeout=60.0)
    c.row_factory = sqlite3.Row
    c.execute("ATTACH DATABASE ? AS samples", (str(samples),))
    # Bulk-load settings. journal_mode stays WAL so readers are never blocked;
    # synchronous=OFF is safe here because a failed import is simply re-run.
    c.execute("PRAGMA synchronous=OFF")
    c.execute("PRAGMA cache_size=-200000")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def import_log(
    path: str | Path,
    db_path: str | Path,
    progress: Callable[[str, float], None] | None = None,
) -> ImportResult:
    """
    Import one log — a `.hoot` or a `.wpilog`.

    `progress(message, fraction)` is called periodically. Raises ImportError_ if
    the file was already imported, cannot be read, or has no parseable rows —
    all operator-fixable, and none should leave a partial session behind.
    """
    path = Path(path).resolve()
    db_path = Path(db_path)
    if not path.is_file():
        raise ImportError_(f"No such file: {path}")

    def say(msg: str, frac: float):
        if progress:
            progress(msg, frac)

    total_bytes = path.stat().st_size
    meta = parse_log_name(path.name)
    conn = _connect(db_path)
    src = None

    try:
        # Before opening the source, not after: opening a .hoot runs owlet, and
        # spending four minutes extracting a log only to be told it was already
        # imported is the kind of thing that happens in a six-minute pit cycle.
        dup = conn.execute("SELECT id FROM log_session WHERE source_file = ?",
                           (str(path),)).fetchone()
        if dup is not None:
            raise ImportError_(
                f"Already imported as session {dup['id']}. Delete that session "
                f"first if you want to re-import."
            )

        src = _open_source(path, say)

        cur = conn.execute(
            """INSERT INTO log_session
                   (source_file, source_name, source_kind, device_serial,
                    started_at, match_key, source_bytes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(path), path.name, src.kind, meta.serial, meta.started,
             meta.match_key, total_bytes),
        )
        session_id = cur.lastrowid

        # ── caches: the whole point of the dictionary tables ──────────────
        dev_cache: dict[tuple[str, int], int] = {}
        sig_cache: dict[tuple[str, str], tuple[int, str]] = {}
        enum_cache: dict[int, dict[str, int]] = defaultdict(dict)
        ser_cache: dict[tuple[int, int], int] = {}
        new_devices: list[str] = []

        for r in conn.execute("SELECT id, device_type, can_id FROM device"):
            dev_cache[(r["device_type"], r["can_id"])] = r["id"]
        for r in conn.execute("SELECT id, device_type, name, signal_class FROM signal"):
            sig_cache[(r["device_type"], r["name"])] = (r["id"], r["signal_class"])
        for r in conn.execute("SELECT signal_id, code, label FROM signal_enum"):
            enum_cache[r["signal_id"]][r["label"]] = r["code"]

        def device_id(dtype: str, cid: int) -> int:
            key = (dtype, cid)
            if key not in dev_cache:
                if key == (APP_DEVICE_TYPE, APP_CAN_ID):
                    # The pseudo-device holding every signal with no CAN
                    # address. Created already named: nobody has to work out
                    # what "Robot -1" is, and an unnamed row would sit in the
                    # "still unnamed" badge forever asking the crew to name
                    # something that has no name to find.
                    c = conn.execute(
                        """INSERT INTO device (device_type, can_id, label, subsystem)
                           VALUES (?, ?, ?, ?)""",
                        (APP_DEVICE_TYPE, APP_CAN_ID, APP_DEVICE_LABEL,
                         APP_DEVICE_SUBSYSTEM))
                else:
                    c = conn.execute(
                        "INSERT INTO device (device_type, can_id) VALUES (?, ?)", key)
                    new_devices.append(f"{dtype} {cid}")
                dev_cache[key] = c.lastrowid
            return dev_cache[key]

        def signal_id(dtype: str, name: str) -> tuple[int, str]:
            key = (dtype, name)
            if key not in sig_cache:
                klass = classify(name)
                c = conn.execute(
                    """INSERT INTO signal (device_type, name, value_kind, signal_class)
                       VALUES (?, ?, 'num', ?)""", (dtype, name, klass))
                sig_cache[key] = (c.lastrowid, klass)
            return sig_cache[key]

        def series_id(did: int, sid: int) -> int:
            key = (did, sid)
            if key not in ser_cache:
                c = conn.execute(
                    """INSERT INTO series (session_id, device_id, signal_id)
                       VALUES (?, ?, ?)""", (session_id, did, sid))
                ser_cache[key] = c.lastrowid
            return ser_cache[key]

        # ── streaming state ───────────────────────────────────────────────
        last_val: dict[int, float] = {}
        last_ms: dict[int, int] = {}
        last_ord: dict[int, int] = {}
        agg: dict[int, list] = {}                     # [n, min, max, sum, last]
        roll: dict[tuple[int, int], list] = {}        # [min, max, sum, n]
        fault_open: dict[int, int] = {}               # series -> t_ms it went high
        faults: list[tuple] = []
        series_meta: dict[int, tuple[int, int, str]] = {}   # ser -> (dev, sig, class)
        pending: list[tuple] = []
        # The clock is reported by every device on the bus. The first series to
        # claim it is the one kept; every other device's copy is discarded, so
        # the values are in the database exactly once. See `classify()`.
        clock_ser: tuple[int, int] | None = None
        clock_dropped = 0
        raw = stored = 0
        t_max = 0
        t0 = time.time()

        for t_ms, dtype, can_id, name, num, label in src.rows():
            raw += 1

            did = device_id(dtype, can_id)
            sid, klass = signal_id(dtype, name)
            if klass == "meta":
                continue
            if klass == "clock":
                # Decided on (device, signal), *before* `series_id()` — calling
                # it first would create a `series` row for every device's copy
                # and then never write to it, leaving nine empty rows with NULL
                # statistics behind on a ten-motor log.
                if clock_ser is None:
                    clock_ser = (did, sid)
                elif (did, sid) != clock_ser:
                    clock_dropped += 1        # another device's copy of the clock
                    continue
            ser = series_id(did, sid)
            series_meta[ser] = (did, sid, klass)

            if label is not None:
                codes = enum_cache[sid]
                if label not in codes:
                    codes[label] = len(codes)
                    conn.execute(
                        "INSERT INTO signal_enum (signal_id, code, label) VALUES (?,?,?)",
                        (sid, codes[label], label))
                    conn.execute(
                        "UPDATE signal SET value_kind='enum' WHERE id=?", (sid,))
                num = float(codes[label])

            if t_ms > t_max:
                t_max = t_ms

            a = agg.get(ser)
            if a is None:
                agg[ser] = a = [0, num, num, 0.0, num]
            a[0] += 1
            if num < a[1]: a[1] = num
            if num > a[2]: a[2] = num
            a[3] += num
            a[4] = num

            b = roll.get((ser, t_ms // 1000))
            if b is None:
                roll[(ser, t_ms // 1000)] = [num, num, num, 1]
            else:
                if num < b[0]: b[0] = num
                if num > b[1]: b[1] = num
                b[2] += num; b[3] += 1

            # fault bits become intervals rather than samples
            if klass in ("fault", "sticky_fault"):
                high = num > 0
                if high and ser not in fault_open:
                    fault_open[ser] = t_ms
                elif not high and ser in fault_open:
                    faults.append((session_id, did, sid,
                                   1 if klass == "sticky_fault" else 0,
                                   fault_open.pop(ser), t_ms))
                continue          # fault bits never enter `sample`

            prev = last_val.get(ser)
            if prev is None or prev != num:
                last_val[ser] = num
                # `ord` disambiguates two different values in one millisecond
                if last_ms.get(ser) == t_ms:
                    last_ord[ser] = last_ord.get(ser, 0) + 1
                else:
                    last_ms[ser] = t_ms
                    last_ord[ser] = 0
                pending.append((ser, t_ms, last_ord[ser], num))
                stored += 1
                if len(pending) >= _BATCH:
                    conn.executemany(
                        "INSERT OR REPLACE INTO samples.sample VALUES (?,?,?,?)",
                        pending)
                    pending.clear()

            if raw % _PROGRESS_EVERY == 0:
                say(f"{raw:,} rows · {stored:,} stored", src.fraction())

        if pending:
            conn.executemany(
                "INSERT OR REPLACE INTO samples.sample VALUES (?,?,?,?)", pending)

        say("Finalising…", 0.97)

        # fault bits still high at end of log stay open (t_ms_end NULL)
        for ser, t_start in fault_open.items():
            did, sid, klass = series_meta[ser]
            faults.append((session_id, did, sid,
                           1 if klass == "sticky_fault" else 0, t_start, None))
        if faults:
            conn.executemany(
                """INSERT INTO fault_event
                       (session_id, device_id, signal_id, sticky, t_ms_start, t_ms_end)
                   VALUES (?,?,?,?,?,?)""", faults)

        # signals that never moved leave the hot table entirely
        constants = [s for s, a in agg.items()
                     if a[1] == a[2] and series_meta[s][2] not in ("fault", "sticky_fault")]
        const_set = set(constants)
        if constants:
            # Enum constants carry their label as well as their code, so a pit
            # screen can render "ForwardLimit = Open" without a second join.
            label_for = {sid: {c: l for l, c in codes.items()}
                         for sid, codes in enum_cache.items()}
            rows = []
            for s in constants:
                did_, sid_, _ = series_meta[s]
                v = agg[s][4]
                rows.append((session_id, did_, sid_, v,
                             label_for.get(sid_, {}).get(int(v))))
            conn.executemany(
                """INSERT OR REPLACE INTO session_constant
                       (session_id, device_id, signal_id, v, v_text) VALUES (?,?,?,?,?)""",
                rows)
            conn.executemany("DELETE FROM samples.sample WHERE series_id = ?",
                             [(s,) for s in constants])

        conn.executemany(
            """UPDATE series SET n_raw=?, n_stored=?, v_min=?, v_max=?, v_mean=?, v_last=?
               WHERE id=?""",
            [(a[0], 0 if s in const_set else 1, a[1], a[2], a[3] / a[0], a[4], s)
             for s, a in agg.items()])

        conn.executemany(
            "INSERT OR REPLACE INTO samples.sample_1s VALUES (?,?,?,?,?,?)",
            [(k[0], k[1], v[0], v[1], v[2] / v[3], v[3])
             for k, v in roll.items() if k[0] not in const_set])

        duration = t_max / 1000.0
        conn.execute(
            """UPDATE log_session SET raw_rows=?, stored_rows=?, duration_s=?
               WHERE id=?""", (raw, stored, duration, session_id))

        if raw == 0:
            conn.rollback()
            raise ImportError_(
                f"{path.name} contains no recognisable log records: a log has "
                f"to carry entries the robot actually wrote.")

        conn.commit()
        say("Done", 1.0)

        return ImportResult(
            session_id=session_id, source_kind=src.kind,
            raw_rows=raw, stored_rows=stored,
            constants=len(constants), faults=len(faults), series=len(ser_cache),
            devices=len(dev_cache), signals=len(sig_cache),
            new_devices=new_devices, skipped=src.skipped,
            clock_dropped=clock_dropped,
            enum_overflow=sorted(src.enum_overflow), duration_s=duration,
            elapsed_s=round(time.time() - t0, 1), source_bytes=total_bytes,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        if src is not None:
            src.close()
        conn.close()


def delete_session(session_id: int, db_path: str | Path) -> None:
    """Remove a session and everything hanging off it, samples included."""
    conn = _connect(Path(db_path))
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM series WHERE session_id = ?", (session_id,))]
        conn.executemany("DELETE FROM samples.sample WHERE series_id=?",
                         [(i,) for i in ids])
        conn.executemany("DELETE FROM samples.sample_1s WHERE series_id=?",
                         [(i,) for i in ids])
        conn.execute("DELETE FROM log_session WHERE id=?", (session_id,))
        conn.commit()
    finally:
        conn.close()
