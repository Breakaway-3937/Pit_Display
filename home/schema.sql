/*
  Breakaway pit data — the master copy on the home SQL Server.

  A STARTING POINT for the home session (home/HANDOFF.md), written without
  access to the server. Adjust names, filegroups and sizing to the real box;
  keep the *shape*, because it mirrors the hub's contract exactly:

    sync.*       the hub mirrored row for row, plus every version ever seen
    team.*       views over sync.row_state: the team's settings as tables
    telemetry.*  robot log sessions, from the bundles (app/db/sync/bundle.py)
    analysis.*   what the Ollama pipeline produced, and what it published

  Target: SQL Server 2019+ (JSON functions, clustered columnstore).
  Every key that crosses machines is a NAME, never a pit's integer id.
*/

CREATE SCHEMA sync;
GO
CREATE SCHEMA team;
GO
CREATE SCHEMA telemetry;
GO
CREATE SCHEMA analysis;
GO

-- ── sync: the hub, mirrored ───────────────────────────────────────────────

-- Where the home agent is in the hub's sequence. One row per hub epoch: a
-- new epoch means the hub was rebuilt (by `home_agent restore`), and the
-- cursor starts again at 0.
CREATE TABLE sync.cursor (
    epoch       VARCHAR(64)  NOT NULL PRIMARY KEY,
    seq         BIGINT       NOT NULL DEFAULT 0,
    updated_at  DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
);

-- The newest state of every row, exactly as the hub holds it.
CREATE TABLE sync.row_state (
    tbl     VARCHAR(40)    NOT NULL,
    uid     NVARCHAR(200)  NOT NULL,
    seq     BIGINT         NOT NULL,
    op      VARCHAR(10)    NOT NULL,            -- 'upsert' | 'delete'
    data    NVARCHAR(MAX)  NULL CHECK (data IS NULL OR ISJSON(data) = 1),
    origin  VARCHAR(80)    NOT NULL,            -- machine id
    at_ms   BIGINT         NOT NULL,            -- hub clock, Unix ms
    epoch   VARCHAR(64)    NOT NULL,
    CONSTRAINT pk_row_state PRIMARY KEY (tbl, uid)
);
CREATE INDEX ix_row_state_seq ON sync.row_state (epoch, seq);

-- Every version the home agent ever pulled. The hub keeps only the newest;
-- this is why home is the master. Append-only.
CREATE TABLE sync.row_history (
    id      BIGINT IDENTITY PRIMARY KEY,
    tbl     VARCHAR(40)    NOT NULL,
    uid     NVARCHAR(200)  NOT NULL,
    seq     BIGINT         NOT NULL,
    op      VARCHAR(10)    NOT NULL,
    data    NVARCHAR(MAX)  NULL,
    origin  VARCHAR(80)    NOT NULL,
    at_ms   BIGINT         NOT NULL,
    epoch   VARCHAR(64)    NOT NULL,
    pulled_at DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_row_history_row ON sync.row_history (tbl, uid, seq);

-- Every file the hub has told us about, and where the archive copy is.
CREATE TABLE sync.blob (
    sha           CHAR(64)       NOT NULL PRIMARY KEY,
    kind          VARCHAR(40)    NOT NULL,       -- 'bundle' | 'raw' | 'file'
    name          NVARCHAR(300)  NULL,
    bytes         BIGINT         NOT NULL,
    origin        VARCHAR(80)    NULL,
    hub_created_ms BIGINT        NULL,
    archive_path  NVARCHAR(500)  NULL,           -- NULL until downloaded + verified
    archived_at   DATETIME2(3)   NULL,
    ingested_at   DATETIME2(3)   NULL,           -- bundles: landed in telemetry.*
    acked_at      DATETIME2(3)   NULL,           -- POST /v1/blob/{sha}/ack sent
    error         NVARCHAR(500)  NULL
);

-- Edits made at home, waiting to be force-pushed. Anything that changes team
-- data at home (a script, a future web page, the pipeline) writes here; the
-- agent pushes with force=true and records the hub's seq.
CREATE TABLE sync.push_queue (
    id        BIGINT IDENTITY PRIMARY KEY,
    tbl       VARCHAR(40)   NOT NULL,
    uid       NVARCHAR(200) NOT NULL,
    op        VARCHAR(10)   NOT NULL,
    data      NVARCHAR(MAX) NULL CHECK (data IS NULL OR ISJSON(data) = 1),
    queued_at DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    pushed_at DATETIME2(3)  NULL,
    seq       BIGINT        NULL,
    error     NVARCHAR(500) NULL
);

-- The hub's machine list, refreshed each cycle from GET /v1/status.
CREATE TABLE sync.machine (
    id            VARCHAR(80)   NOT NULL PRIMARY KEY,
    name          NVARCHAR(80)  NULL,
    role          VARCHAR(10)   NULL,
    version       VARCHAR(40)   NULL,
    first_seen_ms BIGINT        NULL,
    last_seen_ms  BIGINT        NULL,
    last_pull_seq BIGINT        NULL,
    pushed        BIGINT        NULL,
    conflicts     BIGINT        NULL
);
GO

-- ── team: the settings, readable as tables ────────────────────────────────
-- Views, not copies: row_state is the one truth, and a view can't drift.
-- Column lists match app/db/sync/tables.py SPECS.

CREATE VIEW team.checklist AS
SELECT uid, JSON_VALUE(data, '$.name') AS name,
       CAST(JSON_VALUE(data, '$.position') AS INT) AS position, seq, origin
FROM sync.row_state WHERE tbl = 'checklist' AND op = 'upsert';
GO
CREATE VIEW team.checklist_item AS
SELECT uid, JSON_VALUE(data, '$.checklist_uid') AS checklist_uid,
       JSON_VALUE(data, '$.text') AS text,
       CAST(JSON_VALUE(data, '$.position') AS INT) AS position, seq, origin
FROM sync.row_state WHERE tbl = 'checklist_item' AND op = 'upsert';
GO
CREATE VIEW team.eq_preset AS
SELECT uid, JSON_VALUE(data, '$.name') AS name,
       CAST(JSON_VALUE(data, '$.preamp') AS FLOAT) AS preamp,
       JSON_VALUE(data, '$.gains') AS gains, seq, origin
FROM sync.row_state WHERE tbl = 'eq_presets' AND op = 'upsert';
GO
CREATE VIEW team.device AS
SELECT uid, JSON_VALUE(data, '$.device_type') AS device_type,
       CAST(JSON_VALUE(data, '$.can_id') AS INT) AS can_id,
       JSON_VALUE(data, '$.label') AS label,
       JSON_VALUE(data, '$.subsystem') AS subsystem,
       JSON_VALUE(data, '$.notes') AS notes, seq, origin
FROM sync.row_state WHERE tbl = 'device' AND op = 'upsert';
GO
CREATE VIEW team.setting AS
SELECT uid AS doc, data, seq, origin
FROM sync.row_state WHERE tbl = 'setting' AND op = 'upsert';
GO
CREATE VIEW team.log_session AS
SELECT uid,
       JSON_VALUE(data, '$.source_name') AS source_name,
       JSON_VALUE(data, '$.source_kind') AS source_kind,
       JSON_VALUE(data, '$.started_at')  AS started_at,
       CAST(JSON_VALUE(data, '$.duration_s') AS FLOAT) AS duration_s,
       JSON_VALUE(data, '$.match_key')   AS match_key,
       JSON_VALUE(data, '$.notes')       AS notes,
       CAST(JSON_VALUE(data, '$.keep') AS INT) AS keep,
       JSON_VALUE(data, '$.bundle_sha')  AS bundle_sha,
       JSON_VALUE(data, '$.raw_sha')     AS raw_sha,
       origin, seq
FROM sync.row_state WHERE tbl = 'log_session' AND op = 'upsert';
GO

-- ── telemetry: robot logs, from bundles ───────────────────────────────────
-- One bundle → one session. Identity is by name throughout; enum codes are
-- per session (they are per machine on the pits), so labels live per session.

CREATE TABLE telemetry.session (
    uid           NVARCHAR(200) NOT NULL PRIMARY KEY,
    source_name   NVARCHAR(300) NOT NULL,
    source_kind   VARCHAR(20)   NOT NULL,
    device_serial VARCHAR(64)   NULL,
    started_at    VARCHAR(40)   NULL,           -- as the pit parsed it from the filename
    duration_s    FLOAT         NULL,
    raw_rows      BIGINT        NULL,
    stored_rows   BIGINT        NULL,
    source_bytes  BIGINT        NULL,
    origin        VARCHAR(80)   NULL,
    bundle_sha    CHAR(64)      NOT NULL,
    raw_sha       CHAR(64)      NULL,
    ingested_at   DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    deleted_at    DATETIME2(3)  NULL             -- a pit deleted it; home keeps it
);
-- match_key / notes / keep are hand-edited on the pits: read them from
-- team.log_session, which follows every edit. This table is the import.

CREATE TABLE telemetry.signal (
    device_type  VARCHAR(40)   NOT NULL,
    name         NVARCHAR(200) NOT NULL,
    value_kind   VARCHAR(10)   NOT NULL,
    signal_class VARCHAR(20)   NOT NULL,
    unit         VARCHAR(20)   NULL,
    CONSTRAINT pk_signal PRIMARY KEY (device_type, name)
);

CREATE TABLE telemetry.session_enum (
    session_uid NVARCHAR(200) NOT NULL,
    sig_type    VARCHAR(40)   NOT NULL,
    sig_name    NVARCHAR(200) NOT NULL,
    code        INT           NOT NULL,
    label       NVARCHAR(MAX) NOT NULL,
    CONSTRAINT pk_session_enum PRIMARY KEY (session_uid, sig_type, sig_name, code)
);

CREATE TABLE telemetry.series (
    id          BIGINT IDENTITY PRIMARY KEY,
    session_uid NVARCHAR(200) NOT NULL,
    device_type VARCHAR(40)   NOT NULL,
    can_id      INT           NOT NULL,
    sig_type    VARCHAR(40)   NOT NULL,
    sig_name    NVARCHAR(200) NOT NULL,
    n_raw       BIGINT NULL, n_stored BIGINT NULL,
    v_min FLOAT NULL, v_max FLOAT NULL, v_mean FLOAT NULL, v_last FLOAT NULL,
    CONSTRAINT uq_series UNIQUE (session_uid, device_type, can_id, sig_type, sig_name)
);

CREATE TABLE telemetry.constant (
    session_uid NVARCHAR(200) NOT NULL,
    device_type VARCHAR(40)   NOT NULL,
    can_id      INT           NOT NULL,
    sig_type    VARCHAR(40)   NOT NULL,
    sig_name    NVARCHAR(200) NOT NULL,
    v           FLOAT         NULL,
    v_text      NVARCHAR(MAX) NULL
);
CREATE INDEX ix_constant_session ON telemetry.constant (session_uid);

CREATE TABLE telemetry.fault (
    session_uid NVARCHAR(200) NOT NULL,
    device_type VARCHAR(40)   NOT NULL,
    can_id      INT           NOT NULL,
    sig_type    VARCHAR(40)   NOT NULL,
    sig_name    NVARCHAR(200) NOT NULL,
    sticky      BIT           NOT NULL,
    t_ms_start  BIGINT        NOT NULL,
    t_ms_end    BIGINT        NULL
);
CREATE INDEX ix_fault_session ON telemetry.fault (session_uid);

-- The per-second rollup: what charts and the agents read. Small.
CREATE TABLE telemetry.sample_1s (
    series_id BIGINT NOT NULL,
    t_s       INT    NOT NULL,
    v_min FLOAT NULL, v_max FLOAT NULL, v_avg FLOAT NULL, n INT NULL,
    CONSTRAINT pk_sample_1s PRIMARY KEY (series_id, t_s)
);

-- Every stored change-point. Millions of rows per session: columnstore.
CREATE TABLE telemetry.sample (
    series_id BIGINT NOT NULL,
    t_ms      INT    NOT NULL,
    ord       INT    NOT NULL,
    v         FLOAT  NULL
);
CREATE CLUSTERED COLUMNSTORE INDEX cci_sample ON telemetry.sample;
GO

-- ── analysis: the Ollama pipeline ─────────────────────────────────────────

CREATE TABLE analysis.run (
    id           BIGINT IDENTITY PRIMARY KEY,
    started_at   DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    finished_at  DATETIME2(3)  NULL,
    session_uids NVARCHAR(MAX) NOT NULL,        -- JSON array
    question     NVARCHAR(500) NULL,
    analyst      VARCHAR(100)  NOT NULL,        -- Ollama model tag
    designer     VARCHAR(100)  NULL,
    insights     NVARCHAR(MAX) NULL CHECK (insights IS NULL OR ISJSON(insights) = 1),
    board        NVARCHAR(MAX) NULL CHECK (board IS NULL OR ISJSON(board) = 1),
    status       VARCHAR(20)   NOT NULL DEFAULT 'running',  -- running|published|rejected|failed
    reject_reason NVARCHAR(MAX) NULL,
    transcript   NVARCHAR(MAX) NULL             -- tool calls, for debugging a model
);
GO
