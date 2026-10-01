# Database reference

**The authoritative description of how Pit Display stores data and every query
that reads it.** If you add a table, a migration, or a query, it belongs here —
this file is the contract between the schema and everything that reads it.

Contents: [Files](#the-two-files) · [Conventions](#conventions) ·
[Migrations](#migrations) · [Schema](#schema) · [Queries](#queries) ·
[Invariants](#invariants-that-will-bite-you) · [Adding things](#adding-to-the-database)

---

## Does an update keep my data?

Yes, and it is checked rather than promised:

    uv run tools/upgrade_check.py

It fills every table a person writes to — checklists and their items, the
CAN-id names on `device`, EQ and LED presets, the music index — then runs a
version change at it: the seed database a build carries, plus a newly
registered migration. Then it reopens and looks for every row. 16 checks,
exit 0/1.

The reason it exists is `paths.seed_user_data()`. Every build ships
`data/pit_display.db` so a *fresh* install has a schema; if that ever landed
on top of an existing one, a season of imported logs and every CAN-id name
would go with it. Its rule is "what you get when you have nothing, not a
factory reset", and the tool proves it by breaking it: made to overwrite, it
reports nine failures with every planted row gone.

**Not in the database, and behaving differently:**

| | |
|---|---|
| `nexus.json`, `webcast.json`, `update.json` | Files in the data directory. Survive an update |
| `secrets/` | Same. Never in the database, never in the install |
| `data/pit_display_samples.db` | Disposable by design. Delete it and the schema is rebuilt; the main database is untouched |
| Per-screen settings | **In memory only** — theme, screen content, slide index, `checklist_id` reset on every launch, not just on a version change |


## The two files

| File | Holds | Typical size |
|---|---|---|
| `data/pit_display.db` | Everything except bulk telemetry: settings, presets, the admin credential, the music library, and all robot-log *metadata* | ~270 KB |
| `data/pit_display_samples.db` | Only `sample` and `sample_1s` — the robot telemetry itself | ~83 MB per imported session |

The second is **ATTACHed as the `samples` schema** at connection time, so it is
queried as `samples.sample` in ordinary SQL and joins across the two work
normally. `app/db/database.py` owns both.

**Why the split.** One imported log is ~83 MB. Keeping it out of the main file
means the app's own settings stay small enough to copy in a second, and
reclaiming disk space is deleting one file rather than a `VACUUM` on everything.

**The samples file is disposable by design.** Its schema is re-created
idempotently on every startup by `_Database._ensure_sample_schema()` and is
deliberately **not** versioned. Delete the file with the app closed and it comes
back empty on the next start — your sessions, devices and CAN-id names are all
in the main database and survive. The logs themselves can be re-imported from
their originals.

> This is why the samples schema must never gain a migration. If it were
> versioned, deleting the file would leave `user_version` claiming tables exist
> that no longer do, and the next startup would break.

---

## Conventions

- **Access is always through `app.db.db`** — a lazy proxy installed by
  `init_db()` in `main()`. Never open your own `sqlite3.connect` except for bulk
  ingest (see [`ingest.py`](#robot-telemetry)).
- **Writes go inside `with db.transaction():`** which commits on success and
  rolls back on exception.
- `db.fetchone()` / `db.fetchall()` return `sqlite3.Row` — index by column name.
- `db.path`, `db.samples_path`, `db.samples_bytes()` expose file locations.
- **Foreign keys are ON.** `ON DELETE CASCADE` is used deliberately; rows in the
  attached samples file are *not* covered by it (SQLite does not enforce FKs
  across attached databases) and must be deleted explicitly — `delete_session()`
  does this.
- Timestamps stored as TEXT via `datetime('now')` (UTC), except robot telemetry
  which uses integer milliseconds relative to the start of a session.

---

## Migrations

Registered with `@register_migration` in `app/db/migrations.py`, applied in
import order, tracked by `PRAGMA user_version`. Each is atomic.

`app.db.migrations` must be imported **before** `init_db()` — it registers by
side effect. `main.py` does this at the top.

| # | Name | Adds |
|---|---|---|
| 1 | `_v1_led_presets` | `led_presets` |
| 2 | `_v2_music_library` | `tracks` |
| 3 | `_v3_playlists` | `playlists`, `playlist_items` |
| 4 | `_v4_eq_presets` | `eq_presets` |
| 5 | `_v5_drop_explicit` | drops `tracks.explicit` |
| 6 | `_v6_admin` | `admin_credential` |
| 7 | `_v7_robot_logs` | `log_session`, `device`, `signal`, `signal_enum`, `series`, `session_constant`, `fault_event` |
| 8 | `_v8_checklists` | `checklist`, `checklist_item` |
| 9 | `_v9_seed_eq_presets` | the five stock `eq_presets` rows, once (was every launch) |
| 10 | `_v10_sync` | `uid` on every synced table + its triggers; `sync_guard`, `sync_outbox`, `sync_row`, `sync_pending`, `sync_meta`, `sync_session_blob`, `sync_doc`, `analysis_board`. See [Sync](#sync) |
| 11 | `_v11_analysis_run` | `analysis_run`: the log of analysis runs (`app/ai/`). See [Analysis](#analysis) |
| 12 | `_v12_analysis_feedback` | `analysis_feedback` (the crew's ratings); `uid` on `analysis_run`; sync triggers on `analysis_run`, `analysis_feedback`, `analysis_board` |
| 13 | `_v13_tba` | `tba_event`, `tba_match`: TheBlueAlliance rows home pushes; pulled only. See [Analysis](#analysis) |

**Never edit a migration that has shipped.** Add a new one. A migration must be
safe to run against a database that already has data — `_v5` checks
`PRAGMA table_info` before dropping.

---

## Schema

### Pit systems

#### `led_presets`
Operator-saved LED looks, on top of the built-ins in `app/leds/effects.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE | |
| `mode` | INTEGER | matches `app.leds.protocol.Mode` |
| `speed`, `brightness` | INTEGER | 0–255 |
| `color` | TEXT | hex, NULL = follow team colour |
| `created_at` | TEXT | |

#### `tracks`
The scanned local music library. `path` is the natural key — one row per file.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `path` | TEXT UNIQUE | absolute, resolved |
| `title`, `artist`, `album` | TEXT | from tags, falling back to filename |
| `duration` | REAL | seconds |
| `missing` | INTEGER | 1 = file gone since last scan. **Rows are never deleted** so playlists referencing them survive |
| `added_at` | TEXT | |

Indexes: `idx_tracks_title`, `idx_tracks_artist`.

> There is no explicit-content column — it was dropped in `_v5`. The team
> pre-curates the music folder. Do not re-add per-track filtering.

#### `playlists` / `playlist_items`
`playlists.app_mode` (`standard`/`judges`/`lunch`) lets a playlist auto-select on
a display-mode change. `playlist_items` is ordered by `position`.

#### `eq_presets`
Ten band gains stored as a comma-separated string in `gains`, plus `preamp`.

Always read and written as a complete set of ten and never queried per band, so
a column beats a child table here.

**Nothing is protected** (the EQ is admin-only). The stock presets are seeded
once by `_v9` and are then ordinary rows: saving under any existing name
overwrites it in place, and any row can be deleted and stays deleted.
`built_in = 1` now only means "still the stock curve"; overwriting one clears
it, and only then does the panel drop the stock description. Deleting a preset
a display mode selects (`MODE_PRESETS`: Pit Default, Judges Visiting, Lunch)
means that mode leaves the curve as it is.

**Names are matched ignoring case** (`find_preset()`, `COLLATE NOCASE`, oldest
row wins), because the chips show them in capitals and "Pit Display" /
"PIT DISPLAY" look like one preset. The `UNIQUE` column itself is still
case-sensitive, so older installs can hold such pairs; saving updates the
oldest and Delete removes the other.

#### `checklist` / `checklist_item`
The pit checklists shown on the overhead screens. Written and ticked from
Control → (a presentation screen) → Checklist; read by `ChecklistOverlay`.

`checklist`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE | |
| `position` | INTEGER | display order in the picker |
| `created_at` | TEXT | |

`checklist_item`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `checklist_id` | INTEGER | → `checklist(id)` **ON DELETE CASCADE** |
| `text` | TEXT | the item as it appears on screen |
| `position` | INTEGER | display order — **not** `id`, see below |
| `done` | INTEGER | 0/1 |
| `done_at` | TEXT | set when ticked, NULL when cleared |

Index: `idx_checklist_item_list` on `(checklist_id, position)`.

Three things to know before querying this:

- **`position` is the order, `id` is not.** Items get reordered, and a row that
  moves must not have to be deleted and re-inserted to do it. `_ChecklistService`
  renumbers densely from 0 after every structural change, so always
  `ORDER BY position, id` — never `ORDER BY id`.
- **`done` is persistent state, not session state.** The crew ticks items off
  across a whole match cycle, and the app closing between matches must not
  silently un-tick the work. Clearing it is an explicit operator Reset
  (`UPDATE … SET done = 0`), never a side effect of a restart.
- **Which list a screen shows is not in this schema.** It is the per-screen
  `checklist_id` setting in `app.config`, so the two overhead screens can point
  at different lists. A screen whose list gets deleted falls back to
  `default_list_id()` rather than going blank.

Migration `_v8` seeds one empty list named `Pit Checklist` — somewhere for the
first item to go. The items themselves are the team's to write.

#### `admin_credential`
Exactly one row, `CHECK (id = 1)`. PBKDF2-HMAC-SHA256, random per-credential
salt, 200k iterations. `is_default = 1` while the shipped password is unchanged,
which drives the nag in the admin bar.

**A UI lock, not a security boundary** — the file sits on the pit machine's disk.
See [`OPERATOR_GUIDE.md`](OPERATOR_GUIDE.md#the-admin-lock).

---

### Robot telemetry

Storage design and the full 209-signal catalogue:
[storage plan artifact](https://claude.ai/code/artifact/b07af1ed-30eb-4ac4-9296-c7a36fc8e77e).

The shape is: **intern every repeated string once, collapse the three-part
identity into one `series_id`, then store only changes.** Measured on a real
3.85 GB Phoenix export — 62,118,775 raw rows became 3,263,543 stored rows.

```
log_session ──┬── series ──┬── samples.sample      (change points)
              │            └── samples.sample_1s   (1-second rollups)
              ├── session_constant                  (signals that never change)
              └── fault_event                       (fault bits as intervals)

device  ──────┘  signal ──── signal_enum
```

#### The pipeline that fills these tables

Three shapes of file arrive in the pit and all three land here. `app/robot/`
holds the code; the layout is in [`CLAUDE.md`](CLAUDE.md).

```
robot.hoot   ──owlet -f wpilog──▶  .wpilog ──┐
robot.wpilog ───────────────────────────────┼──▶ (t_ms, device, can id, signal, value)
…_detailed.txt ──parser.parse_line──────────┘                    │
                                                                 ▼
                     device · signal · series · sample · session_constant · fault_event
```

The storage code sees one record stream and cannot tell which source produced a
row — a `.hoot`, its `.wpilog` extraction, and a hand-made `.txt` export of the
same log all write identical `device` / `signal` / `series` rows. That is what
lets the CAN-id names survive a change of import route.

**The one thing a wpilog adds** is signals with no CAN address at all: the
team's application data, which does not exist in a hoot. Those go to the
pseudo-device below.

Verified against the team's own logs of 2026-08-17 (one `.hoot`, two
AdvantageKit `.wpilog`):

| | entry shape | resolves to |
|---|---|---|
| owlet's hoot export | `Phoenix6/TalonFX-12/MotorVoltage` | `TalonFX 12`, signal `MotorVoltage` |
| owlet, non-CAN | `RobotMode`, `DS:IsFMSAttached` | `Robot -1` |
| AdvantageKit | `/RealOutputs/Shooter Lead Temp F` | `Robot -1`, full path as the name |

**All 112 TalonFX signal names from the hoot route match the text-export route
exactly**, so the two land on the same `signal` rows and the CAN-id names carry
over. That equivalence is the property to re-check if the entry naming ever
moves.

#### Losslessness, audited

Every series was rebuilt from the database and compared against the source file
record by record — the source's value sequence run-length encoded (which is
exactly what change-only storage should keep) against what is actually stored,
in order. **1,912,619 source records, 100.00% accounted for, zero mismatches**
on all three logs.

| | records | where it went |
|---|---:|---|
| change-only series | 1,235,639 | `samples.sample`, exact |
| never-changing series | 450,450 | `session_constant`, exact (878 rows) |
| fault bits | 84,604 | `fault_event`, exact (32 intervals) |
| `Timestamp` | 141,926 | one series per session kept, the duplicates dropped |
| `string[]` / `raw` | 12 | **dropped** — no REAL representation |

The reader was also checked against an independent walk of each file that shares
none of its logic: rows produced equals rows expected, exactly, on all three.
Re-run both after any change to `wpilog.py`, `parser.classify()` or the storage
loop — and read invariant 8 first.

#### `log_session`
One row per imported file.

| Column | Notes |
|---|---|
| `source_file` | absolute path, **UNIQUE** — a second import of the same file fails loudly instead of silently doubling the data |
| `source_name` | basename, for display |
| `source_kind` | `hoot` \| `wpilog` — the same tables hold both sources. A `.hoot` and the `.txt` export of one are both `hoot`; the roboRIO's own DataLogManager file is `wpilog` |
| `device_serial`, `started_at` | parsed from the filename |
| `duration_s` | length of the log in seconds |
| `raw_rows`, `stored_rows` | compression audit — **read it against `source_kind`**. A hoot compresses ~19×; a wpilog is *already* change-only and lands near 1.1×, which is normal and not a fault. A hoot far off 19× is the cheapest signal that the export format changed |
| `source_bytes`, `archive_path` | provenance for re-import after eviction |
| `match_key`, `notes`, `keep` | operator metadata; `keep = 1` exempts from future eviction. `match_key` is pre-filled from the filename when it carries a match (`FRC_…_Q15` → `qm15`) and is editable either way |

#### `device` — the CAN-id → English name map
**This is the hand-maintained translation table.** A log only ever says
`TalonFX 11`; nothing in the file knows that is the front-left drive motor.

| Column | Notes |
|---|---|
| `device_type`, `can_id` | **UNIQUE together** — the natural key |
| `label` | the English name. NULL until someone types it |
| `subsystem` | `Drivetrain`, `Shooter`, … |

Rows are created automatically on import with `label` NULL. Because the key is
`(device_type, can_id)` and carries **no session reference**, names persist
across every future import and survive deleting the session that created them.
Edited in **Control Screen → Pit Systems → Telemetry → Robot telemetry**.

`repository.DeviceRow.display` falls back to `"TalonFX 11"` when unnamed —
**never render a raw CAN id if a label exists.**

`DeviceRow.detected` carries what the controller reported was physically plugged
in, read from the `ConnectedMotor` constant (`KrakenX60`, `KrakenX44`). It is
observed, not typed, and is empty for CANcoder and Pigeon2, which have no such
signal.

**One row in this table is not a CAN device.** Application signals from a
`.wpilog` — robot states, PDH currents, shooter setpoints — have no CAN address,
so they are all filed under a reserved pseudo-device:

| `device_type` | `can_id` | `label` | `subsystem` |
|---|---|---|---|
| `Robot` | `-1` | `Robot Code` | `Application` |

Their `signal.name` is the full log path (`RealOutputs/Shooter/Setpoint`) and
their `signal.device_type` is `Robot`, which keeps `UNIQUE (device_type, name)`
clear of the Phoenix catalogue. The row is inserted **already named**, and
`repository.devices()` / `unnamed_count()` both filter `can_id >= 0` so it never
reaches the CAN-map screen: that table answers "which motor is CAN 11", and a
row claiming CAN −1 is a lie in it — one nobody can act on, sitting in the
"still unnamed" badge forever. Every other query joins `device` normally and
sees it like any other device.

Constants: `app.robot.wpilog.APP_DEVICE_TYPE` / `APP_CAN_ID`. Do not hardcode
`-1`.

#### `signal`
209 rows for the current firmware; grows only when CTRE adds a signal.

`signal_class` drives which table a value lands in:

| Class | Meaning | Lands in |
|---|---|---|
| `telemetry` | live measurement | `sample` (+ `sample_1s`) |
| `fault` | live fault bit | `fault_event` intervals |
| `sticky_fault` | latched since last clear | `fault_event`, `t_ms_end` NULL |
| `config` | firmware/motor constants | usually `session_constant` |
| `clock` | the device's own clock | `sample` — **one series per session** |
| `meta` | *(unused)* | dropped |

**`clock` keeps the time values exactly once.** Every device on the bus reports
`Timestamp`, so a ten-motor log carries ten near-identical copies — 7.4% of
every file measured. The clock itself is worth having (the gap between a
device's clock and the log's own timestamp is CAN latency and drift); the tenth
copy of it is not. The importer claims the first `(device, signal)` pair to
report it and discards the rest into `ImportResult.clock_dropped`.

> The claim is made **before** `series_id()`, not after. Calling it first
> creates a `series` row for every device's copy and then never writes to it,
> leaving nine rows with NULL statistics on a ten-motor log.

`meta` no longer has any members — `classify()` cannot return it. The branch is
kept in `ingest.py` so a future "read it and throw it away" class needs no
change to the storage loop.

Assigned by `app.robot.parser.classify()`.

**`FaultField` and `StickyFaultField` are `telemetry`, not faults**, despite the
names. They are the whole fault word packed into one number rather than a single
bit — storing them as intervals would record "something was faulted from t1 to
t2" and throw away *which*, which is all they carry. They are the only two
signals in the catalogue that start with `Fault`/`StickyFault` without being a
bit, and `classify()` special-cases the `Field` suffix for exactly them. Real
values seen: `StickyFaultField = 8389376` across ten motors.

A signal name containing `/` is application data off the `Robot` pseudo-device
and is always `telemetry` — CTRE's naming says nothing about the team's own.

#### `signal_enum`
Interns string values (`Open`, `VoltageFOC`, `KrakenX60_Integrated`) to small
integer codes so the hot table stays purely numeric. 23 rows for this firmware.

#### `series`
One row per `(session, device, signal)` — 1,061 for the sample import.
Collapsing that three-part identity into a single id is what keeps the sample
row narrow.

Carries `n_raw`, `n_stored`, `v_min`, `v_max`, `v_mean`, `v_last`. **Most pit
questions are answered from these columns alone, without touching a sample.**

#### `samples.sample` — the hot table
```sql
PRIMARY KEY (series_id, t_ms, ord) WITHOUT ROWID
```
One row per **change**, not per sample. `WITHOUT ROWID` clusters rows by series
then time, which is exactly how every chart and window query reads.

- `t_ms` — milliseconds from session start. Matches the source's real
  resolution; no false precision.
- `ord` — 0 for 99.94% of rows. Exists because the source emits two *different*
  values in the same millisecond about 40,153 times per session. See
  [Invariants](#invariants-that-will-bite-you).
- `v` — the numeric value, or the enum code.

#### `samples.sample_1s`
Per-second `v_min`/`v_max`/`v_avg`/`n`. About 3.4% of the row count — cheap
enough to keep forever after the raw samples are evicted. This is what
dashboards and long-range charts should read.

#### `session_constant`
Signals whose value never changed for a whole session. 134 of 209 signals
qualified in the sample import, removing 13.4 million rows from the hot table.
Enum constants carry both `v` (the code) and `v_text` (the label) so a screen can
render `ForwardLimit = Open` without a join.

#### `fault_event`
Fault bits as intervals rather than samples: `t_ms_start` to `t_ms_end`, with
`sticky = 1` and `t_ms_end` NULL for latched faults. 50 rows for the sample
import — and they are the most diagnostically useful content in the file.

---

## Analysis

`app/ai/` (the local-model pipeline) reads the log tables only through the
ten tools in `app/ai/tools.py`, and writes `analysis_run` and
`analysis_board`; the crew's ratings go in `analysis_feedback`.

#### `analysis_run`
One row per run, **rejected and failed ones too**: that record is how models
get judged. Mirrors home's `analysis.run`. `session_uids`, `insights`,
`board`, `transcript` and `stats` are JSON. `status` is `running` /
`published` / `rejected` / `failed`; `reject_reason` says which check refused
it. **Syncs** (random `uid`); runs from other machines arrive here too, so
`id` is local and `uid` is the identity. `transcript` travels packed
(zstd + base64, `Spec.packed`): a run's can pass the hub's 256 KB row cap.

#### `analysis_feedback`
The crew's verdict, entered on Control → Analysis: **the reward signal**.
Primary key `(run_id, finding_id)`; `finding_id = ''` is the run as a whole.
`rating` useful / not_useful / wrong (or NULL), `acted` 0/1, `score` 1–5 on
the run row only. **`uid` = the run's uid + `#` + finding_id**, set by the
insert trigger, so the same finding rated on two machines is one row and the
hub's last write wins. Syncs (parent `analysis_run` by `run_uid`).

#### `analysis_board` (written here too)
Besides the boards home pushes, a pit's own published boards land here, with
uid **`<session_uid>:<machine_id>-<run id>`** (`multi:…` for several
sessions). Home's are `<session_uid>:<analysis.run id>`; without the machine
in it a pit's run 1 would replace home's board 1 on every pit. A pit's own
boards push up (triggers since `_v12`); pulled ones are written under the
guard, so they never echo.

#### `tba_event`, `tba_match`
TheBlueAlliance data, **home-produced, pulled only** (no triggers; a pit never
edits them): home is TBA's proxy and cache and pushes rows through the hub
(`home/REQUESTS.md` R5 is the row spec). `data` is the hub row whole (JSON);
beside it the keys a query needs: `tba_event(uid = event key, name,
start_date, end_date)`, `tba_match(uid = match key, event_key, comp_level,
match_key = the short 'qm14' a crew types, actual_ms = Unix ms or NULL)`.
`app/ai/tools.py` `match_context()` reads them: the log's `match_key` first,
else the played match within 15 minutes of the log's start.

**Rules the tools keep** (`app/ai/checks.py` enforces the rest):

- Figures are chosen exactly as `app.robot.diagnostics` chooses them; the
  check proves `session_overview` agrees with the pit board.
- Floats are rounded to 4 places in tool results, and that rounded figure is
  the one a model must copy: a metric that differs from every tool result is
  a rejection, never a correction.
- Lists cap at 200 rows; `series_1s` at 500 points.

---

## Sync

Every pit machine's team data meets at the hub (`sync.bh-stack.com`,
[`sync-hub/`](sync-hub/README.md)); the home SQL Server is the master copy
([`home/HANDOFF.md`](home/HANDOFF.md)). Code: `app/db/sync/`. Proof:
`uv run tools/sync_check.py --local`.

### What syncs

| Scope | What | How |
|---|---|---|
| Team-owned rows | `led_presets`, `eq_presets`, `playlists`, `playlist_items`, `checklist`, `checklist_item` (not `done`), `admin_credential`, `device` (the CAN names) | triggers → `sync_outbox` → hub; `app/db/sync/tables.py` `SPECS` |
| Team settings | `nexus.json`'s event and feed keys; `update.json`'s `auto_check`, `check_interval_hours` | `tbl = "setting"`, by hash (`docs.py`) |
| Team files | `assets/judges_slides/*`, `assets/cad/*` in the data tree | `tbl = "file"` → an R2 blob by SHA-256 |
| Machine-produced | `log_session` (hand-edited fields in the row; the data as a **bundle**; the original log as a zstd **raw** blob only with `upload_raw`, off by default) | `bundle.py`, `columns.py` |
| TheBlueAlliance | `tba_event`, `tba_match` (home pushes; `home/REQUESTS.md` R5) | pulled only; the engine special-cases them |
| Analysis | `analysis_run`, `analysis_feedback` (the crew's ratings), `analysis_board` (home's and every pit's; a pit's are `<session>:<machine>-<run>`) | triggers → `sync_outbox`; `SPECS` for the first two, the engine special-cases boards |
| **Never** | `tracks` (paths on this disk), `checklist_item.done` (this pit's ticks), `config`'s per-screen settings, `webcast.json`, the updater's `channel`, `secrets/`, `sync.json` | |

### The bookkeeping tables

| Table | Holds |
|---|---|
| `uid` column (each synced table) | the row's identity everywhere. **Name-derived** where the name is the identity (`eq:pit default`, `cl:load out`, `dev:TalonFX:11`, `admin`) so two machines' stock rows are one row; random otherwise. Never an integer id: those differ per machine |
| `sync_guard` | one row; `applying = 1` inside the engine's transactions so the triggers stay quiet. **Committed value is always 0**; `--self-check` fails otherwise |
| `sync_outbox` | `(tbl, uid)` with an unsent change, its `op`, and `rev` (bumped on every re-edit, so a push that raced an edit keeps it) |
| `sync_row` | the hub's `seq` for each row as this machine last saw it: the `base` a push is judged on |
| `sync_pending` | pulled changes that can't land yet: a parent not here, a track not in this library, a bundle or file to download |
| `sync_meta` | `cursor` (hub seq read up to) and `epoch` (a new one = the hub was rebuilt: re-read from 0) |
| `sync_session_blob` | a session's `bundle_sha` / `raw_sha` at the hub |
| `sync_doc` | hash of each setting document / team file as last pushed or applied |
| `analysis_board` | boards from the home pipeline: `uid`, `title`, `spec` (JSON, `home/contracts/board.schema.json`) |

**Machine identity is not in the database.** `sync.json` beside it holds
`machine_id`; a copied database must not bring another machine's identity.
It changes only through `settings.set_machine_id()` (Telemetry, admin, behind a
warning), never `save()`.

### Rules

1. **The hub decides.** A push is rejected if another machine wrote that row
   since this one last saw it; the pit applies the hub's version and says so
   in Telemetry. Only home may `force`.
2. **Writes that came from the hub run with the guard up**, from the engine's
   own connection. Never write a synced table with the guard up anywhere else:
   that edit would never leave the machine.
3. **Adding a synced table:** a `Spec` in `tables.py`, and a new migration
   with its `uid` column, backfill and three triggers (copy `_v10_sync`).
   References travel as the parent's uid, never an id.
4. **A trigger watches only hand-edited columns** (`UPDATE OF …`). The
   importer's end-of-import `UPDATE log_session SET raw_rows…` is not an edit.
5. **Deletes are tombstones, kept forever** at the hub. Deleting a session on
   one pit deletes it on the others; home keeps it.
6. **Bundle import remaps enum codes** (they're interned per machine) and
   inserts devices/signals it lacks without overwriting names.
7. **Everything leaves compressed, losslessly.** A bundle stores samples as
   columns on each series' exact quantum (`columns.py`), zstd'd: the 3.85 GB
   Phoenix export is a 2.98 MB bundle (was 14.7 MB), bit-exact, checked by
   `sync_check`. Team files go zstd'd unless that saves under 10% (the CAD
   model: 360 → 58 MB). Bundle format 1 (gzip) still imports.

---

## Queries

Every query lives in a module, not inline in a widget. Add new ones here.

### `app.robot.repository`

| Function | Returns | Reads |
|---|---|---|
| `devices()` | `list[DeviceRow]` | the CAN map — **real CAN devices only** (`can_id >= 0`) |
| `set_device_name(device_id, label, subsystem)` | — | writes the CAN map |
| `add_device(device_type, can_id, label, subsystem)` | new id, or `None` if it exists | pre-register before a log arrives |
| `delete_device(device_id)` | `bool` | refuses while `series` references it |
| `unnamed_count()` | `int` | drives the "13 still unnamed" badge; filters `can_id >= 0` like `devices()` |
| `detected_hardware()` | `dict[int, str]` | device_id → motor the controller reported (`ConnectedMotor`), e.g. `KrakenX60` |
| `sessions()` | rows | import list, newest first |
| `session(session_id)` | row | |
| `set_session_meta(session_id, match_key, keep)` | — | |
| `peak_by_signal(session_id, name)` | rows | per-device min/max/mean, name-resolved. **Reads `series` only** |
| `sticky_faults(session_id)` | rows | every latched fault that tripped |
| `battery_low(session_id)` | `float` | lowest `SupplyVoltage` across all devices |
| `signal_names(session_id, klass)` | `list[str]` | populates pickers |
| `trace(session_id, device_id, name)` | rows | full-resolution change points for charting |
| `rollup(session_id, device_id, name)` | rows | 1-second buckets — prefer this for dashboards |
| `wheel_distance(session_id, can_id, **kw)` | `Distance` | see below |
| `drive_distances(session_id, **kw)` | `list[Distance]` | every drive wheel |
| `session_totals()` | dict | counts + telemetry file size |

**Measured timings** against the 62M-row import: `peak_by_signal` 0.07 ms,
`sticky_faults` 0.06 ms, `battery_low` 0.07 ms, `rollup` 0.26 ms,
`trace` (133k points) 38 ms.

> Rule of thumb: if a query touches `samples.sample` it is a *chart*; everything
> else should be answerable from `series`, `session_constant` or `fault_event`.

### `app.robot.distance` — wheel odometry

`distance_for(session_id, can_id, gear_ratio=…, wheel_diameter_in=…)` returns a
`Distance` exposing `motor_rotations`, `wheel_rotations`, `inches`, `feet`,
`miles`, `net_inches`, `moving_s`, `duty_pct`.

Supporting: `drive_motors(session_id)` picks drive motors by peak velocity;
`check_units(session_id, can_id)` asserts the units assumption below.

**Two constants at the top of the module drive every absolute figure:**

```python
GEAR_RATIO = 6.02           # SDS MK5i, R2 gearing — supplied by the team
WHEEL_DIAMETER_IN = 4.0     # nominal ← CONFIRM against the fitted wheels
```

The ratio came from the team; it could not be confirmed from a text source
because SDS publishes the MK5i ratios only as an image. (AndyMark does confirm
the *steering* ratio is 26:1, which is not what this uses.) Wheel diameter is
still nominal — a worn tread measures smaller, and the error is linear.

Sensitivity: at a 4" wheel, ratio 4.59 → 6.75 swings a result from 0.000502 to
0.000342 miles, so neither constant is a detail.

The maths, and why it is not simply `Δposition`:

- *Net displacement* is `Position(last) − Position(first)`. Drive out and back
  and it reads zero. `Position` **is** a true accumulator here — verified,
  ∫v·dt matched Δposition on all four drive motors — but it answers the wrong
  question.
- *Distance travelled* is the path length, `∫|velocity| dt`. That is what this
  computes.

```sql
-- Zero-order hold over a change-only series.
WITH bounds AS (
    SELECT CAST(COALESCE(duration_s,0)*1000 AS INTEGER) AS end_ms
    FROM log_session WHERE id = ?
),
pts AS (
    SELECT sa.t_ms, sa.v AS rps,
           LEAD(sa.t_ms) OVER (ORDER BY sa.t_ms, sa.ord) AS next_ms
    FROM samples.sample sa
    JOIN series se ON se.id = sa.series_id
    JOIN device d  ON d.id  = se.device_id
    JOIN signal s  ON s.id  = se.signal_id
    WHERE se.session_id = ? AND d.device_type = ? AND d.can_id = ?
      AND s.name = 'Velocity'
),
seg AS (   -- each point holds until the next; the last runs to session end
    SELECT rps,
           (COALESCE(next_ms,(SELECT end_ms FROM bounds)) - t_ms)/1000.0 AS dt_s
    FROM pts
)
SELECT SUM(CASE WHEN ABS(rps) > ? THEN ABS(rps)*dt_s END) AS motor_rotations,
       SUM(CASE WHEN ABS(rps) > ? THEN rps     *dt_s END) AS net_motor_rotations,
       SUM(CASE WHEN ABS(rps) > ? THEN dt_s         END) AS moving_s,
       SUM(dt_s)                                         AS span_s
FROM seg WHERE dt_s > 0;
```

Then `wheel_rotations = motor_rotations / GEAR_RATIO`, and
`miles = wheel_rotations × π × WHEEL_DIAMETER_IN / 63360`.

Verified against an independent Python integration: exact match on all four
motors. **The imported session is a bench test** — ~0.3% duty cycle, each wheel
rolled ~27 in in about 2 s of movement across a 9.6-minute log.

### `app.robot.diagnostics` — the pit board

What the crew needs between matches, as opposed to what a visitor wants.
`dashboard()` is one call returning everything the two overlays render;
everything else is a piece of it.

| Function | Returns | Reads |
|---|---|---|
| `dashboard(session_id=None)` | `Dashboard` | all of the below, in one pass |
| `vitals(session_id)` | `list[Reading]` | battery, draw, CAN, loop time, temps |
| `subsystems(session_id)` | `list[Subsystem]` | the team's own `RealOutputs/…` signals |
| `motors(session_id)` | `list[MotorRow]` | per-CAN-device temp / current / volts |
| `faults(session_id)` | `list[Reading]` | latched faults grouped by name |
| `latest_session_id()` / `latest_motor_session()` / `latest_app_session()` | `int \| None` | which session answers which half |

Three things to know before extending it:

- **Status comes from the robot's own latched faults, not from thresholds
  invented here.** A Talon knows its own temperature and current limits and sets
  `StickyFault_DeviceTemp` / `StickyFault_StatorCurrLimit` when it crosses one.
  Three published figures *are* used and each is named in the code: the **6.8 V**
  roboRIO brownout floor, the **20 ms** WPILib loop period, and 100% CAN
  utilisation. Do not add a fourth without a citation — a tile that goes red on
  a made-up number teaches the crew to ignore the colour.
- **`dashboard()` with no argument reads the newest session of *each kind*.** A
  pit pulls two files off one match and each holds half the picture: motors come
  from the newest log that has CAN devices, subsystems and `SystemStats` from
  the newest that has application signals. `Dashboard.sources` names both. Pass
  a `session_id` to pin the whole board to one log.
- **`latest_app_session()` matches on signal *paths*, not the `Robot`
  pseudo-device.** owlet emits a few non-CAN entries of its own (`RobotMode`,
  `AllianceStation`), so a hoot also lands rows on that device and would win the
  query while having no subsystem data at all.

Spiky signals (`FullCycleMS`, CAN utilisation) are reported as the **mean** with
the peak in the caption. The first cycle after boot is a real 10-second
`FullCycleMS` and also the least informative number in the log.

Subsystems are grouped out of the signal names (`RealOutputs/<name> Temp F`)
rather than from a hardcoded list, so renaming a mechanism in robot code renames
it on the board with no edit here.

Reads `series`, `session_constant` and `fault_event` only — never a raw sample.
Measured at 1.9 ms for the whole dashboard across two sessions.

### `app.robot.fun_facts` — audience slides

`slides()` returns `(title, body)` pairs built from the most recent import, for
the standard rotation on the presentation screens. They are listed and
selectable in **Control Screen → Presentation A/B → Standard Slides**. `summary_line()` names the
log they came from. Reads `log_session`, `series`, `fault_event`,
`session_constant` and `distance` — no raw samples. Returns `[]` when nothing is
imported.

### Other modules that own tables

| Module | Owns | Key functions |
|---|---|---|
| `app.music.library` | `tracks` | `scan(folder)`, `all_tracks(search)` |
| `app.music.eq` | `eq_presets` | `all_presets()`, `get_preset()`, `find_preset()`, `save_preset()`, `delete_preset()` |
| `app.admin` | `admin_credential` | `verify()`, `unlock()`, `change_password()`, `reset_to_default()` |
| `app.robot.ingest` | writes everything under `log_session` | `import_log()`, `delete_session()` |
| `app.robot.wpilog` | reads a `.wpilog` into records | `Reader`, `entry_identity()` |
| `app.robot.owlet` | shells out to CTRE's extractor | `convert()`, `find_owlet()`, `scratch_dir()` |
| `app.ai.local` | `analysis_run`; writes its own `analysis_board` rows | `SqliteSink`, `LocalToolbox`, `ThreadDB` |
| `app.ai.feedback` | `analysis_feedback` | `recent_runs()`, `verdicts()`, `set_rating()`, `set_acted()`, `set_score()`, `scoreboard()` |
| `app.ai.boards` | reads `analysis_board` for the screens | `latest()`, `to_dashboard()` |
| `app.ai.tools` | reads the log tables for a model | the nine tools, `call()` |

---

## Invariants that will bite you

**1. `(series_id, t_ms)` is not unique.** The Phoenix export emits two
*different* values for one signal in the same millisecond roughly 40,153 times
per session (0.065%). A key without `ord` silently drops them — `INSERT OR
REPLACE` overwrites and nothing errors. This was found by reconstructing a
series and finding 25 mismatches that should have been zero.

**2. Timestamps are milliseconds, not microseconds.** The line prefix carries
exactly three decimals. The microsecond-looking values in the file are the
device's own `Timestamp` *signal*, which is classified `meta` and dropped.

**3. Samples are change-only, so they are not evenly spaced.** Any integration
or averaging over `samples.sample` must be a **zero-order hold** — each point
weighted by `next_t − this_t`, the last extending to session end. Treating
stored points as regular samples is badly wrong; a signal that changed twice
would count the same as one that changed 100,000 times.

**4. Drive-motor units are motor-shaft rotations.** On this robot `Velocity ==
RotorVelocity` exactly, meaning no `SensorToMechanismRatio` is configured, so the
gear ratio must be applied in the query. If robot code ever configures that
ratio, `Velocity` becomes wheel units and dividing by `GEAR_RATIO` again would
under-report by ~5×. `distance.check_units()` guards this — call it rather than
assuming.

**5. A wpilog's timestamps are microseconds since robot boot, not since the log
started.** `wpilog.Reader` normalises them against the first data record and
divides to milliseconds, because `t_ms` is defined as milliseconds from session
start and `duration_s` is derived from the maximum. Reading `record.timestamp`
yourself and storing it would put every session's samples at an arbitrary offset
in the tens of millions, quietly breaking every window query.

**6. A wpilog barely compresses, and that is correct.** WPILib's DataLog only
appends when a value changes, so the importer's change-only pass has almost
nothing left to remove: measured on the team's own AdvantageKit logs, 932,365
records became 828,490 rows — **1.1×**, against 18.8× for a hoot of the same
robot. Judge the ratio against `source_kind` or it reads as a broken import.
`raw_rows` also counts array elements separately (a 24-wide PDH
`ChannelCurrent` is 24 records from one log entry), which shifts it further.

**7. Not every record in a wpilog can be stored.** `sample.v` is a REAL: string
arrays, msgpack and raw bytes have nowhere to go and are counted in
`ImportResult.skipped`, and a string signal past `wpilog.MAX_ENUM_LABELS`
distinct values is abandoned mid-file rather than allowed to grow an unbounded
`signal_enum`. A series that stops partway through a log is this, not a gap in
the data — check `ImportResult.enum_overflow`.

**8. `owlet` does not produce the same wpilog twice.** Measured: three
extractions of one 1 MB `.hoot` gave 590,619 / 588,134 / 590,580 records, three
different file sizes, three different checksums. The **values never disagree** —
every record present in two runs is byte-identical — and the entry list is
identical; owlet simply stops reading the final buffer at a slightly different
point, so the difference is confined to the **last ~0.2 s** of the log (~0.4% of
records, always the tail).

Consequences, both real:

- **Re-importing a hoot does not reproduce the previous session's row counts.**
  A diff against an earlier import is not evidence of a bug in this app.
- **A verification pass must audit against the exact wpilog that was imported**,
  not a fresh conversion, or it reports thousands of phantom mismatches. That is
  how the losslessness audit below was run.

**9. Cross-database foreign keys are not enforced.** `ON DELETE CASCADE` on
`series` does **not** reach `samples.sample`. Deleting a session must delete
sample rows explicitly; `ingest.delete_session()` is the only correct way.

**10. Ingest uses its own connection.** A 75-second write transaction on the
shared `db` connection would stall every UI read. WAL lets readers keep working
against the previous snapshot until it commits. Always run imports off the GUI
thread.

**11. Never version the samples schema.** See [The two files](#the-two-files).

---

## Adding to the database

1. **New table** → new `@register_migration` function in `app/db/migrations.py`,
   numbered next in sequence. Never edit a shipped one. Document it here.
2. **New query** → a function in the owning module (`repository.py` for robot
   logs), never inline SQL in a widget. Add it to the [Queries](#queries) table.
3. **Bulk time-series data** → `samples`, and remember it is disposable: nothing
   irreplaceable may live there.
4. **Check the plan first** for anything robot-log shaped —
   [the storage plan](https://claude.ai/code/artifact/b07af1ed-30eb-4ac4-9296-c7a36fc8e77e)
   has the measured numbers behind these choices.

### Not built yet

- **Retention tiering.** At ~83 MB per 10-minute session, a competition day is
  ~4 GB. The plan calls for evicting `samples.sample` on a rolling window while
  keeping `sample_1s`, `series`, `session_constant` and `fault_event` forever.
  `log_session.keep` exists for this and is not yet honoured by anything.
- **Scoring runs from the ratings.** The ratings sync and are stored;
  nothing turns them into a per-model / per-prompt score yet (home,
  `home/REQUESTS.md` R3).
- **Rendering `analysis_board` on a screen.** Boards arrive and are stored;
  nothing paints them yet (the plan: an Analysis face built from a
  `diagnostics.Dashboard`, `home/HANDOFF.md` M6).
