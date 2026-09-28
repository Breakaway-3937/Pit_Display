/**
 * pit-sync-hub — where every pit machine and the home server meet.
 *
 *   pit machine ──POST /v1/push────▶ Worker ──RPC──▶ SyncHub ("team")
 *   pit machine ◀─GET  /v1/changes──┘                • orders every change (seq)
 *   pit machine ──PUT  /v1/blob/{sha}──▶ R2           • holds each row's newest state
 *   home agent  ◀─GET  /v1/changes, /v1/blobs         • tombstones are kept forever
 *
 * **The home SQL Server is the master; this is the meeting point.** A pit
 * machine at an event can't reach the house, and the house can't be reached
 * from the internet (no inbound ports, by design). Both can reach Cloudflare.
 * So the hub orders changes and keeps the current picture, and the home agent
 * mirrors all of it, archives every file, and is the one writer that may
 * overrule a conflict (`force`). If this Durable Object were ever lost, the
 * home agent rebuilds it with forced pushes; the new `epoch` tells every pit
 * to re-read from zero.
 *
 * **A row is (tbl, uid) → the newest accepted state.** `seq` is a single
 * counter across all rows, so "everything since N" is one indexed range and
 * a machine's cursor is one integer. A pit sends each change with `base`,
 * the seq it last saw for that row; a newer write from a *different*
 * machine wins and the pit is handed the current state to apply. That's
 * row-level first-writer-wins on the hub's clock, never on a pit's clock.
 *
 * **Nothing lives in class fields that matters**, and there are no timers:
 * every request reads and writes storage. The SQL API is synchronous, so a
 * push with no `await` inside cannot interleave with another.
 */
import { DurableObject } from "cloudflare:workers";

export interface Env {
  HUB: DurableObjectNamespace<SyncHub>;
  BLOBS: R2Bucket;
  PIT_TOKEN: string;   // secret — what every pit machine presents
  HOME_TOKEN: string;  // secret — what the home agent presents (may force, ack, prune)
}

type Role = "pit" | "home";

type Change = {
  tbl: string;
  uid: string;
  op: "upsert" | "delete";
  base?: number;
  data?: unknown;
};

type PushResult = {
  tbl: string;
  uid: string;
  status: "ok" | "conflict" | "invalid";
  seq?: number;
  current?: StoredRow;
  reason?: string;
};

type StoredRow = {
  seq: number;
  tbl: string;
  uid: string;
  op: string;
  data: unknown;
  origin: string;
  at: number;
};

type BlobInfo = {
  sha: string;
  bytes: number;
  kind: string;
  name: string;
  origin: string;
  created: number;
  home_acked: number;
};

/** A row's `data` is JSON; this keeps one runaway row from filling storage. */
const MAX_ROW_BYTES = 256 * 1024;
const MAX_CHANGES_PER_PUSH = 500;
const MAX_PAGE = 1000;
const TBL_RE = /^[a-z][a-z0-9_]{0,39}$/;
/** Row ids carry names ("eq:pit default", "judges_slides/01 Robot.png"): any printable text. */
const UID_RE = /^[^\u0000-\u001f\u007f]{1,200}$/;
/** A machine id is ours to mint, so it can be strict. */
const MACHINE_RE = /^[A-Za-z0-9:._\-]{1,80}$/;
const SHA_RE = /^[0-9a-f]{64}$/;
/** Workers refuse request bodies over 100 MB on the free plan. */
const MAX_SINGLE_PUT = 95 * 1024 * 1024;

// ── helpers ───────────────────────────────────────────────────────────────────

function safeEqual(a: string | null, b: string | undefined): boolean {
  if (a === null || !b) return false;
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  return ab.byteLength === bb.byteLength && crypto.subtle.timingSafeEqual(ab, bb);
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

function roleOf(request: Request, env: Env): Role | null {
  const auth = request.headers.get("Authorization");
  // Both compared, neither short-circuits.
  const home = safeEqual(auth, env.HOME_TOKEN ? `Bearer ${env.HOME_TOKEN}` : undefined);
  const pit = safeEqual(auth, env.PIT_TOKEN ? `Bearer ${env.PIT_TOKEN}` : undefined);
  if (home) return "home";
  if (pit) return "pit";
  return null;
}

function hub(env: Env): DurableObjectStub<SyncHub> {
  return env.HUB.get(env.HUB.idFromName("team"));
}

function blobKey(sha: string): string {
  return `blob/${sha}`;
}

// ── The Durable Object ────────────────────────────────────────────────────────

export class SyncHub extends DurableObject<Env> {
  private sql: SqlStorage;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    // Idempotent; runs on every cold start, costs nothing once the tables exist.
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS row_state (
        tbl TEXT NOT NULL, uid TEXT NOT NULL,
        seq INTEGER NOT NULL, op TEXT NOT NULL, data TEXT,
        origin TEXT NOT NULL, at INTEGER NOT NULL,
        PRIMARY KEY (tbl, uid)
      );
      CREATE INDEX IF NOT EXISTS idx_row_seq ON row_state(seq);
      CREATE TABLE IF NOT EXISTS machine (
        id TEXT PRIMARY KEY, name TEXT, role TEXT, version TEXT,
        first_seen INTEGER, last_seen INTEGER,
        last_pull_seq INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0,
        conflicts INTEGER DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS blob (
        sha TEXT PRIMARY KEY, bytes INTEGER NOT NULL, kind TEXT, name TEXT,
        origin TEXT, created INTEGER NOT NULL, home_acked INTEGER DEFAULT 0
      );
    `);
  }

  private meta(k: string): string | null {
    const row = this.sql.exec("SELECT v FROM meta WHERE k = ?", k).toArray()[0];
    return row ? String(row.v) : null;
  }

  private setMeta(k: string, v: string): void {
    this.sql.exec("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", k, v);
  }

  /** Random at birth. A pit whose cursor was for another epoch starts over. */
  private epoch(): string {
    let e = this.meta("epoch");
    if (!e) {
      e = crypto.randomUUID();
      this.setMeta("epoch", e);
      this.setMeta("seq", "0");
    }
    return e;
  }

  private head(): number {
    this.epoch();
    return Number(this.meta("seq") ?? "0");
  }

  private seen(machine: string, name: string, role: Role, version: string, patch: Partial<{ pull: number; pushed: number; conflicts: number }> = {}): void {
    const now = Date.now();
    this.sql.exec(
      `INSERT INTO machine (id, name, role, version, first_seen, last_seen)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET name = excluded.name, role = excluded.role,
         version = excluded.version, last_seen = excluded.last_seen`,
      machine, name, role, version, now, now);
    if (patch.pull !== undefined) {
      this.sql.exec("UPDATE machine SET last_pull_seq = ? WHERE id = ?", patch.pull, machine);
    }
    if (patch.pushed || patch.conflicts) {
      this.sql.exec("UPDATE machine SET pushed = pushed + ?, conflicts = conflicts + ? WHERE id = ?",
        patch.pushed ?? 0, patch.conflicts ?? 0, machine);
    }
  }

  private row(tbl: string, uid: string): StoredRow | null {
    const r = this.sql.exec(
      "SELECT seq, tbl, uid, op, data, origin, at FROM row_state WHERE tbl = ? AND uid = ?", tbl, uid,
    ).toArray()[0];
    return r ? decode(r) : null;
  }

  async push(machine: string, name: string, role: Role, version: string, changes: Change[], force: boolean): Promise<{ epoch: string; head: number; results: PushResult[] }> {
    const epoch = this.epoch();
    // Only home may overrule. A pit asking to force is just a pit.
    const mayForce = force && role === "home";
    let seq = this.head();
    const results: PushResult[] = [];
    let pushed = 0;
    let conflicts = 0;

    for (const c of changes.slice(0, MAX_CHANGES_PER_PUSH)) {
      const bad = invalid(c);
      if (bad) {
        results.push({ tbl: String(c?.tbl), uid: String(c?.uid), status: "invalid", reason: bad });
        continue;
      }
      const cur = this.row(c.tbl, c.uid);
      const base = Number(c.base ?? 0);
      // A newer write from someone else wins. Our own newer write doesn't
      // count: a pit that pushed, lost the reply and edits again is not in
      // conflict with itself.
      if (!mayForce && cur && cur.seq > base && cur.origin !== machine) {
        results.push({ tbl: c.tbl, uid: c.uid, status: "conflict", current: cur });
        conflicts++;
        continue;
      }
      seq += 1;
      const data = c.op === "delete" ? null : JSON.stringify(c.data ?? {});
      this.sql.exec(
        `INSERT INTO row_state (tbl, uid, seq, op, data, origin, at) VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(tbl, uid) DO UPDATE SET seq = excluded.seq, op = excluded.op,
           data = excluded.data, origin = excluded.origin, at = excluded.at`,
        c.tbl, c.uid, seq, c.op, data, machine, Date.now());
      results.push({ tbl: c.tbl, uid: c.uid, status: "ok", seq });
      pushed++;
    }
    this.setMeta("seq", String(seq));
    this.seen(machine, name, role, version, { pushed, conflicts });
    return { epoch, head: seq, results };
  }

  async changes(machine: string, name: string, role: Role, version: string, since: number, limit: number): Promise<{ epoch: string; head: number; changes: StoredRow[]; more: boolean }> {
    const epoch = this.epoch();
    const head = this.head();
    const n = Math.max(1, Math.min(MAX_PAGE, limit));
    const rows = this.sql.exec(
      "SELECT seq, tbl, uid, op, data, origin, at FROM row_state WHERE seq > ? ORDER BY seq LIMIT ?",
      since, n + 1,
    ).toArray().map(decode);
    const more = rows.length > n;
    const page = rows.slice(0, n);
    const reached = page.length ? page[page.length - 1].seq : since;
    this.seen(machine, name, role, version, { pull: reached });
    return { epoch, head, changes: page, more };
  }

  async registerBlob(info: Omit<BlobInfo, "created" | "home_acked">): Promise<void> {
    this.sql.exec(
      `INSERT INTO blob (sha, bytes, kind, name, origin, created) VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(sha) DO NOTHING`,
      info.sha, info.bytes, info.kind, info.name, info.origin, Date.now());
  }

  async blobs(unackedOnly: boolean, limit: number): Promise<BlobInfo[]> {
    const where = unackedOnly ? "WHERE home_acked = 0" : "";
    return this.sql.exec(
      `SELECT sha, bytes, kind, name, origin, created, home_acked FROM blob ${where} ORDER BY created LIMIT ?`,
      Math.max(1, Math.min(MAX_PAGE, limit)),
    ).toArray() as unknown as BlobInfo[];
  }

  async hasBlob(sha: string): Promise<boolean> {
    return this.sql.exec("SELECT 1 FROM blob WHERE sha = ?", sha).toArray().length > 0;
  }

  async ackBlob(sha: string): Promise<boolean> {
    const c = this.sql.exec("UPDATE blob SET home_acked = ? WHERE sha = ?", Date.now(), sha);
    return c.rowsWritten > 0;
  }

  async forgetBlob(sha: string): Promise<void> {
    this.sql.exec("DELETE FROM blob WHERE sha = ?", sha);
  }

  async status(): Promise<Record<string, unknown>> {
    const one = (q: string) => Number(this.sql.exec(q).toArray()[0]?.n ?? 0);
    return {
      epoch: this.epoch(),
      head: this.head(),
      rows: one("SELECT COUNT(*) AS n FROM row_state WHERE op = 'upsert'"),
      tombstones: one("SELECT COUNT(*) AS n FROM row_state WHERE op = 'delete'"),
      blobs: one("SELECT COUNT(*) AS n FROM blob"),
      blobBytes: one("SELECT COALESCE(SUM(bytes), 0) AS n FROM blob"),
      blobsUnacked: one("SELECT COUNT(*) AS n FROM blob WHERE home_acked = 0"),
      tables: this.sql.exec("SELECT tbl, COUNT(*) AS n, MAX(seq) AS last FROM row_state GROUP BY tbl ORDER BY tbl").toArray(),
      machines: this.sql.exec("SELECT * FROM machine ORDER BY last_seen DESC").toArray(),
    };
  }
}

function decode(r: Record<string, SqlStorageValue>): StoredRow {
  return {
    seq: Number(r.seq),
    tbl: String(r.tbl),
    uid: String(r.uid),
    op: String(r.op),
    data: r.data === null || r.data === undefined ? null : JSON.parse(String(r.data)),
    origin: String(r.origin),
    at: Number(r.at),
  };
}

function invalid(c: Change): string | null {
  if (!c || typeof c !== "object") return "not an object";
  if (typeof c.tbl !== "string" || !TBL_RE.test(c.tbl)) return "bad tbl";
  if (typeof c.uid !== "string" || !UID_RE.test(c.uid)) return "bad uid";
  if (c.op !== "upsert" && c.op !== "delete") return "bad op";
  if (c.op === "upsert") {
    if (c.data === null || typeof c.data !== "object") return "upsert needs an object";
    if (JSON.stringify(c.data).length > MAX_ROW_BYTES) return "row too large";
  }
  return null;
}

// ── Blobs (R2) ────────────────────────────────────────────────────────────────

/**
 * Files are addressed by their SHA-256, so an upload is idempotent and a
 * download is verifiable. A single PUT is checked by R2 against the hash
 * (`sha256` option); a multipart upload can't be, so the reader verifies —
 * both the pit client and the home agent hash what they download.
 */
async function handleBlob(request: Request, env: Env, parts: string[], machine: string, role: Role): Promise<Response> {
  const sha = parts[0];
  if (!SHA_RE.test(sha)) return json({ error: "bad sha256" }, 400);
  const key = blobKey(sha);
  const stub = hub(env);
  const kind = (request.headers.get("X-Blob-Kind") ?? "file").slice(0, 40);
  const name = (request.headers.get("X-Blob-Name") ?? "").slice(0, 300);

  // /v1/blob/{sha}
  if (parts.length === 1) {
    if (request.method === "HEAD") {
      const head = await env.BLOBS.head(key);
      return new Response(null, { status: head ? 200 : 404, headers: head ? { "content-length": String(head.size) } : {} });
    }
    if (request.method === "GET") {
      const obj = await env.BLOBS.get(key);
      if (!obj) return json({ error: "no such blob" }, 404);
      return new Response(obj.body, {
        headers: { "content-type": "application/octet-stream", "content-length": String(obj.size), "x-blob-sha256": sha },
      });
    }
    if (request.method === "PUT") {
      const len = Number(request.headers.get("content-length") ?? "-1");
      if (len < 0) return json({ error: "content-length required" }, 411);
      if (len > MAX_SINGLE_PUT) return json({ error: "too large for one PUT; use multipart" }, 413);
      const existing = await env.BLOBS.head(key);
      if (!existing) {
        try {
          await env.BLOBS.put(key, request.body, { sha256: sha });
        } catch (err) {
          return json({ error: `upload rejected: ${String(err)}` }, 400);
        }
      }
      await stub.registerBlob({ sha, bytes: len, kind, name, origin: machine });
      return json({ ok: true, sha, existed: Boolean(existing) });
    }
    if (request.method === "DELETE") {
      if (role !== "home") return json({ error: "only home may delete blobs" }, 403);
      await env.BLOBS.delete(key);
      await stub.forgetBlob(sha);
      return json({ ok: true });
    }
    return json({ error: "method not allowed" }, 405);
  }

  // /v1/blob/{sha}/ack
  if (parts.length === 2 && parts[1] === "ack" && request.method === "POST") {
    if (role !== "home") return json({ error: "only home acknowledges" }, 403);
    return json({ ok: await stub.ackBlob(sha) });
  }

  // Multipart: POST /v1/blob/{sha}/mpu → {uploadId}
  //            PUT  /v1/blob/{sha}/mpu/{uploadId}/{part} → {partNumber, etag}
  //            POST /v1/blob/{sha}/mpu/{uploadId}/complete {parts, bytes}
  //            DELETE /v1/blob/{sha}/mpu/{uploadId}
  if (parts[1] !== "mpu") return json({ error: "not found" }, 404);
  if (parts.length === 2 && request.method === "POST") {
    if (await env.BLOBS.head(key)) return json({ existed: true, sha });
    const mpu = await env.BLOBS.createMultipartUpload(key);
    return json({ uploadId: mpu.uploadId });
  }
  const uploadId = parts[2];
  if (!uploadId) return json({ error: "not found" }, 404);
  const mpu = env.BLOBS.resumeMultipartUpload(key, uploadId);
  if (parts.length === 4 && parts[3] !== "complete" && request.method === "PUT") {
    const n = Number(parts[3]);
    if (!Number.isInteger(n) || n < 1 || n > 10000) return json({ error: "bad part number" }, 400);
    if (!request.body) return json({ error: "empty part" }, 400);
    const part = await mpu.uploadPart(n, request.body);
    return json(part);
  }
  if (parts.length === 4 && parts[3] === "complete" && request.method === "POST") {
    const body = await request.json() as { parts: R2UploadedPart[]; bytes: number };
    await mpu.complete(body.parts);
    await stub.registerBlob({ sha, bytes: Number(body.bytes ?? 0), kind, name, origin: machine });
    return json({ ok: true, sha });
  }
  if (parts.length === 3 && request.method === "DELETE") {
    await mpu.abort();
    return json({ ok: true });
  }
  return json({ error: "not found" }, 404);
}

// ── The Worker ────────────────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/healthz") {
      return json({ ok: true, pitToken: Boolean(env.PIT_TOKEN), homeToken: Boolean(env.HOME_TOKEN) });
    }
    if (!path.startsWith("/v1/")) return json({ error: "not found" }, 404);

    const role = roleOf(request, env);
    if (!role) return json({ error: "The sync hub refused this token." }, 401);
    const machine = (request.headers.get("X-Pit-Machine") ?? "").trim();
    if (!MACHINE_RE.test(machine)) return json({ error: "X-Pit-Machine header required" }, 400);
    const name = (request.headers.get("X-Pit-Name") ?? machine).slice(0, 80);
    const version = (request.headers.get("X-Pit-Version") ?? "").slice(0, 40);

    const parts = path.slice("/v1/".length).split("/").map(decodeURIComponent);
    const stub = hub(env);

    if (parts[0] === "push" && parts.length === 1 && request.method === "POST") {
      let body: { changes?: Change[]; force?: boolean };
      try { body = await request.json(); } catch { return json({ error: "not json" }, 400); }
      if (!Array.isArray(body.changes)) return json({ error: "changes[] required" }, 400);
      if (body.changes.length > MAX_CHANGES_PER_PUSH) {
        return json({ error: `at most ${MAX_CHANGES_PER_PUSH} changes per push` }, 413);
      }
      return json(await stub.push(machine, name, role, version, body.changes, Boolean(body.force)));
    }
    if (parts[0] === "changes" && parts.length === 1 && request.method === "GET") {
      const since = Number(url.searchParams.get("since") ?? "0") || 0;
      const limit = Number(url.searchParams.get("limit") ?? "500") || 500;
      return json(await stub.changes(machine, name, role, version, since, limit));
    }
    if (parts[0] === "status" && parts.length === 1 && request.method === "GET") {
      return json(await stub.status());
    }
    if (parts[0] === "blobs" && parts.length === 1 && request.method === "GET") {
      const unacked = url.searchParams.get("unacked") === "1";
      const limit = Number(url.searchParams.get("limit") ?? "500") || 500;
      return json({ blobs: await stub.blobs(unacked, limit) });
    }
    if (parts[0] === "blob" && parts.length >= 2) {
      return handleBlob(request, env, parts.slice(1), machine, role);
    }
    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;
