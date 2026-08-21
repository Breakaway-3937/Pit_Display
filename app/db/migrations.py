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
