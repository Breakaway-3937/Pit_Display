# Nexus Relay — Claude Code Handoff

> **Claude Code: start here.** This file is the complete brief for adding live FRC Nexus data to the Breakaway pit display. Follow the "How to run this" section in order. **Do not skip ahead to code before Phase 0 is done.**

---

## How to run this

### Phase 0: Teach before building (required)
The owner (Brayden) is fluent in Python, SQL, data engineering, and home-lab infra. He is **new to Cloudflare Workers and Durable Objects** and has not reviewed this design in depth. Before writing any code, explain:

1. The full request flow: Nexus → Worker → Durable Object → pit display.
2. What a Durable Object is (a single-threaded, addressable actor with its own storage), and why it fits here better than Workers KV.
3. WebSocket Hibernation: why idle sockets are free, why state lives in `ctx.storage` and not in class fields, and what `setWebSocketAutoResponse` does.
4. Wrangler basics: `wrangler.jsonc`, secrets vs. vars, `compatibility_date`, custom domains, and why migrations are append-only.
5. Why the pit display keeps a polling fallback.

Keep it technical and direct, with no beginner framing. Then **stop and wait for questions.**

### Phases (pause after each one for the owner to verify)
| Phase | Work | Needs the owner to |
|---|---|---|
| 1 | Scaffold the Worker in `nexus-relay/`, deploy it, run the curl/websocat tests | `wrangler login`, enter secrets, delete any existing `nexus.bh-stack.com` DNS record |
| 2 | Register the webhook against a Nexus **demo** event and confirm deliveries in `wrangler tail` | Register at frc.nexus/api and paste the webhook token |
| 3 | Add `NexusRelayClient` to the pit display and wire it to the queue UI plus a LIVE/RECONNECTING badge | Check it visually |
| 4 | Add the fallback: after ~60s disconnected, poll the relay's `GET /events/{key}`, and if that fails, poll frc.nexus directly | Supply a Nexus API key |
| 5 | Add a pointer to the pit display's `CLAUDE.md` (see the end of this file) | — |

### Ground rules
- **Verify before trusting this doc.** Check current Cloudflare Workers/Durable Objects docs and the Nexus API docs (https://frc.nexus/api/v1/docs). Flag anything that has changed, especially whether the webhook payload includes `eventKey`, the webhook token header name, and Wrangler config syntax.
- **Never commit secrets.** Tokens go in `wrangler secret put`, `.dev.vars` (gitignored), and the pit display's `.env` or OS keyring. They never go in `wrangler.jsonc`, `CLAUDE.md`, or source.
- Keep the Worker as its own subproject (npm/TypeScript), separate from the pit display (Python/uv).
- Commit at the end of each phase.

---

## Context

- **Pit display:** a PyQt6 app run with `uv`, using a four-window architecture. It's configured through `CLAUDE.md` and `BREAKAWAY_BRAND.md`.
- **Goal:** show live queuing and match status from **frc.nexus**, pushed through Nexus webhooks.
- **Constraint:** **no dependency on the home lab.** The owner's current space has weak internet and no full redundancy. So there's no Cloudflare Tunnel and no self-hosted receiver; everything runs on Cloudflare's edge.
- **Domain:** `bh-stack.com`, already on Cloudflare. The relay hostname is `nexus.bh-stack.com`.

### Nexus webhook facts the design relies on (verify these)
- Webhooks are registered in the web dashboard at **frc.nexus/api**, not through an API. Registration may be per event, so plan to re-register before each competition.
- Requests carry a **`Nexus-Token`** header for verification.
- Each payload is a **full EventStatus snapshot**, the same shape as `GET /api/v1/event/{eventKey}`. That means a dropped delivery self-heals on the next one.
- **No retries.** Hooks that keep returning non-200 responses are **auto-disabled**, so the receiver must return 200 fast.
- **Delivery order isn't guaranteed.** Gate writes on `dataAsOfTime`.
- Test with demo events (`demo` followed by a number, created on frc.nexus). Demo events still need a real API key.

---

## Architecture

```
frc.nexus ──POST /nexus/webhook──▶ Worker @ nexus.bh-stack.com (custom domain)
                                        │  verifies Nexus-Token, routes by eventKey
                                        ▼
                               EventRoom Durable Object (one per event key)
                               • newest snapshot persisted in ctx.storage
                               • fans out to hibernating WebSockets
                                        │ wss://…/events/{key}/ws
pit display (QWebSocket) ◀──────────────┘
   fallback: GET /events/{key}  →  then frc.nexus/api/v1/event/{key} directly
```

**Design decisions**
- **Durable Object over KV:** KV is eventually consistent and can't hold connections. A DO is strongly consistent, single-threaded, and owns its sockets.
- **WebSocket Hibernation over SSE:** SSE keeps the DO awake and billed. Hibernating sockets cost nothing while idle.
- **Heartbeats via `setWebSocketAutoResponse("ping" → "pong")`:** answered by the runtime without waking the DO. Never use `setInterval` inside the DO.
- **Snapshot in `ctx.storage`:** hibernation wipes in-memory fields, but storage survives hibernation, eviction, and redeploys.
- **No lock needed in `ingest()`:** DO input gates prevent interleaving across storage awaits.
- **Auth:** `Nexus-Token` protects the webhook; a bearer `CLIENT_TOKEN` protects the read and WebSocket routes. **No Cloudflare Access on this hostname**, because Nexus can't pass an Access login.
- **Cost:** a few hundred webhooks per event day, and SQLite-backed DOs are on the Workers Free plan. Expect $0, but check usage graphs after the first event.

---

## Phase 1 — The Worker

### Scaffold
```bash
npm create cloudflare@latest -- nexus-relay
# Hello World example → Worker + Durable Objects → TypeScript → don't deploy yet
cd nexus-relay
npx wrangler login
```

### `nexus-relay/wrangler.jsonc`
```jsonc
{
  "name": "nexus-relay",
  "main": "src/index.ts",
  // Pins runtime behavior. Bump it deliberately.
  "compatibility_date": "2026-09-01",
  // A custom domain gets its DNS record and certificate created automatically.
  // Deploy FAILS if a DNS record for this name already exists: delete it first.
  "routes": [{ "pattern": "nexus.bh-stack.com", "custom_domain": true }],
  "durable_objects": {
    "bindings": [{ "name": "EVENT_ROOM", "class_name": "EventRoom" }]
  },
  // Append-only. Never edit v1 after deploying; add v2, v3, etc.
  // (Cloudflare also offers a newer declarative `exports` field. Migrations still work.)
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["EventRoom"] }],
  "observability": { "enabled": true }
}
```

### `nexus-relay/src/index.ts`
```ts
import { DurableObject } from "cloudflare:workers";

export interface Env {
  EVENT_ROOM: DurableObjectNamespace<EventRoom>;
  NEXUS_WEBHOOK_TOKEN: string; // wrangler secret put — never in wrangler.jsonc
  CLIENT_TOKEN: string;
}

type Snapshot = { eventKey?: string; dataAsOfTime?: number; [k: string]: unknown };

/** Constant-time compare. Workers' timingSafeEqual throws on length mismatch. */
function safeEqual(a: string | null, b: string): boolean {
  if (a === null) return false;
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  return ab.byteLength === bb.byteLength && crypto.subtle.timingSafeEqual(ab, bb);
}

const unauthorized = () => new Response("unauthorized", { status: 401 });

/**
 * One instance per event key: holds the newest snapshot and all subscriber sockets.
 * Single-threaded with input gates, so ingest() can't interleave with itself.
 */
export class EventRoom extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    // "ping" gets "pong" from the runtime without waking this object, so idle sockets are free.
    this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  /** RPC from the Worker. Returns false for stale or duplicate deliveries. */
  async ingest(snapshot: Snapshot): Promise<boolean> {
    const current = await this.ctx.storage.get<Snapshot>("snapshot");
    // Nexus doesn't guarantee ordering, so only accept newer data.
    if (current && (current.dataAsOfTime ?? 0) >= (snapshot.dataAsOfTime ?? 0)) return false;

    await this.ctx.storage.put("snapshot", snapshot);
    const msg = JSON.stringify(snapshot);
    for (const ws of this.ctx.getWebSockets()) {
      try { ws.send(msg); } catch { /* socket mid-close; runtime cleans up */ }
    }
    return true;
  }

  async latest(): Promise<Snapshot | undefined> {
    return this.ctx.storage.get<Snapshot>("snapshot");
  }

  /** Only reached for WebSocket upgrades. The Worker has already authenticated. */
  async fetch(_request: Request): Promise<Response> {
    const [client, server] = Object.values(new WebSocketPair());
    // acceptWebSocket (not server.accept()) is what enables hibernation.
    this.ctx.acceptWebSocket(server);
    const snap = await this.latest();
    if (snap) server.send(JSON.stringify(snap)); // a reconnecting display is never blank
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(_ws: WebSocket, _msg: string | ArrayBuffer): Promise<void> {
    // Clients only send "ping", which auto-response handles.
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    // Complete the close handshake. Reserved codes (1005/1006) throw if echoed.
    try { ws.close(code, reason); } catch { ws.close(1000, "closing"); }
  }
}

async function handleWebhook(request: Request, env: Env, url: URL): Promise<Response> {
  if (!safeEqual(request.headers.get("Nexus-Token"), env.NEXUS_WEBHOOK_TOKEN)) {
    return unauthorized();
  }

  // From here on, ALWAYS return 200. Nexus auto-disables hooks that keep failing.
  let payload: Snapshot;
  try {
    payload = await request.json<Snapshot>();
  } catch {
    console.error("unparseable webhook body");
    return Response.json({ ok: false });
  }

  const eventKey = (payload.eventKey ?? url.searchParams.get("event") ?? "").toLowerCase();
  if (!eventKey) {
    console.error("webhook missing eventKey; register the URL with ?event=<key>");
    return Response.json({ ok: false });
  }

  try {
    // Awaited, not waitUntil: it takes milliseconds, and errors stay attached to the request log.
    const accepted = await env.EVENT_ROOM.getByName(eventKey).ingest(payload);
    console.log(JSON.stringify({ eventKey, accepted, asOf: payload.dataAsOfTime }));
  } catch (err) {
    console.error("ingest failed", err);
  }
  return Response.json({ ok: true });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const [root, key, sub] = url.pathname.split("/").filter(Boolean);

    if (url.pathname === "/healthz") return Response.json({ ok: true });

    if (request.method === "POST" && url.pathname === "/nexus/webhook") {
      return handleWebhook(request, env, url);
    }

    if (request.method === "GET" && root === "events" && key) {
      if (!safeEqual(request.headers.get("Authorization"), `Bearer ${env.CLIENT_TOKEN}`)) {
        return unauthorized();
      }
      const room = env.EVENT_ROOM.getByName(key.toLowerCase());

      if (sub === "ws") {
        if (request.headers.get("Upgrade") !== "websocket") {
          return new Response("expected websocket", { status: 426 });
        }
        return room.fetch(request);
      }
      if (!sub) {
        const snap = await room.latest();
        return snap ? Response.json(snap) : new Response("no data yet", { status: 404 });
      }
    }

    return new Response("not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
```

### Secrets and deploy
```bash
npx wrangler types                            # typed bindings; rerun after config changes
openssl rand -hex 32                          # → CLIENT_TOKEN (the owner saves it for the pit display .env)
npx wrangler secret put CLIENT_TOKEN
npx wrangler secret put NEXUS_WEBHOOK_TOKEN   # placeholder until Phase 2
npx wrangler deploy
```
For local `wrangler dev`, put the same keys in `nexus-relay/.dev.vars` and add it to `.gitignore`.

### Phase 1 acceptance tests
```bash
curl -s https://nexus.bh-stack.com/healthz                     # {"ok":true}

curl -s -X POST https://nexus.bh-stack.com/nexus/webhook \
  -H "Nexus-Token: <token>" -H "Content-Type: application/json" \
  -d '{"eventKey":"test","dataAsOfTime":1,"nowQueuing":"Qualification 1","matches":[]}'

curl -s -H "Authorization: Bearer <client token>" https://nexus.bh-stack.com/events/test   # returns the snapshot
curl -s https://nexus.bh-stack.com/events/test                                              # 401

# Should receive the snapshot immediately; send "ping" and get "pong"
websocat -H "Authorization: Bearer <client token>" wss://nexus.bh-stack.com/events/test/ws

# Stale delivery (dataAsOfTime lower than stored) → log shows accepted:false
```
Watch everything with `npx wrangler tail`. Stored snapshots are also visible in the dashboard's Durable Objects Data Studio.

---

## Phase 2 — Wire up Nexus
1. **Owner:** on frc.nexus/api, register `https://nexus.bh-stack.com/nexus/webhook` for a demo event.
2. **Owner:** run `npx wrangler secret put NEXUS_WEBHOOK_TOKEN` with the token Nexus provides. This takes effect immediately, with no redeploy.
3. Advance matches on the demo event and confirm `accepted:true` lines in `wrangler tail`.
4. Check the real payload. If it doesn't include `eventKey`, re-register the URL with `?event=<key>`.
5. **Cloudflare check:** if Bot Fight Mode is on for `bh-stack.com`, look in Security → Events for challenged POSTs to `/nexus/webhook`. The free plan can't exempt a single path, so if Nexus is being blocked, disable Bot Fight Mode for the zone.

---

## Phase 3 — Pit display client

`QtWebSockets` ships in the standard PyQt6 wheel. Using Qt's native networking keeps this on the event loop, so there are no QThreads.

### `pit_display/nexus_client.py`
```python
from __future__ import annotations

import json

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket, QNetworkRequest
from PyQt6.QtWebSockets import QWebSocket


class NexusRelayClient(QObject):
    """WebSocket subscriber to the Cloudflare relay with heartbeat, watchdog, and backoff."""

    snapshotReceived = pyqtSignal(dict)
    connectionChanged = pyqtSignal(bool)

    PING_MS = 30_000      # well under Cloudflare's ~100s idle cutoff
    STALE_MS = 75_000     # two missed pongs = dead socket the OS hasn't noticed (venue wifi)
    MAX_BACKOFF_MS = 30_000

    def __init__(self, base_url: str, event_key: str, token: str, parent: QObject | None = None):
        super().__init__(parent)
        ws_base = base_url.rstrip("/").replace("https://", "wss://", 1)
        self._url = QUrl(f"{ws_base}/events/{event_key.lower()}/ws")
        self._token = token.encode()
        self._backoff_ms = 1000

        self._ws = QWebSocket(parent=self)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text)
        # errorOccurred is the Qt 6.5+ name. Some failures emit it without
        # disconnected, so it also schedules a reconnect (guarded against doubles).
        self._ws.errorOccurred.connect(self._on_error)

        self._ping = QTimer(self, interval=self.PING_MS)
        self._ping.timeout.connect(lambda: self._ws.sendTextMessage("ping"))
        self._watchdog = QTimer(self, interval=self.STALE_MS, singleShot=True)
        self._watchdog.timeout.connect(self._ws.abort)  # abort → disconnected → reconnect
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self.start)

    def start(self) -> None:
        req = QNetworkRequest(self._url)
        # open(QNetworkRequest) is how custom headers get onto the upgrade request.
        req.setRawHeader(b"Authorization", b"Bearer " + self._token)
        self._ws.open(req)

    def _on_connected(self) -> None:
        self._backoff_ms = 1000
        self._ping.start()
        self._watchdog.start()
        self.connectionChanged.emit(True)

    def _on_text(self, msg: str) -> None:
        self._watchdog.start()  # any traffic, including pong, counts as alive
        if msg == "pong":
            return
        try:
            self.snapshotReceived.emit(json.loads(msg))
        except json.JSONDecodeError:
            pass

    def _on_disconnected(self) -> None:
        self._ping.stop()
        self._watchdog.stop()
        self.connectionChanged.emit(False)
        self._schedule_reconnect()

    def _on_error(self, _err: QAbstractSocket.SocketError) -> None:
        if self._ws.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._retry.isActive():
            return
        self._retry.start(self._backoff_ms)
        self._backoff_ms = min(self._backoff_ms * 2, self.MAX_BACKOFF_MS)
```

### Wiring (adapt to the existing window architecture)
```python
client = NexusRelayClient(
    "https://nexus.bh-stack.com",
    cfg.event_key,
    cfg.relay_token,   # from .env or keyring, never hardcoded
    parent=self,
)
client.snapshotReceived.connect(self.queue_panel.apply_snapshot)
client.connectionChanged.connect(self.status_badge.set_live)
client.start()
```
The LIVE/RECONNECTING badge should follow `BREAKAWAY_BRAND.md` (Breakaway Red `#C82027`, Carbon `#181416`, Chakra Petch / Roboto).

---

## Phase 4 — Fallback polling
- Trigger it when `connectionChanged(False)` has lasted about 60s. Stop it when `connectionChanged(True)` fires.
- Tier 1: `GET https://nexus.bh-stack.com/events/{key}` with the bearer token.
- Tier 2, if tier 1 fails: `GET https://frc.nexus/api/v1/event/{key}` with the `Nexus-Api-Key` header.
- Both return the same snapshot shape, so feed `apply_snapshot` unchanged. Poll every ~15s and use `QNetworkAccessManager`, not `requests`.
- Every path needs venue internet. That's a known limitation; the team carries a hotspot.

---

## Gotchas checklist
- [ ] A custom domain won't attach while a DNS record for `nexus.bh-stack.com` exists.
- [ ] Migrations are append-only, so never edit a deployed tag.
- [ ] No in-memory state in `EventRoom`, because hibernation wipes it.
- [ ] No timers inside the DO; heartbeats come from the client plus auto-response.
- [ ] Webhook handler returns 200 on everything except a bad token.
- [ ] No Cloudflare Access on the hostname; check Bot Fight Mode.
- [ ] Secrets only in `wrangler secret`, `.dev.vars`, or the pit display's `.env`/keyring; all gitignored.
- [ ] Re-register the Nexus webhook for each new event.

---

## Phase 5 — Add this to the pit display's `CLAUDE.md`
```markdown
## Live event data (Nexus)
- Source: Cloudflare Worker relay at https://nexus.bh-stack.com (code in nexus-relay/).
- Client: pit_display/nexus_client.py (NexusRelayClient, WebSocket + polling fallback).
- Design + rationale: NEXUS_RELAY_HANDOFF.md. No home-lab dependency, by design.
- Before each competition: register the webhook at frc.nexus/api for the event key.
```
