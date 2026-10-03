"""
Import robot logs in worker **processes**, then merge each into the database in
one short step.

Brayden, 2026-10-03: a batch of 66 logs took hours, locked sync out of the
database, and slowed the whole app, while Task Manager showed ~200 MB and one
busy core on a machine with 16 GB to give. Three causes, measured:

1. `import_log` held one write transaction for a whole log, minutes for a big
   hoot, so every other writer (sync first) got "database is locked".
2. It ran as a thread in the app's process: Python runs one thread at a time,
   so the screens shared a core with the parse.
3. One log at a time, on one core.

So each log is parsed by `stage_one()` in its own process (true parallelism,
no GIL shared with the GUI), into a **private scratch database** with the full
schema: nobody else's lock, nobody waits on it. The finished log is packed as
a sync bundle (`app/db/sync/bundle.py`: names not ids, enum codes remapped,
audited lossless) and `merge()` lands it in the real database in one short
transaction (0.7 s for a 15-minute log, against 1.6 s + to parse it), then
makes it what a normal import would have made: a local session with its
file, fingerprint and sync rows queued.

`workers()` sizes the pool to the machine: every core but one for the
screens, and no more processes than memory holds (~1 GB each, generously).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict
from pathlib import Path

_PER_WORKER_BYTES = 1024 ** 3     # a worker's ceiling: page cache + one owlet extraction's reader


def _total_memory() -> int:
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _Status()
            st.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullAvailPhys)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return 4 * 1024 ** 3


def workers(jobs: int) -> int:
    """How many logs to parse at once: cores minus one, within memory."""
    override = os.environ.get("PIT_IMPORT_WORKERS")
    if override and override.isdigit():
        return max(1, min(int(override), jobs))
    cores = os.cpu_count() or 2
    by_memory = max(1, _total_memory() // _PER_WORKER_BYTES // 2)
    return max(1, min(cores - 1, by_memory, jobs, 12))


def stage_one(path: str, fp, work: str) -> dict:
    """
    **In a worker process.** Import `path` into a fresh private database under
    `work`, pack it as a bundle there, and return
    `{"bundle": path, "result": ImportResult as a dict}`. Raises ImportError_
    like `import_log` (it is `import_log`, on a private database).
    """
    work_dir = Path(work)
    data = work_dir / "db"
    import app.db.migrations  # noqa: F401  (registers by side effect)
    from app.db.database import _Database
    from app.db.sync import bundle
    from app.robot.ingest import import_log
    # Not the app's database singleton: a pool reuses this process for the
    # next log, and each gets its own fresh, fully migrated database.
    db = _Database(data / "staging.db")
    try:
        result = import_log(Path(path), db.path, archive_path=path, fp=fp)
        gz = bundle.build(db.path, result.session_id, work_dir)
    finally:
        db.close()
        shutil.rmtree(data, ignore_errors=True)
    return {"bundle": str(gz), "result": asdict(result)}


def work_dir() -> Path:
    """A scratch folder for one staged import (the caller deletes it)."""
    parent = os.environ.get("PIT_LOG_SCRATCH") or tempfile.gettempdir()
    Path(parent).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".pit_stage_", dir=parent))


def merge(db_path: Path, gz: Path, source: Path, archive_path: Path | None) -> int:
    """
    Land a staged log in the real database: one short transaction (the
    bundle import), then make it a local session (its file, not `sync:`) and
    queue what a normal import queues for sync: the session and any device
    this log added. Returns the session id.
    """
    from app.db.sync import bundle
    db_path = Path(db_path)
    scratch = Path(tempfile.mkdtemp(prefix=".pit_merge_", dir=str(gz.parent)))
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    try:
        max_dev = conn.execute("SELECT COALESCE(MAX(id), 0) FROM device").fetchone()[0]
        conn.close()
        sid = bundle.import_bundle(db_path, gz, scratch)
        conn = sqlite3.connect(str(db_path), timeout=60.0)
        conn.execute("BEGIN")
        uid = conn.execute("SELECT uid FROM log_session WHERE id = ?", (sid,)).fetchone()[0]
        where = str(source)
        if conn.execute("SELECT 1 FROM log_session WHERE source_file = ? AND id <> ?",
                        (where, sid)).fetchone():
            where = f"{source}#{uid[:8]}"              # the path's UNIQUE: another file was here
        conn.execute("UPDATE log_session SET source_file = ?, archive_path = ?, origin = NULL, "
                     "origin_name = NULL WHERE id = ?",
                     (where, str(archive_path) if archive_path else None, sid))
        queue = [("log_session", uid)] + [
            ("device", r[0]) for r in conn.execute(
                "SELECT uid FROM device WHERE id > ? AND uid IS NOT NULL", (max_dev,))]
        conn.executemany(
            """INSERT INTO sync_outbox (tbl, uid, op) VALUES (?, ?, 'upsert')
               ON CONFLICT (tbl, uid) DO UPDATE SET op = 'upsert', at = excluded.at,
                                                    rev = rev + 1""", queue)
        conn.execute("COMMIT")
        return sid
    finally:
        conn.close()
        shutil.rmtree(scratch, ignore_errors=True)


def pool(n: int):
    """A pool of `n` worker processes. Spawned, never forked (Qt and SQLite
    don't survive a fork), and a frozen build's workers skip the launch
    splash (they re-run the executable, see main.py)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    os.environ["PYINSTALLER_SUPPRESS_SPLASH_SCREEN"] = "1"
    return ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"))


def result_from(d: dict):
    """An `ImportResult` back from a worker's dict."""
    from app.robot.ingest import ImportResult
    return ImportResult(**d)


def ping() -> int:
    """**In a worker process.** Its pid: proof a worker started (`--self-check`)."""
    return os.getpid()
