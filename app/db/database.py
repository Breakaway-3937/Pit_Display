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

Call init_db() once in main(), after init_config().
"""

import sqlite3
from pathlib import Path
from typing import Callable

from app.lazy_proxy import LazyProxy

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "pit_display.db"

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
        self._run_migrations()

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
