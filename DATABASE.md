# Database reference

**The authoritative description of how Pit Display stores data and every query
that reads it.** If you add a table, a migration, or a query, it belongs here —
this file is the contract between the schema and everything that reads it.

Contents: [Files](#the-two-files) · [Conventions](#conventions) ·
[Migrations](#migrations) · [Schema](#schema) · [Queries](#queries) ·
[Invariants](#invariants-that-will-bite-you) · [Adding things](#adding-to-the-database)

---

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
a column beats a child table here. `built_in = 1` rows are protected — saving
over one forks a copy named `"<name> (edited)"`.

#### `admin_credential`
Exactly one row, `CHECK (id = 1)`. PBKDF2-HMAC-SHA256, random per-credential
salt, 200k iterations. `is_default = 1` while the shipped password is unchanged,
which drives the nag in the admin bar.

**A UI lock, not a security boundary** — the file sits on the pit machine's disk.
See [`ADMIN_GUIDE.md`](ADMIN_GUIDE.md).

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

#### `log_session`
One row per imported file.

| Column | Notes |
|---|---|
| `source_file` | absolute path, **UNIQUE** — a second import of the same file fails loudly instead of silently doubling the data |
| `source_name` | basename, for display |
| `source_kind` | `hoot` \| `wpilog` — the same tables hold both sources |
| `device_serial`, `started_at` | parsed from the filename |
| `duration_s` | length of the log in seconds |
| `raw_rows`, `stored_rows` | compression audit. A ratio far off ~19× is the cheapest signal that the export format changed |
| `source_bytes`, `archive_path` | provenance for re-import after eviction |
| `match_key`, `notes`, `keep` | operator metadata; `keep = 1` exempts from future eviction |

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
Edited in **Control Screen → Pit Systems → Robot Logs**.

`repository.DeviceRow.display` falls back to `"TalonFX 11"` when unnamed —
**never render a raw CAN id if a label exists.**

`DeviceRow.detected` carries what the controller reported was physically plugged
in, read from the `ConnectedMotor` constant (`KrakenX60`, `KrakenX44`). It is
observed, not typed, and is empty for CANcoder and Pigeon2, which have no such
signal.

#### `signal`
209 rows for the current firmware; grows only when CTRE adds a signal.

`signal_class` drives which table a value lands in:

| Class | Meaning | Lands in |
|---|---|---|
| `telemetry` | live measurement | `sample` (+ `sample_1s`) |
| `fault` | live fault bit | `fault_event` intervals |
| `sticky_fault` | latched since last clear | `fault_event`, `t_ms_end` NULL |
| `config` | firmware/motor constants | usually `session_constant` |
| `meta` | the device's own `Timestamp` echo | **dropped** — redundant with the line timestamp |

Assigned by `app.robot.parser.classify()`.

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

## Queries

Every query lives in a module, not inline in a widget. Add new ones here.

### `app.robot.repository`

| Function | Returns | Reads |
|---|---|---|
| `devices()` | `list[DeviceRow]` | the CAN map |
| `set_device_name(device_id, label, subsystem)` | — | writes the CAN map |
| `add_device(device_type, can_id, label, subsystem)` | new id, or `None` if it exists | pre-register before a log arrives |
| `delete_device(device_id)` | `bool` | refuses while `series` references it |
| `unnamed_count()` | `int` | drives the "13 still unnamed" badge |
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
| `app.music.eq` | `eq_presets` | `all_presets()`, `get_preset()`, `save_preset()`, `delete_preset()`, `ensure_seeded()` |
| `app.admin` | `admin_credential` | `verify()`, `unlock()`, `change_password()`, `reset_to_default()` |
| `app.robot.ingest` | writes everything under `log_session` | `import_log()`, `delete_session()` |

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

**5. Cross-database foreign keys are not enforced.** `ON DELETE CASCADE` on
`series` does **not** reach `samples.sample`. Deleting a session must delete
sample rows explicitly; `ingest.delete_session()` is the only correct way.

**6. Ingest uses its own connection.** A 75-second write transaction on the
shared `db` connection would stall every UI read. WAL lets readers keep working
against the previous snapshot until it commits. Always run imports off the GUI
thread.

**7. Never version the samples schema.** See [The two files](#the-two-files).

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
- **WPILog ingest.** `source_kind` and the schema already accommodate it; the
  parser does not exist. The team's 55 application-level signals (robot states,
  PDH currents, shooter setpoints) live there, not in the hoot.
- **Nightly SQL Server sync.** `app/db/sync/` is an empty stub.
