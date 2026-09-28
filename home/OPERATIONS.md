# Home side: operations

The runbook for keeping the home side healthy once `HANDOFF.md`'s milestones
are built. For the Claude session on the home machine, and for whoever sits
at it. Nothing here names the server, its logins or its paths: those live on
the home machine only.

## What runs where

| Piece | Runs on | Code lives in | Deployed by |
|---|---|---|---|
| Sync hub (`sync.bh-stack.com`) | Cloudflare: Worker + one Durable Object + R2 bucket `pit-sync` | pit repo, `sync-hub/` | `npm run deploy` from a machine logged in to wrangler (the dev Mac) |
| Home agent (mirror, archive, push queue) | home machine | home project | home session |
| MCP server + Ollama pipeline | home machine | home project | home session |
| Pit machines | the pit laptops | pit repo, `app/db/sync/` | the app's self-update |

**Everything irreplaceable is at home:** the SQL Server database and the
blob archive folder. The hub can be rebuilt from them; the pits hold copies
of the team rows and whatever logs they pulled.

## Daily: is it healthy?

```bash
SYNC_HOME_TOKEN=… python home/hub_probe.py --cursor <sync.cursor.seq>
```

Read-only (it never registers as a machine). Exit 0 healthy, 1 warnings,
2 unreachable or token refused. Each warning:

| Warning | Means | Do |
|---|---|---|
| home is N changes behind | the agent's M2 loop isn't pulling | check the service is running and its log |
| N blobs not archived | the M3 loop isn't archiving | same; then check disk space on the archive |
| R2 above 70% of the free tier | retention isn't pruning | run the prune (below) |
| *pit* not seen for N days | a laptop retired, or stopped syncing | retire it (below), or check it: Telemetry → Team sync on that laptop says why |
| exit 2, 401 | the home token was rotated or mistyped | "Rotating a token" |
| exit 2, unreachable | the internet, or Cloudflare | `curl https://sync.bh-stack.com/healthz`; the hub needs no action to recover |

Also worth a glance weekly in SQL Server: `analysis.run` rows with
`status = 'rejected'` (a model producing numbers it can't back up) and
`sync.blob.error`.

## The agent as a service

* Start at boot, restart on failure, whatever the OS offers for that. It
  must survive the home machine rebooting while nobody is there.
* **One instance only.** Take a lock (a lock file, or `sp_getapplock` in
  SQL Server) at start; two agents would double every `row_history` row.
* **One stable machine id**, e.g. `home-<hostname>`, in its config. Every
  distinct `X-Pit-Machine` that pulls or pushes is registered on the hub and
  shows in every pit's Telemetry list. Scripts and tests use that same id,
  or only call `GET /v1/status` (which registers nothing).
* Log to a file, rotated. Log each cycle's pulled / archived / pushed counts
  and every error, one line each.
* 60 s cycle. The Durable Object's free budget is 100,000 row writes a day
  across the account (the Nexus relay shares it); the agent costs ~2 per
  cycle, ~3,000 a day.

## Backups

* SQL Server: the database's normal full + log backups, **off the machine**.
* The archive folder (`<archive>/<kind>/<sha[:2]>/<sha>`): back it up with
  the database. Files are content-addressed, so an incremental copy is exact.
* Test a restore once: a restored database plus archive must be able to
  rebuild the hub ("Rebuilding the hub").

## R2 retention (the prune)

The free tier is 10 GB stored. A job, weekly:

* `raw` blobs: `DELETE /v1/blob/{sha}` once archived (`sync.blob.archived_at`)
  and older than 30 days. Pits never download these.
* `bundle` blobs: delete once archived and older than 90 days. A pit that
  comes online later than that won't get that session's samples (it keeps
  the session row); acceptable for a season-old log.
* `file` blobs: **only** when no current `file` row points at them
  (`data.blob`, or `data.sha` when there's no `codec`). The live CAD model
  and judges slides must stay: every new pit downloads them.
* Never delete a blob the archive doesn't hold, verified by hash.

## Retiring a machine

A laptop leaves the team, or a test registered a machine:

```bash
curl -X DELETE https://sync.bh-stack.com/v1/machine/<id> \
     -H "Authorization: Bearer $SYNC_HOME_TOKEN" -H "X-Pit-Machine: home-<hostname>"
```

It comes off every pit's Telemetry list. Its rows and logs stay. A machine
that syncs again re-registers itself.

## Rotating a token

Both secrets accept several comma-separated tokens, so nobody is locked out
mid-change. Run `wrangler` from the machine that deploys the hub.

**The pit token** (`PIT_TOKEN`; on pits, `secrets/sync_token`):

1. New token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. `npx wrangler secret put PIT_TOKEN` with `NEW,OLD`.
3. Put `NEW` on every pit: a fresh pit setup file (it carries `sync_token`),
   or the file itself.
4. When the probe shows every pit seen since step 3: set `PIT_TOKEN` to `NEW`.

**The home token** (`HOME_TOKEN`): the same four steps with the home agent's
config instead of the pits. It can overrule any conflict: keep it only on
the home machine.

## Rebuilding the hub

When: the Durable Object's data is lost or corrupted, or the hub is being
moved. The R2 bucket is separate and usually survives.

1. Deploy a fresh hub (a new Durable Object, e.g. a new name in
   `hub(env)` or a new Worker). It mints a new `epoch`.
2. `home_agent restore`: force-push every row of `sync.row_state` with
   `op = 'upsert'` **and every tombstone** (`op = 'delete'`), 500 per push,
   in `seq` order. Tombstones matter: without them a pit that missed a
   delete would push the row back.
3. For every blob a restored row references that the bucket lacks, re-upload
   it from the archive **exactly as archived** (raw and file blobs are stored
   compressed; the sha is of the stored bytes).
4. Pits see the new epoch on their next cycle, re-read from zero and re-queue
   anything the new hub lacks. Nothing to do on the laptops.

Home's cursor: a new `sync.cursor` row for the new epoch, at 0.

## Keeping in step with the pit repo

Home reads formats the pit repo owns. Keep the pit checkout at the commit the
pits run (`git log -1` in the checkout; the pit app's version is in
Telemetry), and update it deliberately:

| When the pit repo changes… | Home does |
|---|---|
| `app/db/sync/tables.py` `SPECS` (a new synced table) | nothing is required (unknown `tbl`s are stored as JSON anyway); add a `team.*` view if it's worth querying |
| `bundle.FORMAT` / `READS` | pull the checkout; the agent imports `open_bundle` from it, so new formats just work |
| `home/contracts/*.schema.json` (`schema` number) | update the pipeline's validator and designer prompt before publishing boards in the new version |
| `sync-hub/` API | read `sync-hub/README.md`'s table again |
| DATABASE.md "Sync" rules | read them again |

After pulling: `uv run tools/sync_check.py --local` in the checkout (needs
Node; it starts its own throwaway hub) proves the pit side still works.
**Never** run it with `--url` against the live hub: it writes test rows.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| A pit's Telemetry: "refused this machine's token" | pit token rotated, that pit missed it | give it a new setup file |
| Pits disagree about a setting | one is offline, or conflicts | Telemetry → Team sync on each; the hub's copy is the truth (`sync.row_state`) |
| A log on one pit never reaches another | the receiver has `pull_logs` off, or the bundle was pruned | its `sync.json`; `sync.blob` for that `bundle_sha` |
| Bundle import fails "bundle format N" at home | pit checkout older than the pits | pull the checkout |
| `row_history` growing fast | a pit and home fighting over a row (home forcing an old value) | check `sync.push_queue` for a repeating entry |
| Hub answers 5xx | Cloudflare incident or a bad deploy | `npx wrangler tail` on the deploying machine; roll back with `npx wrangler rollback` |

## Never

* Put a credential, server name or path in the pit repo.
* Force-push from home without meaning to overrule the pits: `force` wins
  over whatever the crew just typed.
* Delete from `sync.row_history`, the archive, or `telemetry.*` because a
  pit deleted something. Home keeps everything; mark it (`deleted_at`).
* Give a local model a free-form SQL tool. The MCP tools are the rail.
