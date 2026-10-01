"""
A robot log session as one file: how imported telemetry moves between machines.

**Why a bundle, not the original log.** Re-importing the original on another
machine would need owlet (a `.hoot` needs CTRE's converter, per platform)
and minutes of parsing; the home server may not be able to run it at all. The
bundle is what the import *produced* (audited lossless against the source,
DATABASE.md "Losslessness, audited"): a SQLite file with the session, its
series, constants and faults, every reference spelled out by name
(`device_type`, `can_id`, signal `name`) instead of this machine's integer
ids, and the samples **as columns** (`columns.py`), the whole file zstd'd.

    bundle_meta   k, v                 format = 2, session uid
    session       the log_session row, minus paths and local ids
    device        device_type, can_id, label, subsystem, notes
    signal        device_type, name, value_kind, signal_class, unit
    signal_enum   sig_type, sig_name, code, label
    series        skey, device_type, can_id, sig_type, sig_name, n_raw, …
    constant      device_type, can_id, sig_type, sig_name, v, v_text
    fault         device_type, can_id, sig_type, sig_name, sticky, t_ms_start, t_ms_end
    colblob       name, data           columns.encode_samples() / encode_rollup()
                                       streams: `sample` and `sample_1s`

**Size, measured on the real 3.85 GB Phoenix export: 3.1 MB** (format 1, the
same tables row by row and gzipped, was 14.7 MB). `open_bundle()` reads both
formats and hands back the tables with `sample` / `sample_1s` materialised,
so the import below is format-blind.

**Enum codes are per machine** (ingest interns labels in order of first
appearance), so an import maps every bundle code to this machine's code for
the same label and rewrites the values that carry one. `sample_1s` for an enum
series is left as-is: a mean of codes means nothing on either machine.

The home server reads the same file (home/HANDOFF.md). **Change the format
only with a new `FORMAT` and a reader for the old one.**
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from pathlib import Path

from app.db.sync import codec, columns

FORMAT = 2
READS = (1, 2)
SUFFIX = ".pitlog.zst"


def _samples_path(main: Path) -> Path:
    return main.with_name(main.stem + "_samples" + main.suffix)


def build(db_path: Path, session_id: int, out_dir: Path) -> Path:
    """Write the session's bundle into `out_dir`. Returns the .zst path."""
    db_path = Path(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = codec.temp_path(".db", out_dir)
    raw.unlink()
    conn = sqlite3.connect(str(raw))
    try:
        conn.execute("ATTACH DATABASE ? AS m", (str(db_path),))
        conn.execute("ATTACH DATABASE ? AS s", (str(_samples_path(db_path)),))
        sid = int(session_id)
        uid = conn.execute("SELECT uid FROM m.log_session WHERE id = ?", (sid,)).fetchone()
        if uid is None:
            raise ValueError(f"no session {sid}")
        used_dev = ("SELECT device_id FROM m.series WHERE session_id = :s "
                    "UNION SELECT device_id FROM m.session_constant WHERE session_id = :s "
                    "UNION SELECT device_id FROM m.fault_event WHERE session_id = :s")
        used_sig = ("SELECT signal_id FROM m.series WHERE session_id = :s "
                    "UNION SELECT signal_id FROM m.session_constant WHERE session_id = :s "
                    "UNION SELECT signal_id FROM m.fault_event WHERE session_id = :s")
        p = {"s": sid}
        conn.executescript("CREATE TABLE bundle_meta (k TEXT PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO bundle_meta VALUES (?, ?)",
                         [("format", str(FORMAT)), ("uid", uid[0])])
        conn.execute(
            """CREATE TABLE session AS SELECT uid, source_name, source_kind, device_serial,
                   started_at, duration_s, raw_rows, stored_rows, source_bytes,
                   match_key, notes, keep, imported_at
               FROM m.log_session WHERE id = :s""", p)
        conn.execute(
            f"""CREATE TABLE device AS SELECT device_type, can_id, label, subsystem, notes
                FROM m.device WHERE id IN ({used_dev})""", p)
        conn.execute(
            f"""CREATE TABLE signal AS SELECT device_type, name, value_kind, signal_class, unit
                FROM m.signal WHERE id IN ({used_sig})""", p)
        conn.execute(
            f"""CREATE TABLE signal_enum AS
                SELECT g.device_type AS sig_type, g.name AS sig_name, e.code, e.label
                FROM m.signal_enum e JOIN m.signal g ON g.id = e.signal_id
                WHERE e.signal_id IN ({used_sig})""", p)
        conn.execute(
            """CREATE TABLE series AS
               SELECT se.id AS skey, d.device_type, d.can_id,
                      g.device_type AS sig_type, g.name AS sig_name,
                      se.n_raw, se.n_stored, se.v_min, se.v_max, se.v_mean, se.v_last
               FROM m.series se JOIN m.device d ON d.id = se.device_id
                                JOIN m.signal g ON g.id = se.signal_id
               WHERE se.session_id = :s""", p)
        conn.execute(
            """CREATE TABLE constant AS
               SELECT d.device_type, d.can_id, g.device_type AS sig_type, g.name AS sig_name,
                      c.v, c.v_text
               FROM m.session_constant c JOIN m.device d ON d.id = c.device_id
                                         JOIN m.signal g ON g.id = c.signal_id
               WHERE c.session_id = :s""", p)
        conn.execute(
            """CREATE TABLE fault AS
               SELECT d.device_type, d.can_id, g.device_type AS sig_type, g.name AS sig_name,
                      f.sticky, f.t_ms_start, f.t_ms_end
               FROM m.fault_event f JOIN m.device d ON d.id = f.device_id
                                    JOIN m.signal g ON g.id = f.signal_id
               WHERE f.session_id = :s""", p)
        streams = columns.encode_samples(conn.execute(
            """SELECT a.series_id, a.t_ms, a.ord, a.v FROM s.sample a
               WHERE a.series_id IN (SELECT id FROM m.series WHERE session_id = :s)
               ORDER BY a.series_id, a.t_ms, a.ord""", p))
        streams.update(columns.encode_rollup(conn.execute(
            """SELECT a.series_id, a.t_s, a.v_min, a.v_max, a.v_avg, a.n FROM s.sample_1s a
               WHERE a.series_id IN (SELECT id FROM m.series WHERE session_id = :s)
               ORDER BY a.series_id, a.t_s""", p)))
        conn.execute("CREATE TABLE colblob (name TEXT PRIMARY KEY, data BLOB NOT NULL)")
        conn.executemany("INSERT INTO colblob VALUES (?, ?)", streams.items())
        conn.commit()
        conn.execute("DETACH DATABASE m")
        conn.execute("DETACH DATABASE s")
        conn.execute("VACUUM")
    finally:
        conn.close()

    out = out_dir / f"{uid[0]}{SUFFIX}"
    try:
        codec.compress_file(raw, out)
    finally:
        raw.unlink(missing_ok=True)
    return out


def open_bundle(path: Path, out_dir: Path) -> Path:
    """
    Any bundle format → a plain SQLite file with `sample` and `sample_1s` as
    tables. The caller deletes it. Format 1 was gzip with the tables in it;
    format 2 is zstd with the samples as columns.
    """
    out = codec.temp_path(".db", out_dir)
    if codec.is_zstd(path):
        codec.decompress_file(path, out)
    else:
        with gzip.open(path, "rb") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    conn = sqlite3.connect(str(out))
    try:
        fmt = int(dict(conn.execute("SELECT k, v FROM bundle_meta").fetchall()).get("format", 0))
        if fmt not in READS:
            raise ValueError(f"bundle format {fmt}; this build reads {READS}")
        if fmt == 2:
            streams = dict(conn.execute("SELECT name, data FROM colblob").fetchall())
            conn.execute("CREATE TABLE sample (skey INTEGER, t_ms INTEGER, ord INTEGER, v REAL)")
            conn.executemany("INSERT INTO sample VALUES (?, ?, ?, ?)",
                             columns.decode_samples(streams))
            conn.execute("CREATE TABLE sample_1s (skey INTEGER, t_s INTEGER, v_min REAL, "
                         "v_max REAL, v_avg REAL, n INTEGER)")
            conn.executemany("INSERT INTO sample_1s VALUES (?, ?, ?, ?, ?, ?)",
                             columns.decode_rollup(streams))
            conn.commit()
    except Exception:
        conn.close()
        out.unlink(missing_ok=True)
        raise
    conn.close()
    return out


def import_bundle(db_path: Path, gz: Path, scratch: Path) -> int:
    """
    Land a bundle in this machine's database. Returns the local session id.
    Idempotent: a session already here (same uid) is left alone. Writes with
    the sync guard up, so nothing it inserts is pushed back.
    """
    db_path = Path(db_path)
    bundle_db = open_bundle(gz, scratch)
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS samples", (str(_samples_path(db_path)),))
        conn.execute("ATTACH DATABASE ? AS b", (str(bundle_db),))
        conn.execute("PRAGMA foreign_keys=ON")
        meta = dict(conn.execute("SELECT k, v FROM b.bundle_meta").fetchall())
        uid = meta["uid"]
        have = conn.execute("SELECT id FROM log_session WHERE uid = ?", (uid,)).fetchone()
        if have is not None:
            return int(have[0])

        conn.execute("BEGIN")
        conn.execute("UPDATE sync_guard SET applying = 1 WHERE id = 1")
        s = conn.execute("SELECT * FROM b.session").fetchone()
        cur = conn.execute(
            """INSERT INTO log_session
                   (source_file, source_name, source_kind, device_serial, started_at,
                    duration_s, raw_rows, stored_rows, source_bytes, match_key, notes,
                    keep, uid)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"sync:{uid}", s["source_name"], s["source_kind"], s["device_serial"],
             s["started_at"], s["duration_s"], s["raw_rows"], s["stored_rows"],
             s["source_bytes"], s["match_key"], s["notes"], s["keep"] or 0, uid))
        sid = cur.lastrowid

        # Devices and signals are shared dictionaries: add what's missing,
        # never overwrite (a device's name arrives as its own synced row).
        conn.execute(
            """INSERT OR IGNORE INTO device (device_type, can_id, label, subsystem, notes, uid)
               SELECT device_type, can_id, label, subsystem, notes,
                      'dev:' || device_type || ':' || can_id FROM b.device""")
        conn.execute(
            """INSERT OR IGNORE INTO signal (device_type, name, value_kind, signal_class, unit)
               SELECT device_type, name, value_kind, signal_class, unit FROM b.signal""")
        conn.execute(
            """UPDATE signal SET value_kind = 'enum'
               WHERE (device_type, name) IN (SELECT device_type, name FROM b.signal
                                              WHERE value_kind = 'enum')""")

        dev = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT device_type, can_id, id FROM device")}
        sig = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT device_type, name, id FROM signal")}

        # Enum codes: bundle code → this machine's code for the same label.
        cmap: dict[int, dict[float, float]] = {}
        for e in conn.execute("SELECT sig_type, sig_name, code, label FROM b.signal_enum").fetchall():
            local_sig = sig[(e[0], e[1])]
            row = conn.execute("SELECT code FROM signal_enum WHERE signal_id = ? AND label = ?",
                               (local_sig, e[3])).fetchone()
            if row is None:
                nxt = conn.execute("SELECT COALESCE(MAX(code) + 1, 0) FROM signal_enum "
                                   "WHERE signal_id = ?", (local_sig,)).fetchone()[0]
                conn.execute("INSERT INTO signal_enum (signal_id, code, label) VALUES (?, ?, ?)",
                             (local_sig, nxt, e[3]))
                row = (nxt,)
            if row[0] != e[2]:
                cmap.setdefault(local_sig, {})[float(e[2])] = float(row[0])

        def remap(sig_id: int, v):
            m = cmap.get(sig_id)
            return m.get(float(v), v) if (m and v is not None) else v

        conn.execute("CREATE TEMP TABLE smap (skey INTEGER PRIMARY KEY, sid INTEGER, sig INTEGER)")
        for r in conn.execute("SELECT * FROM b.series").fetchall():
            d = dev[(r["device_type"], r["can_id"])]
            g = sig[(r["sig_type"], r["sig_name"])]
            new = conn.execute(
                """INSERT INTO series (session_id, device_id, signal_id, n_raw, n_stored,
                                       v_min, v_max, v_mean, v_last)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, d, g, r["n_raw"], r["n_stored"], remap(g, r["v_min"]),
                 remap(g, r["v_max"]), r["v_mean"], remap(g, r["v_last"]))).lastrowid
            conn.execute("INSERT INTO temp.smap VALUES (?, ?, ?)", (r["skey"], new, g))

        for r in conn.execute("SELECT * FROM b.constant").fetchall():
            g = sig[(r["sig_type"], r["sig_name"])]
            conn.execute(
                """INSERT OR REPLACE INTO session_constant (session_id, device_id, signal_id, v, v_text)
                   VALUES (?, ?, ?, ?, ?)""",
                (sid, dev[(r["device_type"], r["can_id"])], g, remap(g, r["v"]), r["v_text"]))
        for r in conn.execute("SELECT * FROM b.fault").fetchall():
            conn.execute(
                """INSERT INTO fault_event (session_id, device_id, signal_id, sticky,
                                            t_ms_start, t_ms_end) VALUES (?, ?, ?, ?, ?, ?)""",
                (sid, dev[(r["device_type"], r["can_id"])], sig[(r["sig_type"], r["sig_name"])],
                 r["sticky"], r["t_ms_start"], r["t_ms_end"]))

        conn.execute(
            """INSERT OR REPLACE INTO samples.sample (series_id, t_ms, ord, v)
               SELECT m.sid, a.t_ms, a.ord, a.v FROM b.sample a JOIN temp.smap m ON m.skey = a.skey""")
        conn.execute(
            """INSERT OR REPLACE INTO samples.sample_1s (series_id, t_s, v_min, v_max, v_avg, n)
               SELECT m.sid, a.t_s, a.v_min, a.v_max, a.v_avg, a.n
               FROM b.sample_1s a JOIN temp.smap m ON m.skey = a.skey""")
        if cmap:
            conn.execute("CREATE TEMP TABLE cmap (series_id INTEGER, f REAL, t REAL, "
                         "PRIMARY KEY (series_id, f))")
            for r in conn.execute("SELECT sid, sig FROM temp.smap").fetchall():
                for f, t in cmap.get(r[1], {}).items():
                    conn.execute("INSERT INTO temp.cmap VALUES (?, ?, ?)", (r[0], f, t))
            conn.execute(
                """UPDATE samples.sample
                   SET v = (SELECT c.t FROM temp.cmap c
                            WHERE c.series_id = sample.series_id AND c.f = sample.v)
                   WHERE EXISTS (SELECT 1 FROM temp.cmap c
                                 WHERE c.series_id = sample.series_id AND c.f = sample.v)""")

        conn.execute("UPDATE sync_guard SET applying = 0 WHERE id = 1")
        conn.commit()
        return int(sid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        bundle_db.unlink(missing_ok=True)


def delete_session(db_path: Path, session_id: int) -> None:
    """Remove a session and its samples, guard up (a pulled delete)."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    try:
        conn.execute("ATTACH DATABASE ? AS samples", (str(_samples_path(db_path)),))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN")
        conn.execute("UPDATE sync_guard SET applying = 1 WHERE id = 1")
        ids = [(r[0],) for r in conn.execute(
            "SELECT id FROM series WHERE session_id = ?", (session_id,))]
        conn.executemany("DELETE FROM samples.sample WHERE series_id = ?", ids)
        conn.executemany("DELETE FROM samples.sample_1s WHERE series_id = ?", ids)
        conn.execute("DELETE FROM log_session WHERE id = ?", (session_id,))
        conn.execute("UPDATE sync_guard SET applying = 0 WHERE id = 1")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
