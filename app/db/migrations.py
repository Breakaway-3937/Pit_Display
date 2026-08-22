"""
Schema migrations, in registration order.

Imported for its side effects before `init_db()` runs — see main.py. Each
function is one atomic step; the SQLite user_version pragma tracks which have
been applied. Never edit a migration that has shipped; add a new one.
"""

import sqlite3

from app.db import register_migration


@register_migration
def _v1_led_presets(conn: sqlite3.Connection) -> None:
    """Operator-saved LED looks, on top of the built-ins in app/leds/effects.py."""
    conn.execute(
        """
        CREATE TABLE led_presets (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL UNIQUE,
            mode       INTEGER NOT NULL,
            speed      INTEGER NOT NULL DEFAULT 128,
            brightness INTEGER NOT NULL DEFAULT 180,
            color      TEXT,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


@register_migration
def _v2_music_library(conn: sqlite3.Connection) -> None:
    """The scanned local library. `path` is the natural key — one row per file."""
    conn.execute(
        """
        CREATE TABLE tracks (
            id        INTEGER PRIMARY KEY,
            path      TEXT    NOT NULL UNIQUE,
            title     TEXT    NOT NULL,
            artist    TEXT    NOT NULL DEFAULT '',
            album     TEXT    NOT NULL DEFAULT '',
            duration  REAL    NOT NULL DEFAULT 0,
            explicit  INTEGER NOT NULL DEFAULT 0,
            missing   INTEGER NOT NULL DEFAULT 0,
            added_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX idx_tracks_title  ON tracks(title)")
    conn.execute("CREATE INDEX idx_tracks_artist ON tracks(artist)")


@register_migration
def _v3_playlists(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE playlists (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL UNIQUE,
            app_mode   TEXT,   -- 'standard'|'judges'|'lunch': auto-select on mode change
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE playlist_items (
            id          INTEGER PRIMARY KEY,
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            track_id    INTEGER NOT NULL REFERENCES tracks(id)    ON DELETE CASCADE,
            position    INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_items_playlist ON playlist_items(playlist_id, position)"
    )


@register_migration
def _v4_eq_presets(conn: sqlite3.Connection) -> None:
    """
    Ten band gains in dB, stored as a comma-separated string.

    A child table per band would be tidier, but these are always read and
    written as a complete set of ten — never queried per band — so a column
    keeps the code that applies them to the engine a single statement.
    """
    conn.execute(
        """
        CREATE TABLE eq_presets (
            id       INTEGER PRIMARY KEY,
            name     TEXT    NOT NULL UNIQUE,
            preamp   REAL    NOT NULL DEFAULT 0,
            gains    TEXT    NOT NULL,
            built_in INTEGER NOT NULL DEFAULT 0
        )
        """
    )


@register_migration
def _v5_drop_explicit(conn: sqlite3.Connection) -> None:
    """
    Drop the explicit-content flag.

    The team pre-curates what goes in the pit music folder, so filtering at
    playback time was solving a problem that no longer exists. Requires SQLite
    3.35+ for DROP COLUMN, which ships with every Python we support.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    if "explicit" in cols:
        conn.execute("ALTER TABLE tracks DROP COLUMN explicit")


@register_migration
def _v6_admin(conn: sqlite3.Connection) -> None:
    """
    Admin credential store — a single row holding the PBKDF2 password hash.

    This gates the advanced LED and equaliser controls so a curious visitor
    cannot re-tune the pit while nobody is looking. It is a UI lock, not a
    security boundary: the database sits on the pit machine's disk and anyone
    with the file can replace the row. Do not reuse a password that matters.
    """
    conn.execute(
        """
        CREATE TABLE admin_credential (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            algo       TEXT    NOT NULL,
            iterations INTEGER NOT NULL,
            salt       TEXT    NOT NULL,
            hash       TEXT    NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


@register_migration
def _v7_robot_logs(conn: sqlite3.Connection) -> None:
    """
    Robot telemetry metadata.

    The bulk samples live in the attached `samples` database (see
    app/db/database.py) — everything here is small, and all of it is worth
    keeping forever even after the raw samples are evicted.

    Measured against a real 3.85 GB Phoenix export: 62,118,776 raw rows became
    4.87M change-rows (105 MB), 1,074 series, 209 signals, 13 devices.
    """
    conn.execute(
        """
        CREATE TABLE log_session (
            id            INTEGER PRIMARY KEY,
            -- absolute path of the imported file; UNIQUE so a second import of
            -- the same log fails loudly instead of silently doubling the data
            source_file   TEXT    NOT NULL UNIQUE,
            source_name   TEXT    NOT NULL,
            source_kind   TEXT    NOT NULL DEFAULT 'hoot',  -- 'hoot' | 'wpilog'
            device_serial TEXT,          -- hex prefix from the filename
            started_at    TEXT,          -- parsed from the filename
            duration_s    REAL,
            raw_rows      INTEGER,
            stored_rows   INTEGER,
            source_bytes  INTEGER,
            archive_path  TEXT,          -- where the original was filed away
            match_key     TEXT,          -- 'qm14' / 'practice' — set by hand
            notes         TEXT,
            keep          INTEGER NOT NULL DEFAULT 0,  -- 1 = exempt from eviction
            imported_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # ── The CAN-id → English name map ────────────────────────────────────
    # A log only ever says "TalonFX 11". Nothing in the file can tell you that
    # is the front-left drive motor, so this is a hand-maintained translation
    # table, edited from Control Screen → Pit Systems → Robot Logs.
    #
    # Rows are created automatically on import (label NULL) and persist across
    # imports, so the names are typed once per robot, not once per log.
    conn.execute(
        """
        CREATE TABLE device (
            id          INTEGER PRIMARY KEY,
            device_type TEXT    NOT NULL,      -- TalonFX | CANcoder | Pigeon2 | …
            can_id      INTEGER NOT NULL,
            label       TEXT,                  -- 'Front-Left Drive'
            subsystem   TEXT,                  -- 'Drivetrain' | 'Shooter' | …
            notes       TEXT,
            UNIQUE (device_type, can_id)
        )
        """
    )

    # 209 rows for the current firmware. Grows only when CTRE adds a signal.
    conn.execute(
        """
        CREATE TABLE signal (
            id           INTEGER PRIMARY KEY,
            device_type  TEXT NOT NULL,
            name         TEXT NOT NULL,
            value_kind   TEXT NOT NULL DEFAULT 'num',   -- num | enum
            signal_class TEXT NOT NULL DEFAULT 'telemetry',
            unit         TEXT,
            UNIQUE (device_type, name)
        )
        """
    )

    # Enum labels are interned to small ints so the hot table stays numeric.
    conn.execute(
        """
        CREATE TABLE signal_enum (
            signal_id INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
            code      INTEGER NOT NULL,
            label     TEXT    NOT NULL,
            PRIMARY KEY (signal_id, code)
        ) WITHOUT ROWID
        """
    )

    # One row per (session, device, signal). Collapsing that three-part identity
    # into a single id is what keeps the sample row narrow, and the rolled-up
    # min/max/mean answers most pit questions without touching the samples.
    conn.execute(
        """
        CREATE TABLE series (
            id         INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES log_session(id) ON DELETE CASCADE,
            device_id  INTEGER NOT NULL REFERENCES device(id),
            signal_id  INTEGER NOT NULL REFERENCES signal(id),
            n_raw      INTEGER NOT NULL DEFAULT 0,
            n_stored   INTEGER NOT NULL DEFAULT 0,
            v_min REAL, v_max REAL, v_mean REAL, v_last REAL,
            UNIQUE (session_id, device_id, signal_id)
        )
        """
    )
    conn.execute("CREATE INDEX idx_series_signal ON series(signal_id)")
    conn.execute("CREATE INDEX idx_series_device ON series(device_id)")

    # 134 of 209 signals never change. Keeping them here instead of in `sample`
    # removed 13.4 million rows from the measured import.
    conn.execute(
        """
        CREATE TABLE session_constant (
            session_id INTEGER NOT NULL REFERENCES log_session(id) ON DELETE CASCADE,
            device_id  INTEGER NOT NULL REFERENCES device(id),
            signal_id  INTEGER NOT NULL REFERENCES signal(id),
            v          REAL,
            v_text     TEXT,
            PRIMARY KEY (session_id, device_id, signal_id)
        ) WITHOUT ROWID
        """
    )

    # Faults as intervals rather than samples. Sticky faults are latched for the
    # session and carry t_ms_end = NULL.
    conn.execute(
        """
        CREATE TABLE fault_event (
            id         INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES log_session(id) ON DELETE CASCADE,
            device_id  INTEGER NOT NULL REFERENCES device(id),
            signal_id  INTEGER NOT NULL REFERENCES signal(id),
            sticky     INTEGER NOT NULL DEFAULT 0,
            t_ms_start INTEGER NOT NULL,
            t_ms_end   INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX idx_fault_session ON fault_event(session_id, device_id)")
