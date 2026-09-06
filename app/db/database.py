"""
Database singleton — SQLite connection + versioned migration runner.

Usage:
    from app.db import db
    rows = db.fetchall("SELECT * FROM foo WHERE id = ?", (1,))

    with db.transaction():
        db.execute("INSERT INTO foo ...")
        db.execute("INSERT INTO bar ...")

Registering a migration (in app/db/migrations.py or any module imported at
startup before init_db() is called):

    from app.db import register_migration

    @register_migration
    def _v1_create_matches(conn):
        conn.execute(
            "CREATE TABLE matches (id INTEGER PRIMARY KEY, ...)"
        )

Migrations run in registration order. The SQLite user_version pragma tracks
which have been applied — each migration is atomic; a failure rolls back
that step and leaves user_version unchanged.

## The attached samples database

Robot telemetry lives in a SECOND file, `data/pit_display_samples.db`, attached
as the `samples` schema. One imported log is ~105 MB, so keeping it out of the
main file means the pit display's own settings stay a 100 KB file that is quick
to back up, and reclaiming space is just deleting one file.

That file is deliberately disposable: its schema is re-created idempotently on
every startup (`_ensure_sample_schema`), NOT through the versioned migration
runner. Delete it to throw away every imported sample and the app still starts —
the metadata in the main database (sessions, devices, the CAN-id name map) is
untouched, and the logs can be re-imported from their originals.

Call init_db() once in main(), after init_config().
"""

import sqlite3
from pathlib import Path
from typing import Callable

from app import paths
from app.lazy_proxy import LazyProxy

# Writable, per-machine — never inside the app bundle. See app/paths.py.
_DB_PATH = paths.data("data", "pit_display.db")

# Bulk telemetry lives here, attached as the `samples` schema. See the module
# docstring — this file is disposable and rebuilt on demand.
_SAMPLES_SUFFIX = "_samples"

# Idempotent DDL for the attached database. Never versioned: it must be safe to
# run against both a fresh file and a fully populated one.
_SAMPLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples.sample (
    series_id INTEGER NOT NULL,
    t_ms      INTEGER NOT NULL,
    ord       INTEGER NOT NULL DEFAULT 0,
    v         REAL,
    PRIMARY KEY (series_id, t_ms, ord)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS samples.sample_1s (
    series_id INTEGER NOT NULL,
    t_s       INTEGER NOT NULL,
    v_min REAL, v_max REAL, v_avg REAL, n INTEGER,
    PRIMARY KEY (series_id, t_s)
) WITHOUT ROWID;
"""

_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = []


def register_migration(fn: Callable[[sqlite3.Connection], None]) -> Callable:
    """Decorator — appends fn to the migration list in import order."""
    _MIGRATIONS.append(fn)
    return fn


class _Database:

    def __init__(self, path: Path | str = _DB_PATH):
        path = Path(path)          # accept a str too — callers pass both
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # Telemetry file sits beside the main one, named after it so test
        # databases get their own rather than sharing the production samples.
        self._path = path
        self._samples_path = path.with_name(path.stem + _SAMPLES_SUFFIX + path.suffix)
        self._conn.execute("ATTACH DATABASE ? AS samples", (str(self._samples_path),))
        self._conn.execute("PRAGMA samples.journal_mode=WAL")

        self._run_migrations()
        self._ensure_sample_schema()

    # ── Migration runner ──────────────────────────────────────────────────────

    def _run_migrations(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for fn in _MIGRATIONS[current:]:
            with self._conn:  # commits on success, rolls back on exception
                fn(self._conn)
                current += 1
                # user_version can't be parameterized — safe because current is
                # always an integer we control, never from external input
                self._conn.execute(f"PRAGMA user_version = {current}")

    def _ensure_sample_schema(self) -> None:
        """
        Re-create the attached telemetry tables if they are missing.

        Runs on every startup and outside the version counter on purpose: the
        samples file is disposable, so deleting it must not leave the app in a
        state where user_version claims tables exist that do not.
        """
        with self._conn:
            self._conn.executescript(_SAMPLE_SCHEMA)

    @property
    def path(self) -> Path:
        """Location of the main database file."""
        return self._path

    @property
    def samples_path(self) -> Path:
        """Location of the attached telemetry file (safe to delete when closed)."""
        return self._samples_path

    def samples_bytes(self) -> int:
        return self._samples_path.stat().st_size if self._samples_path.exists() else 0

    # ── Query helpers ─────────────────────────────────────────────────────────

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_seq) -> sqlite3.Cursor:
        return self._conn.executemany(sql, params_seq)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def transaction(self) -> sqlite3.Connection:
        """Use as a context manager — commits on exit, rolls back on exception."""
        return self._conn

    @property
    def version(self) -> int:
        """Number of migrations that have been applied."""
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


db: _Database = LazyProxy("db", "init_db")  # type: ignore[assignment]


def init_db(path: Path | str | None = None) -> _Database:
    """
    Call once in main(), after init_config().
    Pass path to override the default location (useful in tests).
    """
    real = _Database(path or _DB_PATH)
    db._install(real)
    return real
