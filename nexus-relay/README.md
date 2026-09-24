# nexus-relay

Breakaway's one static address for everything frc.nexus:
**`https://nexus.bh-stack.com`**. A Cloudflare Worker and one Durable Object
per event key. It runs on Cloudflare's edge, so there is no tunnel and no
`cloudflared`, and nothing on a pit laptop or in the home lab has to be up for
it to work.

    frc.nexus ──POST /nexus/webhook──▶ Worker ──RPC──▶ EventRoom (one per event key)
                                                     • newest snapshot in ctx.storage
                                                     • pulls Nexus itself every 30s while anyone listens
                                                     • fans out to hibernating WebSockets
    pit display ◀──wss://…/api/v1/event/{key}/ws──────┘
    pit display ──GET /api/v1/…──▶ Worker ──edge cache──▶ frc.nexus/api/v1/…

How the pit display uses it, and what everything on the Telemetry panel
means, is in [`../NEXUS.md`](../NEXUS.md#the-relay). This file covers running
the relay itself.

## Routes

| Route | Auth | What |
|---|---|---|
| `GET /` | none | A portal page: running, and the webhook URL to register. No event data |
| `GET /healthz` | none | `{"ok":true,"apiKey":bool,"webhookToken":bool}`: whether the secrets are set, never their values |
| `POST /nexus/webhook` | `Nexus-Token` | Both Nexus webhooks. **Always 200**, as explained below |
| `GET /api/v1/events` | Bearer | Mirror of Nexus's `/events`, edge-cached 5 min |
| `GET /api/v1/event/{key}` | Bearer | The held snapshot, pulled fresh first if it is more than 20s old |
| `GET /api/v1/event/{key}/ws` | Bearer | The WebSocket: the held state on connect, then every newer one |
| `GET /api/v1/event/{key}/{pits,map,teams,inspection,alliances}` | Bearer | Mirrors, edge-cached 30s–5 min. Nexus's 404s pass through |
| `GET /api/v1/event/{key}/relay` | Bearer | This room's counters: webhooks, pulls, subscribers |

`/api/v1/` mirrors Nexus's own paths exactly, so the app's `RelayClient` is
the same `Client` with a different base URL and header.

**WebSocket messages** are JSON: `{"type":"status","data":<EventStatus>,"relay":<stats>}`
for new data, `{"type":"relay","relay":<stats>}` when only the counters moved.
The client sends `"ping"`, answered `"pong"` by the runtime without waking the
object, and `"refresh"` to make the relay pull Nexus immediately.

## Decisions that matter

- **Every webhook POST gets a 200, even one with a wrong token.** Nexus does
  not retry, and it disables a hook that keeps failing without telling
  anyone. A 401 on a token mismatch would turn a token rotation into a
  silently dead hook. A POST with the wrong token is ignored, logged, and
  **counted as `refused`** in that event's room, so the pit's Telemetry panel
  shows the mismatch in plain words.
- **Team webhooks are merged, not stored.** A `MatchStatus` body has one
  `match` and no schedule. Storing it as the snapshot would hand every display
  an empty event. `ingestMatch` replaces that one match in the held snapshot.
- **The relay pulls Nexus itself, but only while someone is listening.**
  Webhooks fire only when something changes, so a quiet event or a broken
  registration would otherwise leave the data stale. A Durable Object alarm
  pulls whenever the snapshot is unconfirmed for 30s and a socket is open. With
  no subscribers the alarm is not re-armed and the object sleeps.
- **Nothing that matters lives in a class field.** Hibernation discards the
  object between events, so the snapshot and the counters are in
  `ctx.storage`. The only field, `pulling`, deduplicates concurrent pulls, and
  losing it costs at most one extra fetch.
- **Freshness is compared after the upstream fetch.** An outbound `fetch`
  opens the Durable Object's input gate, so a webhook can land while a pull is
  out. `offer()` reads, compares and writes with only storage awaits between
  them, which the gate keeps atomic.
- **No Cloudflare Access on this hostname.** Nexus cannot complete an Access
  login. The API and socket are protected by the bearer token and the webhook
  by `Nexus-Token`.

## First deploy

> **Done (2026-09-23).** Kept as the procedure for a new Cloudflare account.

You need Node. On the Mac: `brew install node`.

```bash
cd nexus-relay
npm install
npx wrangler login                       # opens a browser; the Cloudflare account that owns bh-stack.com
npm run check                            # tsc --noEmit
```

**Delete any existing DNS record named `nexus`** in the `bh-stack.com` zone
(Cloudflare dashboard → DNS). Deploy fails if one exists: a custom domain
creates its own record and certificate.

Three secrets. None of them goes in `wrangler.jsonc`, this README or git:

```bash
openssl rand -hex 32 > ../secrets/nexus_relay_token   # the CLIENT_TOKEN; this file is its copy of record
npx wrangler secret put CLIENT_TOKEN < ../secrets/nexus_relay_token
npx wrangler secret put NEXUS_API_KEY    # the key from frc.nexus/api
npx wrangler secret put NEXUS_WEBHOOK_TOKEN   # a placeholder for now; the real one comes after registering
npm run deploy
```

On the first deploy, wrangler may say the secrets need the Worker to exist
first. If so, deploy once, then put the secrets. A secret change takes effect
immediately, with no redeploy.

Then check it from the repo root with the new token:

```bash
uv run tools/relay_check.py --token <CLIENT_TOKEN>
```

## Registering with Nexus

> **Done (2026-09-23).** Registered for *live event status, all events*. It
> only needs repeating if the URL or the token changes.

At [frc.nexus/api](https://frc.nexus/api), signed in as the team:

1. Register `https://nexus.bh-stack.com/nexus/webhook` for **live event
   status, all events**. That one registration is the whole integration: every
   event's snapshot lands in its own room, and the pit display picks which
   event it shows. The per-team webhook is optional. Our matches are already
   in every event snapshot, and a team push only makes the relay merge one
   match.
2. Copy the token Nexus shows and run `npx wrangler secret put NEXUS_WEBHOOK_TOKEN`.
3. On the team's demo event (also on that page), advance a match and watch
   `npm run tail` for `{"webhook":"event","accepted":true,…}`. Or, from the
   repo root:
   `uv run tools/relay_check.py --event <demo key> --webhook-token <T>`.

**The URL never changes.** The relay routes each POST by the `eventKey` in
its body, so one all-events registration serves the whole season. **Nexus
itself offers no filter** (its docs describe only "live event status" and
"match status for a team number"), so the choice of event is made in the app:
Control → Event Feed → the event key. Any event is ready the moment it is
picked, because its room already holds the latest snapshot.

**Initial DNS lookups can fail for a while.** If anything looked up
`nexus.bh-stack.com` before the first deploy, a resolver may have cached "no
such name" for up to 30 min (the zone's SOA minimum is 1800 s), and the home
router is usually the one holding it. Cloudflare's nameservers answer at once:
`dig +short nexus.bh-stack.com @1.1.1.1`.

**If Nexus's POSTs never arrive:** check whether Bot Fight Mode is on for
`bh-stack.com` (Security → Bots) and look at Security → Events for challenged
POSTs to `/nexus/webhook`. The free plan cannot exempt a single path, so if
Nexus is being challenged, turn Bot Fight Mode off for the zone.

## Onto a pit machine

The pit machine needs the `CLIENT_TOKEN` and nothing else. Paste it on
Control → Event Feed → Nexus access (admin), or ship it in a setup file:

```bash
echo -n '<CLIENT_TOKEN>' > secrets/nexus_relay_token
uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json --event 2026mitry
```

## Local development

```bash
cp .dev.vars.example .dev.vars           # then set NEXUS_API=http://127.0.0.1:8790/api/v1
npm run dev                              # wrangler dev on :8787, real workerd, local DO storage
uv run tools/relay_check.py --local      # from the repo root; runs a fake frc.nexus on :8790
```

`--local` runs every check, including the webhook write path, against the
spec's example payloads. This is how the relay was built and tested:
workerd 1.20260921 and wrangler 4.136, compatibility date `2026-09-01`.

## Changing it

**Deploys are automatic.** A push to `main` that touches `nexus-relay/` runs
`.github/workflows/relay.yml`: `npm ci`, typecheck, dry-run bundle, deploy, then
`/healthz` must answer with both secrets present or the run goes red. It can
also be started by hand (Actions → *Deploy relay* → Run workflow). It needs one
repository secret, `CLOUDFLARE_API_TOKEN` (Cloudflare → My Profile → API Tokens
→ *Edit Cloudflare Workers* template). The relay's own secrets live in
Cloudflare and deploys never touch them. `npm run deploy` from a logged-in
machine still works for an emergency.


- **Migrations are append-only.** `v1` created `EventRoom`. Never edit a
  deployed tag. A rename or a new class is `v2`.
- **`compatibility_date`** pins runtime behaviour. Bump it on purpose and
  re-run `relay_check.py` when you do.
- **Lost or leaked `CLIENT_TOKEN`? Make a new one.** Nothing else depends on
  it: `openssl rand -hex 32 > ../secrets/nexus_relay_token`, then
  `npx wrangler secret put CLIENT_TOKEN < ../secrets/nexus_relay_token`, then
  give each pit machine a new setup file. Until a machine has it, that machine
  polls frc.nexus directly if it has an API key, or shows the feed as off.
- **Rotating `NEXUS_WEBHOOK_TOKEN`** is safe in either order. Mismatched
  POSTs are counted as refused and still answered 200, so Nexus never disables
  the hook.

## Cost, with the all-events feed

The free plan's binding limit is **100,000 rows written per day** across all
Durable Objects (it resets at 00:00 UTC). Requests (100,000/day), reads
(5M/day) and storage (5 GB) are far from it. Every room keeps its snapshot
and counters under **one key**, so each webhook costs one row:

| | per day |
|---|---|
| One in-season event: ~150 matches × ~4 status changes, plus breaks and announcements | ~700 webhooks |
| A busy Saturday with ~40 Nexus events | ~28,000 rows |
| Our own event's pulls while a pit is subscribed (one every 30 s) | ~1,500 rows |

That leaves roughly 3× headroom on the worst weekend. **If the quota is ever
exhausted, writes fail but pushes do not.** `offer()` broadcasts to connected
pits before it saves, and a failed save is logged
(`{"storage":"write failed"}` in `npm run tail`), not thrown. A pit that
reconnects afterwards gets the last snapshot that was saved, then the next
push. Check Workers & Pages → nexus-relay → Metrics after the first busy
weekend. The Workers Paid plan ($5/month) raises the row limit to 50M/month
if it is ever needed.

Idle sockets cost nothing, because they hibernate.
