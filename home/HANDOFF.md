# Home side: handoff

**Who this is for:** a Claude session running on (or next to) the team's home
SQL Server, with access to it. This repo deliberately holds **no** server
name, login, path or password for that machine; you have them there, this
file doesn't. Read this file, then `sync-hub/README.md`, then
`app/db/sync/bundle.py`'s docstring. Kick-off prompt: `home/KICKOFF.md`.
Once built, running it is `home/OPERATIONS.md`.

## Getting the code

The pit repo is `https://github.com/Breakaway-3937/Pit_Display` (private).
The sync work is on the **`beta`** branch (the future line; `main` stays the stable build) until it's merged into `main`;
check out whichever holds `app/db/sync/`. Keep this checkout beside the home
project and **import from it** rather than copying files (see M3): the pit
repo owns the formats. Python **3.14+** (the bundle reader uses the standard
library's `compression.zstd`), `uv` recommended.

## The hub today (2026-09-28)

**Live** at `https://sync.bh-stack.com`, seeded from the dev Mac:

* one machine, "Dev Mac" (`pit-9f07bf6075f9462a`);
* 33 rows: 2 checklists, 7 items, 5 EQ presets, 13 CAN names, the admin
  password hash, 2 settings documents, 2 team files, 1 log session;
* 3 blobs, 60.8 MB, **none archived yet**: the session's bundle (2.98 MB,
  from a 3.85 GB Phoenix export), the CAD model (57.8 MB zstd'd from 360 MB)
  and its `subsystems.json`. These are M3's first real test data.

`python home/hub_probe.py` (with `SYNC_HOME_TOKEN` set) shows the current
state at any time and changes nothing.

## What already exists (built and tested 2026-09-28)

```
 pit laptops (SQLite)  ──▶  sync.bh-stack.com (Cloudflare Worker + DO + R2)  ◀──  YOU BUILD THIS
   app/db/sync/              sync-hub/                                          home agent → SQL Server
```

* **Pit side** (`app/db/sync/`): every team-owned row is recorded by SQLite
  triggers and pushed; the hub's changes are pulled and applied. Robot log
  sessions travel as **bundles** (zstd'd SQLite, names not ids, samples as
  columns: the 3.85 GB test log is a 2.98 MB bundle, bit-exact), optionally plus the
  **original log file**. `tools/sync_check.py --local` proves it end to end:
  two machines, conflicts, deletes, files, a real log with enum remapping.
* **The hub** (`sync-hub/`): orders changes, holds each row's newest state and
  tombstones, stores files in R2 by SHA-256. API table in its README.
* **The contracts** you build against, in `home/`:
  * `schema.sql`: a starting SQL Server schema (sync mirror, team views,
    telemetry, analysis). Written blind; adapt it to the real server, keep
    its shape.
  * `contracts/insight.schema.json`: what the analyst agent produces.
  * `contracts/board.schema.json`: what the designer agent produces and the
    pit screens render.

**The decision already made:** the home SQL Server is the **master**. The hub
is the meeting point (pits can't reach the house; the house takes no inbound
connections). Home keeps every version and every file forever, may overrule
any conflict (`force`), and can rebuild the hub from scratch.

## What to build, in order

Each milestone ends with a check you can run. Don't start the next until it
passes. Suggested layout: a new repo or folder on the home machine
(`breakaway-home/`), Python 3.14+, `uv`.

### M1. Reach the hub from home

* Secrets on the home machine only: `SYNC_URL=https://sync.bh-stack.com`,
  `SYNC_HOME_TOKEN` (the `HOME_TOKEN` Worker secret; the person running this
  session carries it over from the dev Mac's `secrets/sync_home_token`), and
  **one stable machine id** like `home-<hostname>`. Every distinct
  `X-Pit-Machine` that pulls or pushes is registered on the hub and listed on
  every pit's Telemetry screen, so tests and scripts reuse that id (or only
  call `GET /v1/status`, which registers nothing).
* HTTPS through the OS verifier (`truststore`), same reason as the pit app:
  see `app/net.py`'s docstring. `app/db/sync/client.py` is a complete,
  tested client for every endpoint; importing it from the checkout is fine
  (it needs only `app/net.py`, standard library + `truststore`).
* **Check:** `python home/hub_probe.py` exits 0 and lists "Dev Mac".

### M2. Mirror rows into SQL Server (the home agent's core loop)

Run `schema.sql` (adapted). Then a loop every 60 s:

1. `GET /v1/changes?since=<sync.cursor.seq>&limit=1000` with the home token.
2. **Epoch check:** if the answer's `epoch` isn't the one in `sync.cursor`,
   the hub was rebuilt. Insert a new cursor row at 0 and read from there.
3. For each change, in one transaction per page: `MERGE` into
   `sync.row_state` on `(tbl, uid)`, and `INSERT` into `sync.row_history`.
   Advance the cursor to the last `seq`. Loop while `more`.
4. `GET /v1/status` → refresh `sync.machine`.

Idempotent by construction: replaying a page must change nothing.
**Check:** `SELECT * FROM team.checklist` matches what a pit shows; editing
a checklist item on a pit shows up in `sync.row_history` within a minute.

### M3. Archive every file; ingest log bundles

1. `GET /v1/blobs?unacked=1` → for each: `GET /v1/blob/{sha}` to a temp
   file, **verify the SHA-256**, move to the archive
   (`<archive>/<kind>/<sha[:2]>/<sha>`), record in `sync.blob`.
2. `kind = "bundle"`: **don't reimplement the format; import it.**
   `sys.path.insert(0, <pit checkout>)`, then
   `from app.db.sync.bundle import open_bundle` (verified: standard library
   only, no Qt, creates no files). `open_bundle(path, scratch_dir)` turns
   either bundle format into a plain SQLite file (delete it after) whose
   tables include `sample` and `sample_1s` → read them → insert into
   `telemetry.*` keyed by the bundle's `uid` (from `bundle_meta`). Enum
   labels go in
   `telemetry.session_enum` **per session** (codes differ between pits).
   Use `SqlBulkCopy`-style bulk insert for `sample`; it's millions of rows.
   `kind = "raw"`: always a zstd frame of the original log (archive it
   compressed; `zstd -d` restores it). Pits send raws only with
   `upload_raw` on (off by default: the bundle already holds every record).
   `kind = "file"`: the `file` row's `data` says `codec: "zstd"` (the blob
   is `data.blob`, compressed; `data.sha` is the original's) or `"none"` /
   absent (the blob is `data.sha`, stored as is). Archive blobs exactly as
   stored: a hub rebuild re-uploads them byte for byte.
3. `POST /v1/blob/{sha}/ack`.
4. After 30 days, `DELETE /v1/blob/{sha}` for `raw` blobs (the archive has
   them; R2's free tier is 10 GB). Keep `bundle` blobs 90 days: pits that come
   online late still pull them.
5. A `log_session` tombstone (a pit deleted it): set
   `telemetry.session.deleted_at`, **never** delete the data. Home keeps it.

**Check:** a log imported on a pit appears in `telemetry.session` with the
same series count and sample count the pit reports
(`SELECT COUNT(*) FROM series WHERE session_id = …` on the pit).

### M4. Home edits and hub rebuild

* Anything home changes goes through `sync.push_queue`; the agent pushes with
  `force: true` and records the returned `seq`.
* `home_agent restore`: rebuild a **fresh** hub from home, the procedure in
  `OPERATIONS.md` "Rebuilding the hub" (rows *and* tombstones, then blobs
  from the archive). Pits see the new epoch and reconcile on their own. Test
  it against `wrangler dev` (the pit repo's `tools/sync_check.py` shows how
  to start one with `--persist-to` a temp dir), never the live hub.
* **Check:** force-rename a checklist from home; every pit shows the new name
  within two cycles.

### M5. The MCP server (read-only, over SQL Server)

See "MCP, from zero" below for what this is. Build it with the official
Python SDK (`pip install mcp`, `from mcp.server.fastmcp import FastMCP`), on
a **read-only SQL login**. Tools, each returning small JSON (cap rows; an
LLM's context is the budget):

| Tool | Args | Returns |
|---|---|---|
| `list_sessions` | `match_key?`, `since?`, `limit=20` | uid, name, match_key, started_at, duration_s, origin |
| `session_overview` | `session_uid` | duration, battery min/mean, peak currents, loop time, CAN errors, brownouts, fault count: the diagnostics board's *vitals* (port `app/robot/diagnostics.vitals()`'s choices of signal) |
| `list_signals` | `session_uid`, `contains?` | device_type, can_id, device label, signal, unit, n_stored |
| `series_stats` | `session_uid`, `signal`, `device_type?`, `can_id?` | min, max, mean, last, per device |
| `series_1s` | `session_uid`, `signal`, `device_type`, `can_id`, `column=v_max`, `max_points=120` | [t_s, value] downsampled |
| `faults` | `session_uid` | device, signal, sticky, start/end ms |
| `subsystems` | `session_uid` | per subsystem stator/supply/PDH current, temp (port `diagnostics.subsystems()`) |
| `compare_sessions` | `session_uids[]`, `signal`, `device_type?`, `can_id?` | stats side by side |
| `device_names` | | device_type, can_id, label, subsystem (from `team.device`) |

Every tool result must include the arguments it ran with, so the pipeline can
copy them into `evidence`.

**Two additions from the first real trials (2026-09-30); home's server must
match, `app/ai/tools.py` is the reference:**

* `faults` also returns `summary` (latched faults grouped by fault:
  `fault`, `status` fault/warn as the pit board ranks them, `n_devices`,
  `devices`) and `by_device` (`device`, `device_type`, `can_id`, `n_faults`,
  `faults`). Without them the model counts rows itself, which the checks
  reject; and a count is only accepted when it is the count of the fault or
  device the finding names.
* each `session_overview` vital carries `signal` and `column`: the series
  the figure was read from (None for a derived figure such as CAN errors).
  The pipeline uses them to offer the designer a chart of that figure. **No free-form SQL tool** for a local model: the
tools *are* the safety rail.

**Check:** `npx @modelcontextprotocol/inspector uv run home_mcp.py` opens the
MCP Inspector in a browser; call each tool by hand.

### M6. The two-agent pipeline (Ollama)

**Built in the pit repo (2026-09-30): import it, don't write a second one.**
`app/ai/pipeline.py`'s `analyse(toolbox, sink, llm, session_uids, analyst=…,
designer=…)` is the whole run below, with no Qt, importable from the checkout
like `bundle.py`. Home supplies the two ends (`pipeline.Toolbox`,
`pipeline.Sink`):

* **Toolbox:** `specs()` returns the MCP server's tools as
  `{name, description, input_schema}`; `call(name, args)` returns a tool's
  `structured_content`. A thin wrapper over the MCP `ClientSession`.
* **Sink:** `start_run` inserts `analysis.run` (status `running`) and returns
  its id; `finish_run(id, status, designer, reject_reason, insights, board,
  board_uid, transcript, stats)` updates it; `board_uid` returns
  `<session_uid>:<run id>` (pits use `<session_uid>:<machine_id>-<run>`, so
  the two never collide); `publish(uid, board)` inserts into
  `sync.push_queue`.

`uv run tools/ai_check.py` in the pit checkout proves the checks without a
model; `--model <tag>` runs it for real. The steps it implements:

Runs after M3 ingests a new session (or on demand):

1. **Analyst** (a model with tool calling; see "Choosing models"): given the
   session uid and a fixed question ("What should the pit crew look at before
   the next match?"), calls MCP tools, and must answer in
   `insight.schema.json`. Pass that schema as Ollama's `format` (structured
   output) on the final turn. Store the whole transcript in `analysis.run`.
   Code runs `session_overview` first and hands it over, to ground the run.
2. **Validate the insights** in code (`app/ai/checks.py`): schema-valid;
   every evidence item names a call actually made; every `metric.value`
   equals a number in a cited result; `severity: fault` only with a latched
   fault cited. Problems go back to the model once; a second failure is
   rejected, recorded, stopped. Never "fix" a figure.
3. **Designer** (no tools; one model for both roles is fine): given *only*
   the insights JSON and the **chart options** (the signals the findings
   stand on), fills `board.schema.json` via `format`. Its job is choosing
   and wording: which findings become cards, worst first, the headline,
   short labels, and **which graph tells each finding best** (`charts`:
   `line` across the match, `bars` across devices, `sessions` across
   matches). It names a chart's source; it never writes data and never sees
   the database.
4. **Publisher** (plain code, no model): validates the board (schema;
   every number in a string field appears in the insights; at most one
   `fault` region; ≤ 8 vitals), **fills each card's `shape` and each chart's
   `data` through the same tools** (`series_1s`, `series_stats`,
   `compare_sessions`), adds `evidence`, then
   queues `{tbl: "analysis_board", uid: "<session_uid>:<run id>", op:
   "upsert", data: board}` in `sync.push_queue`. The agent force-pushes it.
5. Pits receive it into their `analysis_board` table.

**Check:** a board for a real session lands in a pit's `analysis_board`
(`sqlite3 pit_display.db "SELECT title FROM analysis_board"`).

**Not yours:** drawing it. The pit repo renders boards with the diagnostics
board's painter (a follow-up there: an "Analysis" face that builds a
`diagnostics.Dashboard` from the spec). Keep to the schema and it will paint.

### M7. Run it for real

Everything in `OPERATIONS.md`: the agent as a service (one instance, one
machine id, restart on failure), backups of the database *and* the archive,
the weekly R2 prune, and `hub_probe.py` on a schedule. **Check:** reboot the
home machine; within five minutes `hub_probe.py --cursor …` shows home
caught up, with no action from anyone.

## MCP, from zero

**MCP (Model Context Protocol)** is a standard way to give a language model
tools. You write an **MCP server**: a small program that says "I have these
tools, here are their argument schemas" and runs them when asked. A **host**
(the program talking to the model) connects to the server as an **MCP
client**, lists the tools, hands their schemas to the model, and when the
model replies "call `series_stats` with these args", the host calls the
server and gives the model the result. Repeat until the model answers.

```
 Ollama model ⇄ your host (MCP client) ⇄ home_mcp.py (MCP server) ⇄ SQL Server (read-only)
```

Why bother, versus plain function calls: the same server works unchanged
with Claude Desktop, Claude Code, the MCP Inspector, Open WebUI (through
`mcpo`), or your own host. You test the tools once and any model can use
them. The transport for a local setup is **stdio** (the host starts the
server as a child process); no ports.

**The host is the part Ollama doesn't provide.** Options:

1. **Write it** (recommended for the pipeline, ~150 lines): `mcp`'s
   `ClientSession` + `stdio_client` to list and call tools; the `ollama`
   Python package's `chat(model, messages, tools=[…])` to run the model;
   convert each MCP tool's `inputSchema` to Ollama's tool format. You control
   the loop: max tool calls, timeouts, the final structured-output turn.
2. **An existing host** for interactive poking: `mcp-client-for-ollama`
   (`ollmcp`), or Open WebUI with `mcpo`. Good for trying models; not for a
   scheduled pipeline.

## Choosing models

* **The analyst needs real tool calling.** Check `ollama show <model>`: its
  Capabilities must list `tools`. Qwen-family and Llama 3.1+ models are the
  usual reliable picks at 7–32B. Reasoning distills (e.g. DeepSeek-R1
  variants) think well but their tool calling in Ollama varies by tag.
  Test with the MCP tools before settling.
* **The designer needs structured output**, which Ollama enforces via
  `format` for any model. A small model is fine; it only chooses and words.
* **Evaluate on real sessions**: keep 5–10 sessions with a known right
  answer (a real sag, a real fault, a clean match) and score each model
  pair on them. `analysis.run` holds every transcript for this.
* Temperature 0–0.3 for both. The validator, not the prompt, is what makes
  output trustworthy.

## Rules carried over from the pit repo

* **Every number is real.** A figure on a board comes from a tool result;
  code checks it. Models choose and word, never compute or invent.
* **Status is the robot's own fault flags** (DATABASE.md, diagnostics), not
  an invented threshold. `fault` means a latched fault.
* **Team numbers are strings, timestamps are Unix ms** on the wire.
* **Names, not ids**, across machines: `(device_type, can_id)`, signal
  `(device_type, name)`, row `uid`.
* Credentials in environment/secret files on the home machine, never in a
  repo, a row, or a board.

## Where to look in the pit repo

| Question | File |
|---|---|
| What syncs, what doesn't | `app/db/sync/tables.py` docstring, DATABASE.md "Sync" |
| The bundle format | `app/db/sync/bundle.py` |
| How a pit applies a pull | `app/db/sync/engine.py` |
| The hub's API and budget | `sync-hub/README.md` |
| What the board fields mean | `app/robot/diagnostics.py` (`Reading`, `Subsystem`, `Dashboard`) |
| Which signals the vitals read | `app/robot/diagnostics.py` `vitals()`, `subsystems()` |
| The data's traps | DATABASE.md "Invariants that will bite you" |
