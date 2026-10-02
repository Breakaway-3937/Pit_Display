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


@register_migration
def _v8_checklists(conn: sqlite3.Connection) -> None:
    """
    Pit checklists shown on the overhead screens.

    Several named lists, because a pit runs more than one — pre-match, end of
    day, load-out. A presentation screen points at one of them by id (the
    per-screen `checklist_id` setting), so the two overhead screens can show
    different lists at the same time.

    `done` lives here rather than in memory on purpose: the crew ticks items
    off across a whole match cycle, and closing the app between matches must
    not silently un-tick the work. The control screen has an explicit Reset.
    """
    conn.execute(
        """
        CREATE TABLE checklist (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL UNIQUE,
            position   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )

    # position, not id, is the display order — items get reordered, and a row
    # that moves must not have to be deleted and re-inserted to do it.
    conn.execute(
        """
        CREATE TABLE checklist_item (
            id           INTEGER PRIMARY KEY,
            checklist_id INTEGER NOT NULL
                         REFERENCES checklist(id) ON DELETE CASCADE,
            text         TEXT    NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            done         INTEGER NOT NULL DEFAULT 0,
            done_at      TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_checklist_item_list "
        "ON checklist_item(checklist_id, position)"
    )

    # One empty list so the feature has somewhere to put its first item. The
    # items themselves are the team's to write — see the empty state in
    # ChecklistOverlay, which says where to add them.
    conn.execute("INSERT INTO checklist (name, position) VALUES ('Pit Checklist', 0)")


@register_migration
def _v9_seed_eq_presets(conn: sqlite3.Connection) -> None:
    """
    Write the stock EQ presets once, here, instead of on every launch.

    They used to be re-inserted at every start (`eq.ensure_seeded()`), so a
    stock preset the team deleted came straight back, and one could only be
    "overwritten" by forking a "<name> (edited)" copy. Seeded once, they are
    ordinary rows: the admin can overwrite or delete any of them. `built_in`
    now only means "still the stock curve" (it shows the stock description).

    A snapshot of the values as of this migration, deliberately not imported
    from app.music.eq: a migration must do the same thing forever. INSERT OR
    IGNORE, so a database that already has them keeps its own.
    """
    stock = [
        ("Flat",            0.0, "0,0,0,0,0,0,0,0,0,0"),
        ("Pit Default",    -2.0, "-12,-8,-3,-4,-2,0,2,3,1,0"),
        ("Crowded",        -3.0, "-14,-10,-4,-5,-2,1,3,4,2,0"),
        ("Judges Visiting", -6.0, "-16,-12,-6,-6,-3,0,2,2,0,-2"),
        ("Lunch",          -2.0, "-8,-5,-2,-3,-1,0,1,2,1,0"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO eq_presets (name, preamp, gains, built_in) "
        "VALUES (?, ?, ?, 1)", stock)


@register_migration
def _v10_sync(conn: sqlite3.Connection) -> None:
    """
    Sync bookkeeping: every team-owned row gets a `uid`, and every change to
    one is recorded by a trigger into `sync_outbox`.

    Triggers, not calls in each writer, because a writer that forgets to
    record its change is a setting that silently never leaves this machine,
    and there are writers on two connections (the GUI's and the log
    importer's). The triggers stand down while `sync_guard.applying` is 1,
    which is how the sync engine writes what it pulled without echoing it
    back. See app/db/sync/ and DATABASE.md "Sync".

    uids are derived from the name where the name *is* the identity (EQ
    presets, LED presets, playlists, checklists, CAN devices), so the stock
    "Flat" preset seeded on two machines is one row at the hub, not two.
    Anything else gets a random one. A snapshot of the synced tables as of
    this migration, deliberately not imported from app.db.sync: a migration
    must do the same thing forever.
    """
    import uuid

    # table -> (name-derived uid expression over NEW, or None; watched columns)
    tables = {
        "led_presets":      ("'led:' || lower(NEW.name)",
                             "name, mode, speed, brightness, color"),
        "eq_presets":       ("'eq:' || lower(NEW.name)",
                             "name, preamp, gains, built_in"),
        "playlists":        ("'pl:' || lower(NEW.name)", "name, app_mode"),
        "playlist_items":   (None, "playlist_id, track_id, position"),
        "checklist":        ("'cl:' || lower(NEW.name)", "name, position"),
        # `done` is deliberately not watched: ticks are this pit's live state.
        "checklist_item":   (None, "checklist_id, text, position"),
        "admin_credential": ("'admin'",
                             "algo, iterations, salt, hash, is_default"),
        "device":           ("'dev:' || NEW.device_type || ':' || NEW.can_id",
                             "label, subsystem, notes"),
        # Only the hand-edited fields; the importer's own UPDATE of the row
        # counts at the end of an import is not a change anyone made.
        "log_session":      (None, "match_key, notes, keep"),
    }

    conn.executescript(
        """
        CREATE TABLE sync_guard (
            id       INTEGER PRIMARY KEY CHECK (id = 1),
            applying INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sync_guard (id, applying) VALUES (1, 0);

        -- One entry per row with a change not yet accepted by the hub. `rev`
        -- moves on every re-edit, so a push that raced an edit keeps it.
        CREATE TABLE sync_outbox (
            tbl TEXT    NOT NULL,
            uid TEXT    NOT NULL,
            op  TEXT    NOT NULL,           -- 'upsert' | 'delete'
            at  TEXT    NOT NULL DEFAULT (datetime('now')),
            rev INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tbl, uid)
        );

        -- The hub's seq for each row as this machine last saw it: the `base`
        -- a push is judged against.
        CREATE TABLE sync_row (
            tbl TEXT    NOT NULL,
            uid TEXT    NOT NULL,
            seq INTEGER NOT NULL,
            PRIMARY KEY (tbl, uid)
        ) WITHOUT ROWID;

        -- Pulled changes that couldn't land yet: a parent not here, a track
        -- this machine doesn't have, a log bundle still to download.
        CREATE TABLE sync_pending (
            tbl    TEXT    NOT NULL,
            uid    TEXT    NOT NULL,
            seq    INTEGER NOT NULL,
            op     TEXT    NOT NULL,
            data   TEXT,
            reason TEXT,
            tries  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tbl, uid)
        );

        -- Cursor and epoch. Machine identity is NOT here: a copied database
        -- would clone it. It lives in sync.json beside the database.
        CREATE TABLE sync_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);

        -- A session's files at the hub, once uploaded or downloaded.
        CREATE TABLE sync_session_blob (
            uid          TEXT PRIMARY KEY,
            bundle_sha   TEXT,
            bundle_bytes INTEGER,
            raw_sha      TEXT,
            raw_bytes    INTEGER
        );

        -- Team settings documents and team files (judges slides, CAD), by
        -- the hub's key: what this machine last pushed or applied.
        CREATE TABLE sync_doc (
            tbl  TEXT NOT NULL,             -- 'setting' | 'file'
            uid  TEXT NOT NULL,
            hash TEXT NOT NULL,
            PRIMARY KEY (tbl, uid)
        ) WITHOUT ROWID;

        -- Boards written by the home analysis pipeline. Read-only here.
        CREATE TABLE analysis_board (
            id         INTEGER PRIMARY KEY,
            uid        TEXT    NOT NULL UNIQUE,
            title      TEXT    NOT NULL DEFAULT '',
            spec       TEXT    NOT NULL,     -- JSON, home/contracts/board.schema.json
            session_uid TEXT,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    guard = "(SELECT applying FROM sync_guard WHERE id = 1) = 0"
    random_uid = "lower(hex(randomblob(16)))"
    for table, (derived, watched) in tables.items():
        conn.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")

        # Backfill: the derived uid where free, a random one otherwise (an old
        # install can hold "Pit Display" and "PIT DISPLAY" side by side).
        taken: set[str] = set()
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        for row in conn.execute(f"SELECT rowid, * FROM {table}").fetchall():
            record = dict(zip(["rowid", *cols], row))
            uid = None
            if derived is not None:
                uid = conn.execute(
                    "SELECT " + derived.replace("NEW.", ":"), record).fetchone()[0]
            if not uid or uid in taken:
                uid = uuid.uuid4().hex
            taken.add(uid)
            conn.execute(f"UPDATE {table} SET uid = ? WHERE rowid = ?",
                         (uid, record["rowid"]))
            # Everything already here is news to the hub.
            conn.execute("INSERT INTO sync_outbox (tbl, uid, op) VALUES (?, ?, 'upsert')",
                         (table, uid))
        conn.execute(f"CREATE UNIQUE INDEX idx_{table}_uid ON {table}(uid)")

        if derived is None:
            new_uid = random_uid
        else:
            # The derived uid unless a renamed row already holds it.
            new_uid = (f"CASE WHEN EXISTS (SELECT 1 FROM {table} WHERE uid = {derived}) "
                       f"THEN {random_uid} ELSE {derived} END")
        conn.executescript(
            f"""
            CREATE TRIGGER sync_{table}_ins AFTER INSERT ON {table}
            WHEN {guard}
            BEGIN
                UPDATE {table} SET uid = {new_uid}
                 WHERE rowid = NEW.rowid AND NEW.uid IS NULL;
                INSERT INTO sync_outbox (tbl, uid, op)
                     SELECT '{table}', uid, 'upsert' FROM {table} WHERE rowid = NEW.rowid
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'upsert', at = excluded.at, rev = rev + 1;
            END;

            CREATE TRIGGER sync_{table}_upd AFTER UPDATE OF {watched} ON {table}
            WHEN {guard} AND NEW.uid IS NOT NULL
            BEGIN
                INSERT INTO sync_outbox (tbl, uid, op) VALUES ('{table}', NEW.uid, 'upsert')
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'upsert', at = excluded.at, rev = rev + 1;
            END;

            CREATE TRIGGER sync_{table}_del AFTER DELETE ON {table}
            WHEN {guard} AND OLD.uid IS NOT NULL
            BEGIN
                INSERT INTO sync_outbox (tbl, uid, op) VALUES ('{table}', OLD.uid, 'delete')
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'delete', at = excluded.at, rev = rev + 1;
            END;
            """
        )


@register_migration
def _v11_analysis_run(conn: sqlite3.Connection) -> None:
    """
    This machine's log of analysis runs (app/ai/), one row per run, including
    the rejected and failed ones: that record is how models get judged. Mirrors
    home's `analysis.run`. Machine-local for now; it doesn't sync.
    """
    conn.execute(
        """
        CREATE TABLE analysis_run (
            id            INTEGER PRIMARY KEY,
            session_uids  TEXT    NOT NULL,          -- JSON array, even for one
            question      TEXT,
            analyst       TEXT    NOT NULL,          -- model tag
            designer      TEXT,
            status        TEXT    NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running', 'published', 'rejected', 'failed')),
            reject_reason TEXT,
            insights      TEXT,                      -- JSON, insight.schema.json
            board         TEXT,                      -- JSON, board.schema.json
            board_uid     TEXT,                      -- analysis_board.uid when published
            transcript    TEXT,                      -- JSON: both conversations
            stats         TEXT,                      -- JSON: turns, tokens, seconds
            started_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            finished_at   TEXT
        )
        """
    )


@register_migration
def _v12_analysis_feedback(conn: sqlite3.Connection) -> None:
    """
    The crew's verdict on each analysis run, and analysis joining sync.

    `analysis_feedback`: one row per finding (`finding_id`) and one for the
    run as a whole (`finding_id` = ''). `rating` is useful / not_useful /
    wrong, `acted` says the crew did something because of it, and `score`
    (1–5, run row only) is the run's rank. This is the record that steers
    model and prompt choices: a run is good when its findings are useful, not
    when it finds problems.

    **All of it syncs** (DATABASE.md, "Sync"), so a run rated from a Mac in a
    hotel counts everywhere: `analysis_run` (random uid), `analysis_feedback`
    (uid = its run's uid + '#' + finding, so the same finding rated on two
    machines is one row) and the boards this machine makes. Triggers as in
    `_v10_sync`, written out here because a migration must do the same thing
    forever.
    """
    conn.executescript(
        """
        CREATE TABLE analysis_feedback (
            run_id     INTEGER NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            finding_id TEXT    NOT NULL DEFAULT '',   -- '' = the whole run
            rating     TEXT    CHECK (rating IN ('useful', 'not_useful', 'wrong')),
            acted      INTEGER NOT NULL DEFAULT 0,
            score      INTEGER CHECK (score BETWEEN 1 AND 5),
            note       TEXT,
            rated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
            uid        TEXT,
            PRIMARY KEY (run_id, finding_id)
        );
        CREATE UNIQUE INDEX idx_analysis_feedback_uid ON analysis_feedback(uid);

        ALTER TABLE analysis_run ADD COLUMN uid TEXT;
        UPDATE analysis_run SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL;
        CREATE UNIQUE INDEX idx_analysis_run_uid ON analysis_run(uid);
        INSERT INTO sync_outbox (tbl, uid, op)
             SELECT 'analysis_run', uid, 'upsert' FROM analysis_run;
        INSERT INTO sync_outbox (tbl, uid, op)
             SELECT 'analysis_board', uid, 'upsert' FROM analysis_board
              WHERE uid NOT IN (SELECT uid FROM sync_row WHERE tbl = 'analysis_board');
        """
    )
    guard = "(SELECT applying FROM sync_guard WHERE id = 1) = 0"
    uids = {
        "analysis_run": "lower(hex(randomblob(16)))",
        "analysis_feedback": "(SELECT uid FROM analysis_run WHERE id = NEW.run_id)"
                             " || '#' || NEW.finding_id",
        "analysis_board": None,             # always written with its uid
    }
    watched = {
        "analysis_run": "status, designer, reject_reason, insights, board, board_uid, "
                        "transcript, stats, finished_at",
        "analysis_feedback": "rating, acted, score, note",
        "analysis_board": "title, spec",
    }
    for table, new_uid in uids.items():
        set_uid = (f"UPDATE {table} SET uid = {new_uid} "
                   f"WHERE rowid = NEW.rowid AND NEW.uid IS NULL;" if new_uid else "")
        conn.executescript(
            f"""
            CREATE TRIGGER sync_{table}_ins AFTER INSERT ON {table}
            WHEN {guard}
            BEGIN
                {set_uid}
                INSERT INTO sync_outbox (tbl, uid, op)
                     SELECT '{table}', uid, 'upsert' FROM {table} WHERE rowid = NEW.rowid
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'upsert', at = excluded.at, rev = rev + 1;
            END;

            CREATE TRIGGER sync_{table}_upd AFTER UPDATE OF {watched[table]} ON {table}
            WHEN {guard} AND NEW.uid IS NOT NULL
            BEGIN
                INSERT INTO sync_outbox (tbl, uid, op) VALUES ('{table}', NEW.uid, 'upsert')
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'upsert', at = excluded.at, rev = rev + 1;
            END;

            CREATE TRIGGER sync_{table}_del AFTER DELETE ON {table}
            WHEN {guard} AND OLD.uid IS NOT NULL
            BEGIN
                INSERT INTO sync_outbox (tbl, uid, op) VALUES ('{table}', OLD.uid, 'delete')
                ON CONFLICT (tbl, uid) DO UPDATE
                     SET op = 'delete', at = excluded.at, rev = rev + 1;
            END;
            """
        )


@register_migration
def _v13_tba(conn: sqlite3.Connection) -> None:
    """
    TheBlueAlliance data, as home serves it (home/REQUESTS.md R5): the events
    3937 attends and their matches. **Home-produced and pulled only**, like
    `analysis_board`: the home server is TBA's proxy and cache and pushes these
    through the hub, so a pit at an event needs no internet route to TBA. The
    row is the hub's `data`, kept whole as JSON (`data`), with the columns a
    query needs pulled out beside it. No triggers: a pit never edits them.
    """
    conn.executescript(
        """
        CREATE TABLE tba_event (
            uid        TEXT PRIMARY KEY,          -- event key, e.g. '2026arli'
            name       TEXT,
            start_date TEXT,                      -- local calendar date
            end_date   TEXT,
            data       TEXT NOT NULL,             -- the whole row as home sent it
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE tba_match (
            uid         TEXT PRIMARY KEY,         -- match key, e.g. '2026arli_qm14'
            event_key   TEXT NOT NULL,
            comp_level  TEXT,
            match_key   TEXT,                     -- the pit's short form: 'qm14', 'sf1m1'
            actual_ms   INTEGER,                  -- Unix ms; NULL until played
            data        TEXT NOT NULL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_tba_match_event ON tba_match(event_key, match_key);
        """
    )


@register_migration
def _v14_session_origin(conn: sqlite3.Connection) -> None:
    """
    Which machine a log session was imported on: its root, where the original
    file is. NULL `origin` is a log imported here; a session that arrived by
    sync gets the importing machine's id from the hub (the `origin` of its
    bundle blob, which only the importer uploads) and that machine's name.
    A local cache of a hub fact, so neither column syncs or is watched.
    """
    conn.executescript(
        """
        ALTER TABLE log_session ADD COLUMN origin TEXT;
        ALTER TABLE log_session ADD COLUMN origin_name TEXT;
        """
    )


@register_migration
def _v15_tba_feeds(conn: sqlite3.Connection) -> None:
    """
    Home's award feeds, pulled only like `tba_event` (home/REQUESTS.md R5):
    `data` is the hub row whole; beside it, what a query needs. Rows pushed
    before a build knew these tables arrive through the engine's catch-up
    (`sync_meta.known_tables`), not here.

    * `tba_team`: every team (≈9,200): nickname and home, award totals, blue
      banners, streaks, Quality Award counts. Also the nicknames for a
      match schedule.
    * `tba_rival`: teams 3937 has met in finals, with and against.
    * `tba_fact`: finished one-sentence facts, `category` '3937' or 'league'.
    """
    conn.executescript(
        """
        CREATE TABLE tba_team (
            uid          TEXT PRIMARY KEY,         -- team key, 'frc3937'
            team_number  TEXT,                     -- '3937', a string as in tba_match
            nickname     TEXT,
            data         TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_tba_team_number ON tba_team(team_number);
        CREATE TABLE tba_rival (
            uid          TEXT PRIMARY KEY,         -- team key
            team_number  TEXT,
            data         TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE tba_fact (
            uid          TEXT PRIMARY KEY,         -- fact key, 'our_streak'
            category     TEXT,                     -- '3937' | 'league'
            team_number  TEXT,                     -- the team it's about, or NULL
            text         TEXT,                     -- one finished sentence
            sort         INTEGER,
            data         TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


@register_migration
def _v16_relay(conn: sqlite3.Connection) -> None:
    """
    R2 as a relay, not a store (home/REQUESTS.md R8). Machine-local
    bookkeeping; none of it has triggers:

    * `sync_blob_request`: this pit's requests for team-file bytes the hub no
      longer holds. Pushed as `blob_request` rows; `done_at` once the bytes
      landed and verified.
    * `sync_verdict`: home's comparison of a machine's manifest with the
      master, pulled only; the panel shows this machine's.
    * `tracks.sha*`: every scanned song is a team file now, hashed once per
      (size, mtime) and remembered here, so a restart doesn't re-read the
      library. `team_deleted`: an admin removed the song for the team; it's
      hidden everywhere and stays on disk until home approves the deletion.
    """
    conn.executescript(
        """
        CREATE TABLE sync_blob_request (
            sha          TEXT PRIMARY KEY,         -- the blob the hub should hold
            file_uid     TEXT NOT NULL,
            file_sha     TEXT,                     -- the file's own sha (= sha unless zstd'd)
            bytes        INTEGER,
            codec        TEXT,
            requested_at TEXT NOT NULL DEFAULT (datetime('now')),
            done_at      TEXT
        );
        CREATE TABLE sync_verdict (
            uid        TEXT PRIMARY KEY,           -- machine id
            data       TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        ALTER TABLE tracks ADD COLUMN sha TEXT;
        ALTER TABLE tracks ADD COLUMN sha_size INTEGER;
        ALTER TABLE tracks ADD COLUMN sha_mtime_ns INTEGER;
        ALTER TABLE tracks ADD COLUMN team_deleted INTEGER NOT NULL DEFAULT 0;
        """
    )

