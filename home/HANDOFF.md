# Home side: handoff

**Who this is for:** a Claude session running on (or next to) the team's home
SQL Server, with access to it. This repo deliberately holds **no** server
name, login, path or password for that machine; you have them there, this
file doesn't. Read this file, then `sync-hub/README.md`, then
`app/db/sync/bundle.py`'s docstring. Kick-off prompt: `home/KICKOFF.md`.

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
(`breakaway-home/`), Python 3.12+, `uv`.

### M1. Reach the hub from home

* Secrets on the home machine only: `SYNC_URL=https://sync.bh-stack.com`,
  `SYNC_HOME_TOKEN` (the `HOME_TOKEN` Worker secret), a machine id like
  `home-<hostname>`.
* HTTPS through the OS verifier (`truststore`), same reason as the pit app:
  see `app/net.py`'s docstring.
* **Check:** `GET /v1/status` returns the pits in `machines`.

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
2. `kind = "bundle"`: **don't reimplement the format.** Copy
   `app/db/sync/columns.py`, `codec.py` and `bundle.open_bundle()` from the
   pit repo (standard library only; Python 3.14+ for `compression.zstd`).
   `open_bundle(path, scratch)` turns either bundle format into a plain SQLite
   file whose tables include `sample` and `sample_1s` → read them → insert
   into `telemetry.*` keyed by the bundle's `uid` (from `bundle_meta`). Enum
   labels go in
   `telemetry.session_enum` **per session** (codes differ between pits).
   Use `SqlBulkCopy`-style bulk insert for `sample`; it's millions of rows.
   `kind = "raw"`: always a zstd frame of the original log (archive it
   compressed; `zstd -d` restores it). `kind = "file"`: the row's `data`
   says `codec: "zstd"` (stored compressed; `sha` is the original's) or
   `"none"` / absent (stored as is).
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
* `home_agent restore`: force-push every non-deleted row of
  `sync.row_state` (and re-upload any blob the hub lacks) into a **fresh**
  hub. Pits see the new epoch and reconcile on their own.
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
copy them into `evidence`. **No free-form SQL tool** for a local model: the
tools *are* the safety rail.

**Check:** `npx @modelcontextprotocol/inspector uv run home_mcp.py` opens the
MCP Inspector in a browser; call each tool by hand.

### M6. The two-agent pipeline (Ollama)

Runs after M3 ingests a new session (or on demand):

1. **Analyst** (a model with tool calling; see "Choosing models"): given the
   session uid and a fixed question ("What should the pit crew look at before
   the next match?"), calls MCP tools, and must answer in
   `insight.schema.json`. Pass that schema as Ollama's `format` (structured
   output) on the final turn. Store the whole transcript in `analysis.run`.
2. **Validate the insights** in code: schema-valid; every `metric.value`
   equals a number in its `evidence`; `severity: fault` only with a `faults`
   tool result behind it. Reject → record why, stop. Never "fix" a figure.
3. **Designer** (can be a smaller model, no tools): given *only* the
   insights JSON and the board schema, fills `board.schema.json` via
   `format`. Its job is choosing and wording: which findings become cards,
   worst first, the headline, short labels. It never sees the database.
4. **Publisher** (plain code, no model): validates the board (schema;
   every number in a string field appears in the insights; at most one
   `fault` region; ≤ 8 vitals), **fills each card's `shape` from
   `telemetry.sample_1s` using its `series` ref**, adds `evidence`, then
   queues `{tbl: "analysis_board", uid: "<session_uid>:<run id>", op:
   "upsert", data: board}` in `sync.push_queue`. The agent force-pushes it.
5. Pits receive it into their `analysis_board` table.

**Check:** a board for a real session lands in a pit's `analysis_board`
(`sqlite3 pit_display.db "SELECT title FROM analysis_board"`).

**Not yours:** drawing it. The pit repo renders boards with the diagnostics
board's painter (a follow-up there: an "Analysis" face that builds a
`diagnostics.Dashboard` from the spec). Keep to the schema and it will paint.

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
