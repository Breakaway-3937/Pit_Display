# pit-sync-hub

The Cloudflare Worker where every pit machine and the home server meet:
`https://sync.bh-stack.com`. A separate npm/TypeScript subproject, like
`nexus-relay/`.

```
 pit machine A ─┐                                   ┌─ home agent ─▶ SQL Server (the master)
 pit machine B ─┼─▶ Worker ─▶ SyncHub (one DO) ◀────┤   pulls everything, archives every
 pit machine C ─┘      └────▶ R2 bucket pit-sync ◀──┘   file, force-pushes home's edits
```

**Why Cloudflare is in the middle.** A pit laptop at an event can't reach the
house, and the house takes no inbound connections. Both can reach Cloudflare,
over the same verified TLS the app already uses for the Nexus relay. So the
hub is the meeting point, and the home SQL Server is the master copy: it
keeps every version it has ever seen, every file, and it's the only writer
that may overrule a conflict. If the hub is ever lost, the home agent rebuilds
it (a new `epoch` makes every pit re-read from zero).

## The model

* **A row is `(tbl, uid)` → its newest accepted state** (`op`, JSON `data`,
  `origin` machine, `at`). Tombstones (`op: "delete"`) are kept forever, so a
  machine that was off for a week still learns what was deleted.
* **`seq` is one counter for the whole hub.** "Everything since N" is one
  indexed range; a machine's position is one integer.
* **Pushes are judged on `base`**, the seq the machine last saw for that row.
  If the hub holds a newer write *from another machine*, the push is a
  `conflict` and the answer carries the current row, which the pit applies.
  First to reach the hub wins, on the hub's clock, never a laptop's.
* **Home may `force`.** A pit sending `force` is ignored.
* **Files are blobs named by their SHA-256** in R2: idempotent uploads,
  verifiable downloads. A single PUT is checked by R2 against the hash; a
  multipart one is checked by whoever downloads it.

What the tables are, column by column, is `app/db/sync/tables.py` and
DATABASE.md "Sync". The hub doesn't know or care: any `tbl` matching
`^[a-z][a-z0-9_]{0,39}$` is stored.

## API

Every `/v1/` call carries `Authorization: Bearer <PIT_TOKEN or HOME_TOKEN>` and
`X-Pit-Machine: <id>` (`^[A-Za-z0-9:._-]{1,80}$`). Optional `X-Pit-Name`,
`X-Pit-Version` show in `/v1/status`.

| Call | Body / query | Answer |
|---|---|---|
| `GET /healthz` | (no auth) | `{ok, pitToken, homeToken}` |
| `POST /v1/push` | `{changes: [{tbl, uid, op, base, data}], force?}` (≤ 500) | `{epoch, head, results: [{tbl, uid, status: ok\|conflict\|invalid, seq?, current?, reason?}]}` |
| `GET /v1/changes` | `?since=N&limit=500` (≤ 1000) | `{epoch, head, changes: [{seq, tbl, uid, op, data, origin, at}], more}` |
| `GET /v1/status` | | `{epoch, head, rows, tombstones, blobs, blobBytes, blobsUnacked, tables, machines}` |
| `HEAD /v1/blob/{sha}` | | 200 / 404 |
| `GET /v1/blob/{sha}` | | the bytes |
| `PUT /v1/blob/{sha}` | the bytes (≤ 95 MB), `X-Blob-Kind`, `X-Blob-Name` | `{ok, sha, existed}` |
| `POST /v1/blob/{sha}/mpu` | | `{uploadId}` or `{existed: true}` |
| `PUT /v1/blob/{sha}/mpu/{id}/{n}` | one part (≥ 5 MB except the last) | `{partNumber, etag}` |
| `POST /v1/blob/{sha}/mpu/{id}/complete` | `{parts, bytes}` | `{ok, sha}` |
| `DELETE /v1/blob/{sha}/mpu/{id}` | | abort |
| `GET /v1/blobs` | `?unacked=1&limit=` | `{blobs: [{sha, bytes, kind, name, origin, created, home_acked}]}` |
| `POST /v1/blob/{sha}/ack` | home only | `{ok}`: home has archived it |
| `DELETE /v1/blob/{sha}` | home only | removes it from R2 |
| `DELETE /v1/machine/{id}` | home only | forgets a retired or test machine (its rows stay) |

`kind` is `bundle` (a log session, `app/db/sync/bundle.py`), `raw` (the
original log file, for the archive) or `file` (judges slides, CAD).

## Set up (once)

```bash
cd sync-hub
npm install
npx wrangler r2 bucket create pit-sync        # R2 needs to be enabled on the account
npx wrangler secret put PIT_TOKEN             # what every pit machine presents
npx wrangler secret put HOME_TOKEN            # what the home agent presents; different
npm run deploy                                # creates sync.bh-stack.com
curl https://sync.bh-stack.com/healthz
```

Generate each token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Either secret may hold several comma-separated tokens, so one can be rotated
without locking anyone out (`home/OPERATIONS.md`, "Rotating a token").
The pit token goes to pit machines in a pit setup file (`sync_token`, see
`app/provision.py`); the home token goes only to the home machine.

## Check it

```bash
uv run tools/sync_check.py --local     # starts its own wrangler dev, fresh state
PIT_SECRET_SYNC_TOKEN=… SYNC_HOME_TOKEN=… uv run tools/sync_check.py --url https://sync.bh-stack.com
```

**The `--url` form writes test rows** (a checklist, an EQ preset, a board)
into whatever hub it points at. Point it at the real one only before the team
has data in it, or not at all.

## Budget (free plan)

Durable Object storage bills a row per write, 100,000 a day across the
account (the Nexus relay shares it). A push writes one row per change plus the
machine's row; a pull writes the machine's row. Sixty-second cycles on five
machines is ~15,000 a day at rest. R2's free tier is 10 GB with no egress
fees. Everything is zstd'd before it's sent: log bundles are ~1-3 MB each
(the 3.85 GB test log is 2.98 MB), the CAD model 58 MB, raw logs (off by
default) up to 512 MB compressed. The home agent deletes raw blobs from R2
once it has archived them.

## Rules

* Everything in storage, nothing in class fields, no timers (same as the relay).
* The SQL API is synchronous: keep `push()` free of `await`, so two pushes
  can't interleave between the conflict check and the write.
* Never edit a deployed migration tag in `wrangler.jsonc`; add one.
