/**
 * nexus-relay — Breakaway's one static address for everything frc.nexus.
 *
 *   frc.nexus ──POST /nexus/webhook──▶ Worker ──RPC──▶ EventRoom (one per event key)
 *                                                    • newest snapshot in ctx.storage
 *                                                    • pulls Nexus itself when pushes go quiet
 *                                                    • fans out to hibernating WebSockets
 *   pit display ◀──wss://…/api/v1/event/{key}/ws──────┘
 *   pit display ──GET /api/v1/…──▶ Worker ──(edge cache)──▶ frc.nexus/api/v1/…
 *
 * **The relay holds the Nexus API key; a pit machine holds one relay token.**
 * `/api/v1/` mirrors Nexus's own paths exactly, so the pit display's client is
 * the same code pointed at a different base URL with a different header.
 *
 * **Every webhook POST gets a 200.** Nexus does not retry and disables a hook
 * that keeps failing, without telling anyone — so a wrong token, a body we
 * cannot read and a stale snapshot are all logged, counted and answered 200.
 * Nothing is ingested without the right token.
 *
 * **Nothing lives in class fields that matters.** Hibernation throws the
 * object away between events; the snapshot and the counters are in storage.
 */
import { DurableObject } from "cloudflare:workers";

export interface Env {
  EVENT_ROOM: DurableObjectNamespace<EventRoom>;
  NEXUS_API: string;             // var — upstream base, no trailing slash
  CLIENT_TOKEN: string;          // secret — what a pit display presents
  NEXUS_WEBHOOK_TOKEN: string;   // secret — what Nexus presents to us
  NEXUS_API_KEY: string;         // secret — what we present to Nexus
}

type Match = { label?: string; [k: string]: unknown };
type Snapshot = {
  eventKey?: string;
  dataAsOfTime?: number;
  matches?: Match[];
  [k: string]: unknown;
};
type MatchPush = { eventKey?: string; dataAsOfTime?: number; match?: Match };

/** What the relay knows about itself for one event — the pit's telemetry. */
type Stats = {
  eventKey: string;
  webhooks: number;          // every POST for this event that carried the right token
  accepted: number;          // …that moved the snapshot forward
  stale: number;             // …that were older than what was held
  refused: number;           // POSTs for this event with a wrong or missing token
  lastWebhookAt: number;     // ms, 0 = never
  lastRefusedAt: number;
  pulls: number;             // relay → Nexus fetches of the live snapshot
  pullFailures: number;
  lastPullAt: number;
  lastPullResult: string;
  lastConfirmedAt: number;   // last time anything proved the snapshot current
  lastSource: string;        // "webhook" | "match" | "pull"
  dataAsOfTime: number;      // of the held snapshot
  subscribers: number;       // filled in on read, never stored
};

/**
 * Everything a room holds, under ONE key. The storage API bills a row per key
 * written, and the free plan allows 100,000 rows a day across every event in
 * the season — the relay takes Nexus's all-events feed, so a busy Saturday is
 * tens of thousands of webhooks. Snapshot and counters in one value means one
 * write per webhook instead of two.
 */
const ROOM_KEY = "room";
type Room = { snap: Snapshot | null; stats: Stats };

/** While a display is subscribed, the snapshot is never older than this. */
const PULL_MS = 30_000;
/** A plain GET accepts a snapshot this old before pulling a fresh one. */
const GET_MAX_AGE_MS = 20_000;

const SUB_RESOURCES: Record<string, number> = {
  // Cache seconds at the edge, per sub-resource. These change on an hour
  // scale (inspection on minutes, alliances only on Saturday).
  pits: 300,
  map: 300,
  teams: 300,
  inspection: 60,
  alliances: 30,
};

function emptyStats(eventKey: string): Stats {
  return {
    eventKey, webhooks: 0, accepted: 0, stale: 0, refused: 0,
    lastWebhookAt: 0, lastRefusedAt: 0, pulls: 0, pullFailures: 0,
    lastPullAt: 0, lastPullResult: "", lastConfirmedAt: 0, lastSource: "",
    dataAsOfTime: 0, subscribers: 0,
  };
}

/** Constant-time compare. `timingSafeEqual` throws on a length mismatch. */
function safeEqual(a: string | null, b: string | undefined): boolean {
  if (a === null || !b) return false;
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  return ab.byteLength === bb.byteLength && crypto.subtle.timingSafeEqual(ab, bb);
}

/**
 * `NEXUS_WEBHOOK_TOKEN` may hold several tokens, comma-separated: each webhook
 * registered at frc.nexus/api (all events, one event, one team) can carry its
 * own, and all of them post to this one URL. Every entry is compared in
 * constant time and none short-circuits.
 */
function webhookTokenOk(got: string | null, configured: string | undefined): boolean {
  let ok = false;
  for (const token of (configured ?? "").split(",").map((t) => t.trim()).filter(Boolean)) {
    if (safeEqual(got, token)) ok = true;
  }
  return ok;
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", ...headers },
  });
}

/**
 * A Nexus GET, with Nexus's auth statuses translated so they can never be
 * mistaken for ours. **A 401 from the relay must only ever mean "the pit's
 * relay token is wrong"** — that is what the pit display tells its operator.
 * But Nexus answers 401 for *an invalid event key* too (measured 2026-09-23:
 * `"'x' is an invalid event key."`), and 401/403 for a bad relay-side key.
 * So: invalid event key → 404 (no such event, stop asking), any other Nexus
 * 401/403 → 502 (the relay's own key is the problem, not the pit's).
 */
async function upstream(env: Env, path: string): Promise<Response> {
  if (!env.NEXUS_API_KEY) {
    return json({ error: "The relay has no Nexus API key. Run `wrangler secret put NEXUS_API_KEY`." }, 503);
  }
  let res: Response;
  try {
    res = await fetch(`${env.NEXUS_API}${path}`, {
      headers: {
        "Nexus-Api-Key": env.NEXUS_API_KEY,
        Accept: "application/json",
        "User-Agent": "breakaway-nexus-relay",
      },
    });
  } catch (err) {
    return json({ error: `frc.nexus unreachable from the relay: ${String(err)}` }, 502);
  }
  if (res.status !== 401 && res.status !== 403) return res;
  const text = (await res.text()).slice(0, 300);
  if (/invalid event key/i.test(text)) return json({ error: text }, 404);
  return json({ error: `frc.nexus refused the relay's API key (HTTP ${res.status}): ${text}` }, 502);
}

// ── The Durable Object ────────────────────────────────────────────────────────

/**
 * One instance per event key. Single-threaded with input gates, so a
 * read-compare-write on `dataAsOfTime` with only storage awaits inside it
 * cannot interleave with another. The upstream `fetch` in `pull()` does open
 * the gate, which is why the compare happens *after* it, in `offer()`.
 */
export class EventRoom extends DurableObject<Env> {
  /** In-flight pull, so a burst of GETs costs one upstream fetch. Losing it
   *  to hibernation costs at most one duplicate fetch; nothing depends on it. */
  private pulling: Promise<void> | null = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    // Answered by the runtime without waking this object: idle sockets are free.
    this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  // ── State ──

  private async load(eventKey: string): Promise<Room> {
    const room = await this.ctx.storage.get<Room>(ROOM_KEY);
    const stats = { ...emptyStats(eventKey), ...(room?.stats ?? {}) };
    stats.eventKey = room?.stats?.eventKey || eventKey;
    return { snap: room?.snap ?? null, stats };
  }

  /**
   * Persist the room. Never throws: a write that fails — the daily free-plan
   * quota is the realistic cause — must not stop a snapshot reaching the pits
   * that are connected right now, so callers broadcast whatever happens here.
   */
  private async save(room: Room): Promise<void> {
    try {
      await this.ctx.storage.put(ROOM_KEY, { snap: room.snap, stats: { ...room.stats, subscribers: 0 } });
    } catch (err) {
      console.error(JSON.stringify({ storage: "write failed", eventKey: room.stats.eventKey, err: String(err) }));
    }
  }

  private publicStats(stats: Stats): Stats {
    return { ...stats, subscribers: this.ctx.getWebSockets().length };
  }

  private broadcast(message: unknown): void {
    const text = JSON.stringify(message);
    for (const ws of this.ctx.getWebSockets()) {
      try { ws.send(text); } catch { /* mid-close; the runtime cleans up */ }
    }
  }

  /** Adopt `snap` if it is newer than what is held. The one door for all three sources. */
  private async offer(room: Room, snap: Snapshot, source: string): Promise<boolean> {
    const { stats } = room;
    if (room.snap && (room.snap.dataAsOfTime ?? 0) >= (snap.dataAsOfTime ?? 0)) {
      if (source !== "pull") stats.stale += 1;
      this.broadcast({ type: "relay", relay: this.publicStats(stats) });
      await this.save(room);
      return false;
    }
    room.snap = snap;
    stats.accepted += source === "pull" ? 0 : 1;
    stats.lastSource = source;
    stats.dataAsOfTime = snap.dataAsOfTime ?? 0;
    // Pits first, disk second: see save().
    this.broadcast({ type: "status", data: snap, relay: this.publicStats(stats) });
    await this.save(room);
    return true;
  }

  // ── RPC from the Worker ──

  async ingestEvent(eventKey: string, snap: Snapshot): Promise<boolean> {
    const room = await this.load(eventKey);
    const now = Date.now();
    room.stats.webhooks += 1;
    room.stats.lastWebhookAt = now;
    room.stats.lastConfirmedAt = now;
    return this.offer(room, snap, "webhook");
  }

  /**
   * The team webhook: one match, not a snapshot. Folded into the held
   * snapshot — storing it as-is would hand every display a body with no
   * schedule in it.
   */
  async ingestMatch(eventKey: string, push: MatchPush): Promise<boolean> {
    const room = await this.load(eventKey);
    room.stats.webhooks += 1;
    room.stats.lastWebhookAt = Date.now();
    const held = room.snap;
    const match = push.match;
    if (!held || !match?.label) {
      // Nothing to fold it into: fetch the whole schedule instead, now.
      await this.save(room);
      await this.ctx.storage.setAlarm(Date.now());
      return false;
    }
    const matches = [...(held.matches ?? [])];
    const i = matches.findIndex((m) => m.label === match.label);
    if (i >= 0) matches[i] = match; else matches.push(match);
    return this.offer(room, { ...held, matches, dataAsOfTime: push.dataAsOfTime ?? 0 }, "match");
  }

  async noteRefused(eventKey: string): Promise<void> {
    const room = await this.load(eventKey);
    room.stats.refused += 1;
    room.stats.lastRefusedAt = Date.now();
    this.broadcast({ type: "relay", relay: this.publicStats(room.stats) });
    await this.save(room);
  }

  /** The held snapshot, pulled fresh first when it is older than `maxAgeMs`. */
  async status(eventKey: string, maxAgeMs = GET_MAX_AGE_MS): Promise<{ snap: Snapshot | null; error: string; code: number }> {
    let room = await this.load(eventKey);
    if (!room.snap || Date.now() - room.stats.lastConfirmedAt > maxAgeMs) {
      await this.pull(eventKey);
      room = await this.load(eventKey);
    }
    if (room.snap) return { snap: room.snap, error: "", code: 200 };
    const result = room.stats.lastPullResult;
    const code = /^HTTP (\d{3})/.exec(result)?.[1];
    return { snap: null, error: result || "no data yet", code: code ? Number(code) : 502 };
  }

  async stats(eventKey: string): Promise<Stats> {
    return this.publicStats((await this.load(eventKey)).stats);
  }

  // ── Pulling from Nexus ──

  private async pull(eventKey: string): Promise<void> {
    if (!this.pulling) {
      this.pulling = this.doPull(eventKey).finally(() => { this.pulling = null; });
    }
    return this.pulling;
  }

  private async doPull(eventKey: string): Promise<void> {
    const res = await upstream(this.env, `/event/${encodeURIComponent(eventKey)}`);
    let body: Snapshot | null = null;
    let result = `HTTP ${res.status}`;
    if (res.ok) {
      try { body = (await res.json()) as Snapshot; result = "ok"; }
      catch { result = "HTTP 200 but not JSON"; }
    } else {
      const text = (await res.text()).slice(0, 200);
      result = `HTTP ${res.status} ${text}`.trim();
    }
    // Re-read after the fetch: a webhook may have landed while it was out.
    const room = await this.load(eventKey);
    const { stats } = room;
    stats.pulls += 1;
    stats.lastPullAt = Date.now();
    stats.lastPullResult = result;
    if (!body) {
      stats.pullFailures += 1;
      this.broadcast({ type: "relay", relay: this.publicStats(stats) });
      await this.save(room);
      return;
    }
    stats.lastConfirmedAt = Date.now();
    await this.offer(room, body, "pull");
  }

  /**
   * The only timer in here, and it exists only while somebody is listening.
   * Webhooks fire on *change*; a quiet event (lunch, a field fault) sends
   * nothing, and a broken registration sends nothing forever. So while a
   * display is subscribed, anything not confirmed in the last 30s is pulled.
   * With no subscriber the alarm is not re-armed and the object sleeps.
   */
  async alarm(): Promise<void> {
    if (this.ctx.getWebSockets().length === 0) return;
    const { stats } = await this.load("");
    if (stats.eventKey && Date.now() - stats.lastConfirmedAt >= PULL_MS - 1_000) {
      await this.pull(stats.eventKey);
    }
    await this.ctx.storage.setAlarm(Date.now() + PULL_MS);
  }

  // ── WebSockets ──

  /** Only reached for upgrades; the Worker has already authenticated. */
  async fetch(request: Request): Promise<Response> {
    const eventKey = request.headers.get("X-Relay-Event") ?? "";
    const stored = await this.ctx.storage.get<Room>(ROOM_KEY);
    const room = await this.load(eventKey);
    const { stats, snap } = room;
    // A room first opened by a pit rather than a webhook has to learn its
    // own name, or the alarm cannot pull for it.
    if (!stored?.stats?.eventKey && eventKey) await this.save(room);

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    // acceptWebSocket (not server.accept()) is what makes it hibernatable.
    this.ctx.acceptWebSocket(server);

    // A reconnecting display is never blank: it gets the held state at once.
    server.send(JSON.stringify(snap
      ? { type: "status", data: snap, relay: this.publicStats(stats) }
      : { type: "relay", relay: this.publicStats(stats) }));

    const stale = !snap || Date.now() - stats.lastConfirmedAt > PULL_MS;
    const armed = await this.ctx.storage.getAlarm();
    if (stale) await this.ctx.storage.setAlarm(Date.now());
    else if (armed === null) await this.ctx.storage.setAlarm(Date.now() + PULL_MS);

    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    // "ping" never gets here — the auto-response answers it. "refresh" is the
    // operator's Refresh button: pull now, and always answer the one who asked.
    if (message !== "refresh") return;
    const { stats } = await this.load("");
    if (stats.eventKey) await this.pull(stats.eventKey);
    const fresh = await this.load("");
    try {
      ws.send(JSON.stringify(fresh.snap
        ? { type: "status", data: fresh.snap, relay: this.publicStats(fresh.stats) }
        : { type: "relay", relay: this.publicStats(fresh.stats) }));
    } catch { /* gone */ }
  }

  async webSocketClose(ws: WebSocket, code: number, reason: string): Promise<void> {
    // Complete the close handshake. 1005/1006 are reserved and throw if echoed.
    try { ws.close(code, reason); } catch { try { ws.close(1000, "closing"); } catch { /* already closed */ } }
  }

  async webSocketError(ws: WebSocket): Promise<void> {
    try { ws.close(1011, "error"); } catch { /* already closed */ }
  }
}

// ── The Worker ────────────────────────────────────────────────────────────────

function room(env: Env, eventKey: string): DurableObjectStub<EventRoom> {
  return env.EVENT_ROOM.get(env.EVENT_ROOM.idFromName(eventKey));
}

function classify(body: unknown): "event" | "match" | null {
  if (!body || typeof body !== "object" || !("eventKey" in body)) return null;
  const b = body as Record<string, unknown>;
  if (b.match && typeof b.match === "object") return "match";
  if (Array.isArray(b.matches)) return "event";
  return null;
}

async function handleWebhook(request: Request, env: Env, url: URL): Promise<Response> {
  // From here on, ALWAYS 200. Nexus disables a hook that keeps failing, and a
  // disabled hook is invisible until somebody logs in to frc.nexus to look.
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    // The empty POST Nexus may send when a URL is registered lands here too.
    console.log(JSON.stringify({ webhook: "unparseable" }));
    return json({ ok: false, reason: "not json" });
  }
  const kind = classify(body);
  const b = (body ?? {}) as Snapshot;
  const eventKey = String(b.eventKey ?? url.searchParams.get("event") ?? "").trim().toLowerCase();
  if (!kind || !eventKey) {
    console.log(JSON.stringify({ webhook: "unrecognised", keys: Object.keys(b).slice(0, 8) }));
    return json({ ok: false, reason: "not an EventStatus or MatchStatus" });
  }

  const stub = room(env, eventKey);
  if (!webhookTokenOk(request.headers.get("Nexus-Token"), env.NEXUS_WEBHOOK_TOKEN)) {
    console.warn(JSON.stringify({ webhook: "refused", eventKey, kind }));
    try { await stub.noteRefused(eventKey); } catch { /* counting is best effort */ }
    return json({ ok: false, reason: "token" });
  }

  try {
    // Awaited rather than waitUntil: it takes milliseconds, and an error stays
    // attached to this request in the log.
    const accepted = kind === "event"
      ? await stub.ingestEvent(eventKey, b)
      : await stub.ingestMatch(eventKey, b as MatchPush);
    console.log(JSON.stringify({ webhook: kind, eventKey, accepted, asOf: b.dataAsOfTime }));
  } catch (err) {
    console.error("ingest failed", String(err));
  }
  return json({ ok: true });
}

/** A Nexus GET, served through the edge cache. 200s and 404s are cached. */
async function proxied(request: Request, env: Env, ctx: ExecutionContext, path: string, ttl: number): Promise<Response> {
  const cache = caches.default;
  // The key carries no auth — every authorised caller shares one entry.
  const key = new Request(new URL(`/__cache/api/v1${path}`, request.url).toString());
  const hit = await cache.match(key);
  if (hit) {
    const out = new Response(hit.body, hit);
    out.headers.set("x-relay-cache", "hit");
    return out;
  }
  const res = await upstream(env, path);
  const body = await res.text();
  const out = new Response(body, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") ?? "application/json",
      "cache-control": `public, max-age=${ttl}`,
      "x-relay-cache": "miss",
    },
  });
  if (res.status === 200 || res.status === 404) ctx.waitUntil(cache.put(key, out.clone()));
  return out;
}

function portal(url: URL): Response {
  const hook = `${url.origin}/nexus/webhook`;
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Breakaway Nexus Relay</title>
<style>
:root{--bg:#FAF9F8;--ink:#181416;--muted:#6A6462;--rule:#E5E2E1;--card:#fff;--dot:#2E8B7F}
@media (prefers-color-scheme:dark){:root{--bg:#181416;--ink:#fff;--muted:#A6A19E;--rule:#443F3D;--card:#221d1f}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Roboto,system-ui,sans-serif}
main{max-width:640px;margin:0 auto;padding:48px 16px}
h1{font:700 30px/1.1 "Chakra Petch",system-ui,sans-serif;margin:0 0 8px}
.eyebrow{font:600 12px/1.4 "Chakra Petch",system-ui,sans-serif;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.card{background:var(--card);border:1.5px solid var(--rule);border-radius:14px;padding:16px 20px;margin:20px 0}
code{font:13px/1.5 ui-monospace,"JetBrains Mono",monospace;word-break:break-all}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--dot);margin-right:8px}
p{color:var(--muted)}
</style></head><body><main>
<div class="eyebrow">Breakaway 3937 · Pit systems</div>
<h1>Nexus relay</h1>
<p><span class="dot"></span>Running. Event data from frc.nexus.</p>
<div class="card"><div class="eyebrow">Register this at frc.nexus/api</div>
<p>Once, for the live-event webhook and for the team webhook. It never changes.</p>
<code>${hook}</code></div>
<p>Pit displays connect with their relay token. Nothing here is readable without one.</p>
</main></body></html>`;
  return new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/healthz") {
      return json({ ok: true, apiKey: Boolean(env.NEXUS_API_KEY), webhookToken: Boolean(env.NEXUS_WEBHOOK_TOKEN) });
    }
    if (request.method === "GET" && path === "/") return portal(url);
    if (request.method === "POST" && path === "/nexus/webhook") return handleWebhook(request, env, url);

    if (!path.startsWith("/api/v1/")) return json({ error: "not found" }, 404);
    if (request.method !== "GET") return json({ error: "method not allowed" }, 405);
    if (!safeEqual(request.headers.get("Authorization"), `Bearer ${env.CLIENT_TOKEN}`)) {
      return json({ error: "The relay refused this token." }, 401);
    }

    // /api/v1/events · /api/v1/event/{key}[/{sub}]
    const parts = path.slice("/api/v1/".length).split("/").map(decodeURIComponent);
    if (parts.length === 1 && parts[0] === "events") {
      return proxied(request, env, ctx, "/events", 300);
    }
    if (parts[0] !== "event" || !parts[1] || parts.length > 3) {
      return json({ error: "not found" }, 404);
    }
    const eventKey = parts[1].trim().toLowerCase();
    const sub = parts[2];
    const stub = room(env, eventKey);

    if (!sub) {
      const { snap, error, code } = await stub.status(eventKey);
      return snap ? json(snap) : json({ error }, code);
    }
    if (sub === "ws") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return json({ error: "expected a websocket upgrade" }, 426);
      }
      const fwd = new Request(request);
      fwd.headers.set("X-Relay-Event", eventKey);
      return stub.fetch(fwd);
    }
    if (sub === "relay") return json(await stub.stats(eventKey));
    if (sub in SUB_RESOURCES) {
      return proxied(request, env, ctx, `/event/${encodeURIComponent(eventKey)}/${sub}`, SUB_RESOURCES[sub]);
    }
    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;
