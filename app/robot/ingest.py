"""
Streaming importer for Phoenix hoot exports.

One pass over the file, no intermediate storage. Measured on a real 3.85 GB
export: 62,118,776 raw rows in ~60 s, producing 4.87 M stored rows (105 MB).

## What it does, and why

The export samples every signal at a fixed rate whether or not it moved, so 92%
of it is repetition. The importer keeps only *changes*, moves signals that never
change into `session_constant`, folds fault bits into intervals, and builds
1-second rollups in the same pass.

This is lossless for any "what was the value at time T" question, which is the
only question the data can answer — see `verify_series()`.

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
from typing import Callable

from app.robot.parser import Sample, classify, coerce, parse_filename, parse_line

_BATCH = 100_000
_PROGRESS_EVERY = 2_000_000


class ImportError_(Exception):
    """Raised for problems the operator can act on."""


@dataclass
class ImportResult:
    session_id: int
    raw_rows: int = 0
    stored_rows: int = 0
    constants: int = 0
    faults: int = 0
    series: int = 0
    devices: int = 0
    signals: int = 0
    new_devices: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    elapsed_s: float = 0.0
    source_bytes: int = 0

    @property
    def compression(self) -> float:
        return self.raw_rows / self.stored_rows if self.stored_rows else 0.0


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
    Import one export. `progress(message, fraction)` is called periodically.

    Raises ImportError_ if the file was already imported or has no parseable
    rows — both are operator-fixable and neither should leave a partial session.
    """
    path = Path(path).resolve()
    db_path = Path(db_path)
    if not path.is_file():
        raise ImportError_(f"No such file: {path}")

    total_bytes = path.stat().st_size
    serial, started = parse_filename(path.name)
    conn = _connect(db_path)

    def say(msg: str, frac: float):
        if progress:
            progress(msg, frac)

    try:
        dup = conn.execute("SELECT id FROM log_session WHERE source_file = ?",
                           (str(path),)).fetchone()
        if dup is not None:
            raise ImportError_(
                f"Already imported as session {dup['id']}. Delete that session "
                f"first if you want to re-import."
            )

        cur = conn.execute(
            """INSERT INTO log_session
                   (source_file, source_name, source_kind, device_serial,
                    started_at, source_bytes)
               VALUES (?, ?, 'hoot', ?, ?, ?)""",
            (str(path), path.name, serial, started, total_bytes),
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
                c = conn.execute(
                    "INSERT INTO device (device_type, can_id) VALUES (?, ?)", key)
                dev_cache[key] = c.lastrowid
                new_devices.append(f"{dtype} {cid}")
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
        raw = stored = 0
        t_max = 0.0
        t0 = time.time()
        read = 0

        with open(path, "r", encoding="utf-8", errors="replace",
                  buffering=1 << 22) as fh:
            for line in fh:
                read += len(line)
                s: Sample | None = parse_line(line)
                if s is None:
                    continue
                raw += 1

                did = device_id(s.device_type, s.can_id)
                sid, klass = signal_id(s.device_type, s.signal)
                if klass == "meta":
                    continue                      # device Timestamp echo — drop
                ser = series_id(did, sid)
                series_meta[ser] = (did, sid, klass)

                num, label = coerce(s.raw)
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

                if s.t_ms > t_max:
                    t_max = s.t_ms

                a = agg.get(ser)
                if a is None:
                    agg[ser] = a = [0, num, num, 0.0, num]
                a[0] += 1
                if num < a[1]: a[1] = num
                if num > a[2]: a[2] = num
                a[3] += num
                a[4] = num

                b = roll.get((ser, s.t_ms // 1000))
                if b is None:
                    roll[(ser, s.t_ms // 1000)] = [num, num, num, 1]
                else:
                    if num < b[0]: b[0] = num
                    if num > b[1]: b[1] = num
                    b[2] += num; b[3] += 1

                # fault bits become intervals rather than samples
                if klass in ("fault", "sticky_fault"):
                    high = num > 0
                    if high and ser not in fault_open:
                        fault_open[ser] = s.t_ms
                    elif not high and ser in fault_open:
                        faults.append((session_id, did, sid,
                                       1 if klass == "sticky_fault" else 0,
                                       fault_open.pop(ser), s.t_ms))
                    continue          # fault bits never enter `sample`

                prev = last_val.get(ser)
                if prev is None or prev != num:
                    last_val[ser] = num
                    # `ord` disambiguates two different values in one millisecond
                    if last_ms.get(ser) == s.t_ms:
                        last_ord[ser] = last_ord.get(ser, 0) + 1
                    else:
                        last_ms[ser] = s.t_ms
                        last_ord[ser] = 0
                    pending.append((ser, s.t_ms, last_ord[ser], num))
                    stored += 1
                    if len(pending) >= _BATCH:
                        conn.executemany(
                            "INSERT OR REPLACE INTO samples.sample VALUES (?,?,?,?)",
                            pending)
                        pending.clear()

                if raw % _PROGRESS_EVERY == 0:
                    say(f"{raw:,} rows · {stored:,} stored", read / total_bytes)

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
                f"{path.name} contains no recognisable log lines — is it a "
                f"Phoenix 'detailed' export?")

        conn.commit()
        say("Done", 1.0)

        return ImportResult(
            session_id=session_id, raw_rows=raw, stored_rows=stored,
            constants=len(constants), faults=len(faults), series=len(ser_cache),
            devices=len(dev_cache), signals=len(sig_cache),
            new_devices=new_devices, duration_s=duration,
            elapsed_s=round(time.time() - t0, 1), source_bytes=total_bytes,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
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
