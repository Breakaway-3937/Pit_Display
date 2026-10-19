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
 * **Storage is the truth, and there are no timers.** Class fields only
 * cache what storage holds (`meta`, `machine`, `tbl_stat`), written through, loaded
 * again on every start. The SQL API is synchronous, so a push with no
 * `await` inside cannot interleave with another.
 *
 * **The free plan bills rows: 5M read and 100k written a day** (writes
 * shared with the relay), and a row scanned counts as read. `status()` once
 * counted row_state and blob from scratch (~3,200 rows at 866 rows / 202
 * files), and every pit and home called it every minute: 90% of the day's
 * reads by midday (2026-10-18). So:
 *   • counts are kept as they change (`tbl_stat`, blob counters in `meta`),
 *     built once from the tables, one write per (tbl, op) per push;
 *   • an up-to-date pull, home's "anything unacked?" and status run no
 *     table query; meta and machines come from memory;
 *   • a machine's row is written only when something about it changed, or
 *     its last_seen is SEEN_FLUSH_MS old in storage.
 * An idle minute across every machine reads and writes no rows. `usage` in
 * /v1/status is the instance's own tally; `sync_check` holds it to that.
 */
import { DurableObject } from "cloudflare:workers";

export interface Env {
  HUB: DurableObjectNamespace<SyncHub>;
  BLOBS: R2Bucket;
  PIT_TOKEN: string;   // secret — what every pit machine presents ("new,old" while rotating)
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

/**
 * Each secret may hold several tokens, comma-separated, so a token can be
 * rotated without locking anyone out: set "new,old", move every machine to
 * the new one, then set "new" (home/OPERATIONS.md). Every entry is compared in
 * constant time and none short-circuits.
 */
function anyToken(auth: string | null, configured: string | undefined): boolean {
  let ok = false;
  for (const t of (configured ?? "").split(",").map((x) => x.trim()).filter(Boolean)) {
    if (safeEqual(auth, `Bearer ${t}`)) ok = true;
  }
  return ok;
}

function roleOf(request: Request, env: Env): Role | null {
  const auth = request.headers.get("Authorization");
  const home = anyToken(auth, env.HOME_TOKEN);
  const pit = anyToken(auth, env.PIT_TOKEN);
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

/** A machine's row as kept in memory; `stored` is the last_seen written to storage. */
type Machine = {
  id: string; name: string; role: string; version: string;
  first_seen: number; last_seen: number; last_pull_seq: number;
  pushed: number; conflicts: number; stored: number;
};

/**
 * last_seen is written at most this often when nothing else about a machine
 * changed: an idle pull then reads and writes no rows at all. Status reads
 * the exact time from memory, so only a DO restart can show it this stale.
 */
const SEEN_FLUSH_MS = 5 * 60 * 1000;

export class SyncHub extends DurableObject<Env> {
  private sql: SqlStorage;
  /**
   * Write-through copies of `meta`, `machine` and `tbl_stat`, loaded once per instance.
   * Storage stays the truth (every change is written before it's answered,
   * except last_seen, see SEEN_FLUSH_MS); these only spare the reads. One
   * instance serves every request, so nothing else can make them stale.
   */
  private metaCache = new Map<string, string>();
  private machines = new Map<string, Machine>();
  private stats = new Map<string, { tbl: string; op: string; n: number; last: number }>();
  /** Rows this instance read and wrote, since `usageSince` (status shows it). */
  private usage = { rowsRead: 0, rowsWritten: 0, calls: 0 };
  private usageSince = Date.now();

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
      CREATE INDEX IF NOT EXISTS idx_blob_acked ON blob(home_acked, created);
      CREATE TABLE IF NOT EXISTS tbl_stat (
        tbl TEXT NOT NULL, op TEXT NOT NULL, n INTEGER NOT NULL, last INTEGER NOT NULL,
        PRIMARY KEY (tbl, op)
      );
    `);
    for (const r of this.run("SELECT k, v FROM meta")) this.metaCache.set(String(r.k), String(r.v));
    for (const r of this.run("SELECT * FROM machine")) {
      const m = {
        id: String(r.id), name: String(r.name ?? r.id), role: String(r.role ?? ""),
        version: String(r.version ?? ""), first_seen: Number(r.first_seen ?? 0),
        last_seen: Number(r.last_seen ?? 0), last_pull_seq: Number(r.last_pull_seq ?? 0),
        pushed: Number(r.pushed ?? 0), conflicts: Number(r.conflicts ?? 0), stored: 0,
      };
      m.stored = m.last_seen;
      this.machines.set(m.id, m);
    }
    this.buildStats();
    for (const r of this.run("SELECT tbl, op, n, last FROM tbl_stat")) {
      this.stats.set(`${r.tbl}\u0000${r.op}`,
        { tbl: String(r.tbl), op: String(r.op), n: Number(r.n), last: Number(r.last) });
    }
  }

  /** Every query goes through here, so `usage` is the bill. */
  private run(query: string, ...args: SqlStorageValue[]): Record<string, SqlStorageValue>[] {
    const cur = this.sql.exec(query, ...args);
    const rows = cur.toArray();
    this.usage.rowsRead += cur.rowsRead;
    this.usage.rowsWritten += cur.rowsWritten;
    return rows;
  }

  private written(query: string, ...args: SqlStorageValue[]): number {
    const cur = this.sql.exec(query, ...args);
    cur.toArray();
    this.usage.rowsRead += cur.rowsRead;
    this.usage.rowsWritten += cur.rowsWritten;
    return cur.rowsWritten;
  }

  /** The kept counts, built from the tables once (a hub that predates them). */
  private buildStats(): void {
    if (this.meta("stats") === "1") return;
    this.ctx.storage.transactionSync(() => {
      this.run("DELETE FROM tbl_stat");
      this.run(`INSERT INTO tbl_stat (tbl, op, n, last)
                SELECT tbl, op, COUNT(*), MAX(seq) FROM row_state GROUP BY tbl, op`);
      const b = this.run(`SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS bytes,
                            COALESCE(SUM(home_acked = 0), 0) AS unacked FROM blob`)[0];
      this.setMeta("blobs", String(b?.n ?? 0));
      this.setMeta("blobBytes", String(b?.bytes ?? 0));
      this.setMeta("blobsUnacked", String(b?.unacked ?? 0));
      this.setMeta("stats", "1");
    });
  }

  private meta(k: string): string | null {
    return this.metaCache.get(k) ?? null;
  }

  private setMeta(k: string, v: string): void {
    if (this.metaCache.get(k) === v) return;
    this.run("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", k, v);
    this.metaCache.set(k, v);
  }

  private bump(k: string, delta: number): void {
    if (delta) this.setMeta(k, String(Number(this.meta(k) ?? "0") + delta));
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

  /**
   * Note a machine's visit. Written only when something about it changed
   * (name, version, pull position, pushes), or last_seen is SEEN_FLUSH_MS
   * old in storage: one statement, usually none.
   */
  private seen(id: string, name: string, role: Role, version: string, patch: Partial<{ pull: number; pushed: number; conflicts: number }> = {}): void {
    this.usage.calls++;
    const now = Date.now();
    let m = this.machines.get(id);
    const fresh = !m;
    if (!m) {
      m = { id, name, role, version, first_seen: now, last_seen: now, last_pull_seq: 0,
            pushed: 0, conflicts: 0, stored: 0 };
      this.machines.set(id, m);
    }
    const changed = fresh || m.name !== name || m.role !== role || m.version !== version
      || (patch.pull !== undefined && patch.pull !== m.last_pull_seq)
      || Boolean(patch.pushed) || Boolean(patch.conflicts);
    m.name = name; m.role = role; m.version = version; m.last_seen = now;
    if (patch.pull !== undefined) m.last_pull_seq = patch.pull;
    m.pushed += patch.pushed ?? 0;
    m.conflicts += patch.conflicts ?? 0;
    if (!changed && now - m.stored < SEEN_FLUSH_MS) return;
    this.run(
      `INSERT INTO machine (id, name, role, version, first_seen, last_seen, last_pull_seq, pushed, conflicts)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET name = excluded.name, role = excluded.role,
         version = excluded.version, last_seen = excluded.last_seen,
         last_pull_seq = excluded.last_pull_seq, pushed = excluded.pushed,
         conflicts = excluded.conflicts`,
      m.id, m.name, m.role, m.version, m.first_seen, m.last_seen, m.last_pull_seq,
      m.pushed, m.conflicts);
    m.stored = now;
  }

  private row(tbl: string, uid: string): StoredRow | null {
    const r = this.run(
      "SELECT seq, tbl, uid, op, data, origin, at FROM row_state WHERE tbl = ? AND uid = ?", tbl, uid,
    )[0];
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
    // Count changes per (tbl, op) here and write each once at the end.
    const deltas = new Map<string, { tbl: string; op: string; n: number; last: number }>();
    const count = (tbl: string, op: string, n: number, last: number) => {
      const k = `${tbl}\u0000${op}`;
      const d = deltas.get(k) ?? { tbl, op, n: 0, last: 0 };
      d.n += n;
      d.last = Math.max(d.last, last);
      deltas.set(k, d);
    };

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
      this.run(
        `INSERT INTO row_state (tbl, uid, seq, op, data, origin, at) VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(tbl, uid) DO UPDATE SET seq = excluded.seq, op = excluded.op,
           data = excluded.data, origin = excluded.origin, at = excluded.at`,
        c.tbl, c.uid, seq, c.op, data, machine, Date.now());
      if (cur) count(cur.tbl, cur.op, -1, 0);
      count(c.tbl, c.op, 1, seq);
      results.push({ tbl: c.tbl, uid: c.uid, status: "ok", seq });
      pushed++;
    }
    for (const d of deltas.values()) {
      if (!d.n && !d.last) continue;      // only ever decremented to zero
      this.run(
        `INSERT INTO tbl_stat (tbl, op, n, last) VALUES (?, ?, ?, ?)
         ON CONFLICT(tbl, op) DO UPDATE SET n = n + excluded.n, last = MAX(last, excluded.last)`,
        d.tbl, d.op, d.n, d.last);
      const k = `${d.tbl}\u0000${d.op}`;
      const s = this.stats.get(k) ?? { tbl: d.tbl, op: d.op, n: 0, last: 0 };
      this.stats.set(k, { ...s, n: s.n + d.n, last: Math.max(s.last, d.last) });
    }
    this.setMeta("seq", String(seq));
    this.seen(machine, name, role, version, { pushed, conflicts });
    return { epoch, head: seq, results };
  }

  async changes(machine: string, name: string, role: Role, version: string, since: number, limit: number): Promise<{ epoch: string; head: number; changes: StoredRow[]; more: boolean }> {
    const epoch = this.epoch();
    const head = this.head();
    const n = Math.max(1, Math.min(MAX_PAGE, limit));
    // Up to date (the usual answer): no query at all.
    const rows = since >= head ? [] : this.run(
      "SELECT seq, tbl, uid, op, data, origin, at FROM row_state WHERE seq > ? ORDER BY seq LIMIT ?",
      since, n + 1,
    ).map(decode);
    const more = rows.length > n;
    const page = rows.slice(0, n);
    const reached = page.length ? page[page.length - 1].seq : since;
    this.seen(machine, name, role, version, { pull: reached });
    return { epoch, head, changes: page, more };
  }

  async registerBlob(info: Omit<BlobInfo, "created" | "home_acked">): Promise<void> {
    const wrote = this.written(
      `INSERT INTO blob (sha, bytes, kind, name, origin, created) VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(sha) DO NOTHING`,
      info.sha, info.bytes, info.kind, info.name, info.origin, Date.now());
    if (wrote > 0) {
      this.bump("blobs", 1);
      this.bump("blobBytes", info.bytes);
      this.bump("blobsUnacked", 1);
    }
  }

  private blobRow(sha: string): { bytes: number; home_acked: number } | null {
    const r = this.run("SELECT bytes, home_acked FROM blob WHERE sha = ?", sha)[0];
    return r ? { bytes: Number(r.bytes), home_acked: Number(r.home_acked) } : null;
  }

  async blobs(unackedOnly: boolean, limit: number): Promise<BlobInfo[]> {
    const n = Math.max(1, Math.min(MAX_PAGE, limit));
    // Nothing waiting for home (the usual answer): no query at all.
    if (unackedOnly && Number(this.meta("blobsUnacked") ?? "0") <= 0) return [];
    const where = unackedOnly ? "WHERE home_acked = 0" : "";
    return this.run(
      `SELECT sha, bytes, kind, name, origin, created, home_acked FROM blob ${where} ORDER BY created LIMIT ?`,
      n,
    ) as unknown as BlobInfo[];
  }

  async hasBlob(sha: string): Promise<boolean> {
    return this.run("SELECT 1 FROM blob WHERE sha = ?", sha).length > 0;
  }

  async ackBlob(sha: string): Promise<boolean> {
    const before = this.blobRow(sha);
    if (!before) return false;
    this.run("UPDATE blob SET home_acked = ? WHERE sha = ?", Date.now(), sha);
    if (!before.home_acked) this.bump("blobsUnacked", -1);
    return true;
  }

  async forgetBlob(sha: string): Promise<void> {
    const before = this.blobRow(sha);
    if (!before) return;
    this.run("DELETE FROM blob WHERE sha = ?", sha);
    this.bump("blobs", -1);
    this.bump("blobBytes", -before.bytes);
    if (!before.home_acked) this.bump("blobsUnacked", -1);
  }

  /** A machine retired or a test run: off every pit's Telemetry list. Its rows stay. */
  async forgetMachine(id: string): Promise<boolean> {
    const gone = this.written("DELETE FROM machine WHERE id = ?", id) > 0;
    return this.machines.delete(id) || gone;
  }

  /** From memory: no rows read, however big the hub gets. */
  async status(): Promise<Record<string, unknown>> {
    const stats = [...this.stats.values()].filter((s) => s.n > 0)
      .sort((a, b) => a.tbl.localeCompare(b.tbl));
    const ops = (op: string) => stats.filter((s) => s.op === op).reduce((a, s) => a + Number(s.n), 0);
    const tables = new Map<string, { tbl: string; n: number; last: number }>();
    for (const s of stats) {
      const t = tables.get(String(s.tbl)) ?? { tbl: String(s.tbl), n: 0, last: 0 };
      t.n += Number(s.n);
      t.last = Math.max(t.last, Number(s.last));
      tables.set(t.tbl, t);
    }
    const num = (k: string) => Number(this.meta(k) ?? "0");
    const machines = [...this.machines.values()]
      .sort((a, b) => b.last_seen - a.last_seen)
      .map(({ stored: _stored, ...m }) => m);
    return {
      epoch: this.epoch(),
      head: this.head(),
      rows: ops("upsert"),
      tombstones: ops("delete"),
      blobs: num("blobs"),
      blobBytes: num("blobBytes"),
      blobsUnacked: num("blobsUnacked"),
      tables: [...tables.values()],
      machines,
      // This instance's bill (a DO restarts when idle, so it's a sample, not the day).
      usage: { since: this.usageSince, ...this.usage },
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
    if (parts[0] === "machine" && parts.length === 2 && request.method === "DELETE") {
      if (role !== "home") return json({ error: "only home may forget a machine" }, 403);
      return json({ ok: await stub.forgetMachine(parts[1]) });
    }
    if (parts[0] === "blob" && parts.length >= 2) {
      return handleBlob(request, env, parts.slice(1), machine, role);
    }
    return json({ error: "not found" }, 404);
  },
} satisfies ExportedHandler<Env>;
