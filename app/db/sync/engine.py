"""
One sync cycle, start to finish, with no Qt in it. `service.py` runs it on a
worker thread; `tools/sync_check.py` runs it directly.

    scan   settings JSON and team files → outbox, when their hash moved
    push   outbox → hub, oldest parent first; uploads bundles and files first
    pull   hub changes since our cursor → SQLite / JSON / disk
    retry  pulled rows that were waiting on a parent or a track
    fetch  log bundles and files that pulled rows point at (a few per cycle)

**The hub decides.** A push the hub rejects (another machine wrote that row
since we last saw it) is answered with the current row, which is applied
here and replaces the local edit. A pulled change for a row with an
unpushed local edit wins the same way. The Telemetry panel names both.

**Writes go through the guard.** Every pulled write runs inside
`_guarded()`, which raises `sync_guard.applying` for the transaction, so the
triggers that record local edits stay quiet and nothing is echoed back.

The engine keeps its own SQLite connection (autocommit, explicit
transactions), like the log importer: it must never share the GUI's.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app import paths
from app.db.sync import bundle, codec, docs, tables
from app.db.sync.client import HubClient, SyncError, sha256_file
from app.db.sync.tables import LOG_SESSION, ANALYSIS_BOARD, Pending

PUSH_BATCH = 100
PULL_PAGE = 500
FETCH_PER_CYCLE = 3
# The original log, when it goes up at all (`upload_raw`, off by default: the
# bundle already carries every record), goes compressed; past this compressed
# size it's skipped. The 3.85 GB Phoenix export compresses to ~110 MB.
RAW_MAX_BYTES = 512 * 1024 * 1024
WAITING_FOR_FILES = ("bundle", "download")


@dataclass
class Report:
    pushed: int = 0
    pulled: int = 0
    applied: set[str] = field(default_factory=set)
    conflicts: list[str] = field(default_factory=list)
    # Rows a machine's first sync took from the team instead of its own.
    adopted: int = 0
    errors: list[str] = field(default_factory=list)
    uploaded_bytes: int = 0
    downloaded_bytes: int = 0
    outbox: int = 0
    pending: int = 0
    waiting: int = 0
    cursor: int = 0
    head: int = 0
    seconds: float = 0.0
    # The hub's view of every machine: [{id, name, role, version, last_seen, …}]
    machines: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def scratch_dir() -> Path:
    return paths.data_dir("sync")


class Engine:

    def __init__(self, db_path: Path, client: HubClient, machine_id: str,
                 prefs: dict, say: Callable[[str], None] | None = None):
        self.db_path = Path(db_path)
        self.client = client
        self.me = machine_id
        self.prefs = prefs
        self._say = say or (lambda _msg: None)
        self.hasher = docs.FileHasher()

    # ── plumbing ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        samples = self.db_path.with_name(self.db_path.stem + "_samples" + self.db_path.suffix)
        conn.execute("ATTACH DATABASE ? AS samples", (str(samples),))
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _guarded(self, conn: sqlite3.Connection):
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE sync_guard SET applying = 1 WHERE id = 1")
            yield
            conn.execute("UPDATE sync_guard SET applying = 0 WHERE id = 1")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _meta(conn, k: str, default: str = "") -> str:
        row = conn.execute("SELECT v FROM sync_meta WHERE k = ?", (k,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def _set_meta(conn, k: str, v) -> None:
        conn.execute("INSERT INTO sync_meta (k, v) VALUES (?, ?) "
                     "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (k, str(v)))

    @staticmethod
    def _seq(conn, tbl: str, uid: str) -> int:
        row = conn.execute("SELECT seq FROM sync_row WHERE tbl = ? AND uid = ?",
                           (tbl, uid)).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _saw(conn, tbl: str, uid: str, seq: int) -> None:
        conn.execute("INSERT INTO sync_row (tbl, uid, seq) VALUES (?, ?, ?) "
                     "ON CONFLICT (tbl, uid) DO UPDATE SET seq = MAX(seq, excluded.seq)",
                     (tbl, uid, int(seq)))

    @staticmethod
    def _enqueue(conn, tbl: str, uid: str, op: str) -> None:
        conn.execute("INSERT INTO sync_outbox (tbl, uid, op) VALUES (?, ?, ?) "
                     "ON CONFLICT (tbl, uid) DO UPDATE SET op = excluded.op, "
                     "at = excluded.at, rev = rev + 1", (tbl, uid, op))

    @staticmethod
    def _set_doc(conn, tbl: str, uid: str, digest: str | None) -> None:
        if digest is None:
            conn.execute("DELETE FROM sync_doc WHERE tbl = ? AND uid = ?", (tbl, uid))
        else:
            conn.execute("INSERT INTO sync_doc (tbl, uid, hash) VALUES (?, ?, ?) "
                         "ON CONFLICT (tbl, uid) DO UPDATE SET hash = excluded.hash",
                         (tbl, uid, digest))

    # ── the cycle ─────────────────────────────────────────────────────────

    def cycle(self) -> Report:
        rep = Report()
        t0 = time.monotonic()
        conn = self._connect()
        try:
            for step in (self._scan, self._push, self._pull, self._retry, self._fetch,
                         self._status):
                try:
                    step(conn, rep)
                except SyncError as e:
                    rep.errors.append(str(e))
                    break               # the hub is unreachable: stop, try next cycle
                except Exception as e:  # a bug must not kill the service
                    rep.errors.append(f"{step.__name__.strip('_')}: {type(e).__name__}: {e}")
            rep.outbox = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
            rep.pending = conn.execute(
                "SELECT COUNT(*) FROM sync_pending WHERE reason NOT IN ('bundle', 'download')"
            ).fetchone()[0]
            rep.waiting = conn.execute(
                "SELECT COUNT(*) FROM sync_pending WHERE reason IN ('bundle', 'download')"
            ).fetchone()[0]
            rep.cursor = int(self._meta(conn, "cursor", "0") or 0)
        finally:
            conn.close()
        rep.seconds = round(time.monotonic() - t0, 2)
        return rep

    def _status(self, conn, rep: Report) -> None:
        rep.machines = list(self.client.status().get("machines", []))

    # ── scan: JSON settings and files have no triggers ────────────────────

    def _scan(self, conn, rep: Report) -> None:
        known = {(r[0], r[1]): r[2] for r in conn.execute("SELECT tbl, uid, hash FROM sync_doc")}
        for uid, (_data, digest) in docs.local_settings().items():
            if known.get((docs.SETTING, uid)) != digest:
                self._enqueue(conn, docs.SETTING, uid, "upsert")
        if not self.prefs.get("sync_files", True):
            return
        waiting = {r[0] for r in conn.execute(
            "SELECT uid FROM sync_pending WHERE tbl = 'file'")}
        local = self.hasher.local_files()
        for uid, (_path, sha, _size) in local.items():
            if known.get((docs.FILE, uid)) != sha and uid not in waiting:
                self._enqueue(conn, docs.FILE, uid, "upsert")
        for (tbl, uid) in known:
            if tbl == docs.FILE and uid not in local and uid not in waiting \
                    and docs.file_path(uid) is not None and not docs.file_path(uid).exists():
                self._enqueue(conn, docs.FILE, uid, "delete")

    # ── push ──────────────────────────────────────────────────────────────

    def _payload(self, conn, tbl: str, uid: str, rep: Report) -> dict | None:
        """The row as sent, None when it's gone. Uploads its files first."""
        spec = tables.BY_NAME.get(tbl)
        if spec is not None:
            return tables.serialize(conn, spec, uid)
        if tbl == LOG_SESSION:
            return self._session_payload(conn, uid, rep)
        if tbl == ANALYSIS_BOARD:
            row = conn.execute("SELECT spec FROM analysis_board WHERE uid = ?",
                               (uid,)).fetchone()
            return json.loads(row[0]) if row else None
        if tbl == docs.SETTING:
            return docs.setting_data(uid)
        if tbl == docs.FILE:
            path = docs.file_path(uid)
            if path is None or not path.is_file():
                return None
            sha = self.hasher.sha(path)
            size = path.stat().st_size
            self._say(f"Compressing {path.name}…")
            packed, blob, blob_bytes = self._pack(path)
            try:
                if packed is None:
                    blob, blob_bytes, how = sha, size, "none"
                    self._say(f"Uploading {path.name}…")
                    self.client.put_blob(path, sha, "file", uid)
                else:
                    how = "zstd"
                    self._say(f"Uploading {path.name} ({blob_bytes / 1e6:.1f} MB)…")
                    self.client.put_blob(packed, blob, "file", uid)
            finally:
                if packed is not None:
                    packed.unlink(missing_ok=True)
            rep.uploaded_bytes += blob_bytes
            return {"sha": sha, "bytes": size, "name": path.name,
                    "blob": blob, "blob_bytes": blob_bytes, "codec": how}
        raise ValueError(f"unknown table {tbl}")

    @staticmethod
    def _pack(path: Path) -> tuple[Path | None, str, int]:
        """zstd `path` to a temp file: (temp, its sha, its bytes), or (None, "", 0)
        when compression wouldn't save `codec.MIN_SAVING` (a JPG, a PNG)."""
        tmp = codec.temp_path(".zst", scratch_dir())
        sha, size = codec.compress_file(path, tmp)
        if size > path.stat().st_size * (1 - codec.MIN_SAVING):
            tmp.unlink(missing_ok=True)
            return None, "", 0
        return tmp, sha, size

    def _session_payload(self, conn, uid: str, rep: Report) -> dict | None:
        row = conn.execute("SELECT * FROM log_session WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            return None
        blobs = conn.execute("SELECT * FROM sync_session_blob WHERE uid = ?", (uid,)).fetchone()
        blobs = dict(blobs) if blobs else {"uid": uid}
        if not blobs.get("bundle_sha"):
            self._say(f"Packing {row['source_name']}…")
            gz = bundle.build(self.db_path, row["id"], scratch_dir())
            try:
                sha = sha256_file(gz)
                size = gz.stat().st_size
                self._say(f"Uploading {row['source_name']} ({size / 1e6:.1f} MB)…")
                self.client.put_blob(gz, sha, "bundle", row["source_name"])
                rep.uploaded_bytes += size
            finally:
                gz.unlink(missing_ok=True)
            blobs.update(bundle_sha=sha, bundle_bytes=size)
            self._save_blobs(conn, blobs)
        if self.prefs.get("upload_raw", True) and not blobs.get("raw_sha"):
            src = None
            for cand in (row["archive_path"], row["source_file"]):
                if cand and not str(cand).startswith("sync:") and Path(cand).is_file():
                    src = Path(cand)
                    break
            if src is not None:
                # Raw blobs are always a zstd frame of the original file.
                self._say(f"Compressing original {src.name}…")
                tmp = codec.temp_path(".zst", scratch_dir())
                try:
                    sha, size = codec.compress_file(src, tmp)
                    if size <= RAW_MAX_BYTES:
                        self._say(f"Uploading original {src.name} ({size / 1e6:.1f} MB)…")
                        self.client.put_blob(tmp, sha, "raw", src.name + ".zst")
                        rep.uploaded_bytes += size
                        blobs.update(raw_sha=sha, raw_bytes=size)
                        self._save_blobs(conn, blobs)
                finally:
                    tmp.unlink(missing_ok=True)
        data = {c: row[c] for c in tables.SESSION_COLS}
        for k in ("bundle_sha", "bundle_bytes", "raw_sha", "raw_bytes"):
            data[k] = blobs.get(k)
        return data

    @staticmethod
    def _save_blobs(conn, b: dict) -> None:
        conn.execute(
            """INSERT INTO sync_session_blob (uid, bundle_sha, bundle_bytes, raw_sha, raw_bytes)
               VALUES (:uid, :bundle_sha, :bundle_bytes, :raw_sha, :raw_bytes)
               ON CONFLICT (uid) DO UPDATE SET
                 bundle_sha = excluded.bundle_sha, bundle_bytes = excluded.bundle_bytes,
                 raw_sha = excluded.raw_sha, raw_bytes = excluded.raw_bytes""",
            {"bundle_sha": None, "bundle_bytes": None, "raw_sha": None, "raw_bytes": None, **b})

    def _push(self, conn, rep: Report) -> None:
        # Never pulled yet: everything here is this machine's install defaults
        # or its pre-sync data, and the team's copy winning isn't news.
        first_contact = not self._meta(conn, "epoch")
        entries = [dict(r) for r in conn.execute("SELECT tbl, uid, op, rev FROM sync_outbox")]
        entries.sort(key=lambda e: tables.order_key(e["tbl"]))
        for i in range(0, len(entries), PUSH_BATCH):
            batch, sent = [], {}
            for e in entries[i:i + PUSH_BATCH]:
                try:
                    data = None if e["op"] == "delete" else \
                        self._payload(conn, e["tbl"], e["uid"], rep)
                except SyncError:
                    raise
                except Exception as exc:
                    rep.errors.append(f"{e['tbl']} {e['uid'][:12]}: {type(exc).__name__}: {exc}")
                    continue
                op = "delete" if data is None else "upsert"
                batch.append({"tbl": e["tbl"], "uid": e["uid"], "op": op,
                              "base": self._seq(conn, e["tbl"], e["uid"]), "data": data})
                sent[(e["tbl"], e["uid"])] = (e["rev"], op, data)
            if not batch:
                continue
            self._say(f"Sending {len(batch)} change{'s' if len(batch) != 1 else ''}…")
            resp = self.client.push(batch)
            rep.head = max(rep.head, int(resp.get("head", 0)))
            for r in resp.get("results", []):
                key = (r.get("tbl"), r.get("uid"))
                if key not in sent:
                    continue
                rev, op, data = sent[key]
                tbl, uid = key
                done = "DELETE FROM sync_outbox WHERE tbl = ? AND uid = ? AND rev = ?"
                if r["status"] == "ok":
                    conn.execute("BEGIN")
                    self._saw(conn, tbl, uid, r["seq"])
                    conn.execute(done, (tbl, uid, rev))
                    if tbl == docs.SETTING:
                        self._set_doc(conn, tbl, uid, docs.hash_json(data) if data else None)
                    elif tbl == docs.FILE:
                        self._set_doc(conn, tbl, uid, data["sha"] if data else None)
                    conn.execute("COMMIT")
                    rep.pushed += 1
                elif r["status"] == "conflict":
                    conn.execute(done, (tbl, uid, rev))
                    cur = r.get("current") or {}
                    if first_contact:
                        # A new machine's stock rows and default settings
                        # meeting the team's: adopting them is the point.
                        rep.adopted += 1
                    elif cur.get("op") == op and cur.get("data") == data:
                        pass        # both machines made the same change
                    else:
                        rep.conflicts.append(
                            f"{_label(tbl, uid, data)} was changed on another machine "
                            f"first; kept that version")
                    self._apply(conn, cur, rep)
                else:
                    conn.execute(done, (tbl, uid, rev))
                    rep.errors.append(f"hub refused {tbl} {uid[:12]}: {r.get('reason')}")

    # ── pull ──────────────────────────────────────────────────────────────

    def _pull(self, conn, rep: Report) -> None:
        cursor = int(self._meta(conn, "cursor", "0") or 0)
        epoch = self._meta(conn, "epoch")
        reconcile = False
        while True:
            resp = self.client.changes(cursor, PULL_PAGE)
            if resp.get("epoch") != epoch:
                if epoch:
                    # The hub was rebuilt (from the home server). Everything
                    # we remember about its seqs is for a hub that's gone.
                    conn.execute("DELETE FROM sync_row")
                    reconcile = True
                epoch = resp.get("epoch", "")
                self._set_meta(conn, "epoch", epoch)
                if cursor:
                    cursor = 0
                    self._set_meta(conn, "cursor", 0)
                    continue
            rep.head = max(rep.head, int(resp.get("head", 0)))
            changes = resp.get("changes", [])
            if changes:
                self._say(f"Receiving {len(changes)} change{'s' if len(changes) != 1 else ''}…")
            for ch in changes:
                if ch.get("origin") == self.me and self._seq(conn, ch["tbl"], ch["uid"]) >= ch["seq"]:
                    continue
                self._apply(conn, ch, rep)
                rep.pulled += 1
            if changes:
                cursor = int(changes[-1]["seq"])
                self._set_meta(conn, "cursor", cursor)
            if not resp.get("more"):
                break
        if reconcile:
            self._reconcile(conn)

    def _reconcile(self, conn) -> None:
        """Queue every local row the (new) hub doesn't know about."""
        conn.execute("BEGIN")
        for tbl in [s.name for s in tables.SPECS] + [LOG_SESSION, ANALYSIS_BOARD]:
            for (uid,) in conn.execute(
                    f"SELECT uid FROM {tbl} WHERE uid IS NOT NULL AND uid NOT IN "
                    f"(SELECT uid FROM sync_row WHERE tbl = ?)", (tbl,)).fetchall():
                self._enqueue(conn, tbl, uid, "upsert")
        conn.execute("DELETE FROM sync_doc")
        conn.execute("COMMIT")

    # ── apply one pulled change ───────────────────────────────────────────

    def _pend(self, conn, ch: dict, reason: str) -> None:
        conn.execute(
            """INSERT INTO sync_pending (tbl, uid, seq, op, data, reason) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (tbl, uid) DO UPDATE SET seq = excluded.seq, op = excluded.op,
                 data = excluded.data, reason = excluded.reason, tries = tries + 1""",
            (ch["tbl"], ch["uid"], ch["seq"], ch["op"], json.dumps(ch.get("data")), reason))

    def _apply(self, conn, ch: dict, rep: Report) -> None:
        tbl, uid, seq, op = ch.get("tbl"), ch.get("uid"), int(ch.get("seq", 0)), ch.get("op")
        data = ch.get("data") or {}
        if not tbl or not uid:
            return
        spec = tables.BY_NAME.get(tbl)
        drop = "DELETE FROM sync_outbox WHERE tbl = ? AND uid = ?"
        clear = "DELETE FROM sync_pending WHERE tbl = ? AND uid = ?"

        if spec is not None:
            with self._guarded(conn):
                conn.execute(drop, (tbl, uid))
                conn.execute("SAVEPOINT row")
                try:
                    if op == "delete":
                        tables.apply_delete(conn, spec, uid)
                    else:
                        replaced = tables.apply_upsert(conn, spec, uid, data)
                        if replaced:
                            # This machine had the same thing under another
                            # identity; retire that one at the hub too.
                            conn.execute("DELETE FROM sync_row WHERE tbl = ? AND uid = ?",
                                         (tbl, replaced))
                            self._enqueue(conn, tbl, replaced, "delete")
                    conn.execute("RELEASE row")
                    self._saw(conn, tbl, uid, seq)
                    conn.execute(clear, (tbl, uid))
                    rep.applied.add(tbl)
                except Pending as p:
                    conn.execute("ROLLBACK TO row")
                    conn.execute("RELEASE row")
                    self._pend(conn, ch, p.reason)
                except sqlite3.IntegrityError as e:
                    conn.execute("ROLLBACK TO row")
                    conn.execute("RELEASE row")
                    self._pend(conn, ch, f"clash: {e}")
            return

        if tbl == LOG_SESSION:
            self._apply_session(conn, ch, rep)
            return

        if tbl == docs.SETTING:
            with self._guarded(conn):
                conn.execute(drop, (tbl, uid))
                if op == "upsert":
                    digest = docs.apply_setting(uid, data)
                    if digest is not None:
                        self._set_doc(conn, tbl, uid, digest)
                        rep.applied.add(f"setting:{uid}")
                self._saw(conn, tbl, uid, seq)
            return

        if tbl == docs.FILE:
            if not self.prefs.get("sync_files", True) or docs.split(uid) is None:
                return
            with self._guarded(conn):
                conn.execute(drop, (tbl, uid))
                path = docs.file_path(uid)
                if op == "delete":
                    docs.remove_file(uid)
                    self._set_doc(conn, tbl, uid, None)
                    conn.execute(clear, (tbl, uid))
                    self._saw(conn, tbl, uid, seq)
                    rep.applied.add(f"file:{uid.partition('/')[0]}")
                elif path.is_file() and self.hasher.sha(path) == data.get("sha"):
                    self._set_doc(conn, tbl, uid, data["sha"])
                    conn.execute(clear, (tbl, uid))
                    self._saw(conn, tbl, uid, seq)
                else:
                    self._pend(conn, ch, "download")
            return

        if tbl in (tables.TBA_EVENT, tables.TBA_MATCH):
            with self._guarded(conn):
                if op == "delete":
                    conn.execute(f"DELETE FROM {tbl} WHERE uid = ?", (uid,))
                elif tbl == tables.TBA_EVENT:
                    conn.execute(
                        """INSERT INTO tba_event (uid, name, start_date, end_date, data)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT (uid) DO UPDATE SET name = excluded.name,
                             start_date = excluded.start_date, end_date = excluded.end_date,
                             data = excluded.data, updated_at = datetime('now')""",
                        (uid, data.get("name"), data.get("start_date"), data.get("end_date"),
                         json.dumps(data)))
                else:
                    conn.execute(
                        """INSERT INTO tba_match (uid, event_key, comp_level, match_key,
                                                  actual_ms, data)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT (uid) DO UPDATE SET event_key = excluded.event_key,
                             comp_level = excluded.comp_level, match_key = excluded.match_key,
                             actual_ms = excluded.actual_ms, data = excluded.data,
                             updated_at = datetime('now')""",
                        (uid, data.get("event_key") or uid.partition("_")[0],
                         data.get("comp_level"), tables.short_match_key(uid),
                         data.get("actual_time"), json.dumps(data)))
                self._saw(conn, tbl, uid, seq)
            rep.applied.add(tbl)
            return

        if tbl == ANALYSIS_BOARD:
            with self._guarded(conn):
                if op == "delete":
                    conn.execute("DELETE FROM analysis_board WHERE uid = ?", (uid,))
                else:
                    conn.execute(
                        """INSERT INTO analysis_board (uid, title, spec, session_uid)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT (uid) DO UPDATE SET title = excluded.title,
                             spec = excluded.spec, session_uid = excluded.session_uid,
                             created_at = datetime('now')""",
                        (uid, str(data.get("title", ""))[:200], json.dumps(data),
                         data.get("session_uid")))
                self._saw(conn, tbl, uid, seq)
            rep.applied.add(tbl)
            return
        # Unknown table: a newer build's data. Skipped, cursor moves on.

    def _apply_session(self, conn, ch: dict, rep: Report) -> None:
        uid, seq, data = ch["uid"], int(ch["seq"]), ch.get("data") or {}
        local = conn.execute("SELECT id FROM log_session WHERE uid = ?", (uid,)).fetchone()
        conn.execute("DELETE FROM sync_outbox WHERE tbl = ? AND uid = ?", (LOG_SESSION, uid))
        if ch["op"] == "delete":
            if local is not None:
                bundle.delete_session(self.db_path, local[0])
                rep.applied.add(LOG_SESSION)
            conn.execute("BEGIN")
            conn.execute("DELETE FROM sync_session_blob WHERE uid = ?", (uid,))
            conn.execute("DELETE FROM sync_pending WHERE tbl = ? AND uid = ?", (LOG_SESSION, uid))
            self._saw(conn, LOG_SESSION, uid, seq)
            conn.execute("COMMIT")
            return
        if local is not None:
            with self._guarded(conn):
                cols = [c for c in tables.SESSION_EDITABLE if c in data]
                if cols:
                    conn.execute(
                        f"UPDATE log_session SET {', '.join(f'{c} = ?' for c in cols)} WHERE uid = ?",
                        (*(data[c] for c in cols), uid))
                if data.get("bundle_sha"):
                    have = conn.execute("SELECT 1 FROM sync_session_blob WHERE uid = ?",
                                        (uid,)).fetchone()
                    if have is None:
                        self._save_blobs(conn, {k: data.get(k) for k in (
                            "bundle_sha", "bundle_bytes", "raw_sha", "raw_bytes")} | {"uid": uid})
                self._saw(conn, LOG_SESSION, uid, seq)
            rep.applied.add(LOG_SESSION)
            return
        if self.prefs.get("pull_logs", True) and data.get("bundle_sha"):
            self._pend(conn, ch, "bundle")

    # ── retry and fetch ───────────────────────────────────────────────────

    def _pending(self, conn, waiting_for_files: bool, limit: int = 1000) -> list[dict]:
        where = "IN" if waiting_for_files else "NOT IN"
        rows = conn.execute(
            f"SELECT tbl, uid, seq, op, data, reason FROM sync_pending "
            f"WHERE reason {where} ('bundle', 'download') ORDER BY seq LIMIT ?", (limit,)).fetchall()
        return [{"tbl": r[0], "uid": r[1], "seq": r[2], "op": r[3],
                 "data": json.loads(r[4]) if r[4] else None, "reason": r[5]} for r in rows]

    def _retry(self, conn, rep: Report) -> None:
        for ch in self._pending(conn, waiting_for_files=False):
            self._apply(conn, ch, rep)

    def _fetch(self, conn, rep: Report) -> None:
        for ch in self._pending(conn, waiting_for_files=True, limit=FETCH_PER_CYCLE):
            data = ch["data"] or {}
            try:
                if ch["reason"] == "bundle":
                    self._fetch_bundle(conn, ch, data, rep)
                else:
                    self._fetch_file(conn, ch, data, rep)
            except SyncError:
                raise
            except Exception as e:
                conn.execute("UPDATE sync_pending SET tries = tries + 1 WHERE tbl = ? AND uid = ?",
                             (ch["tbl"], ch["uid"]))
                rep.errors.append(f"couldn't land {ch['tbl']} {ch['uid'][:12]}: "
                                  f"{type(e).__name__}: {e}")

    def _fetch_bundle(self, conn, ch: dict, data: dict, rep: Report) -> None:
        sha = data["bundle_sha"]
        self._say(f"Downloading log {data.get('source_name', '')}…")
        tmp = codec.temp_path(bundle.SUFFIX, scratch_dir())
        try:
            self.client.get_blob(sha, tmp)
            rep.downloaded_bytes += tmp.stat().st_size
            bundle.import_bundle(self.db_path, tmp, scratch_dir())
        finally:
            tmp.unlink(missing_ok=True)
        uid = ch["uid"]
        with self._guarded(conn):
            cols = [c for c in tables.SESSION_EDITABLE if c in data]
            if cols:
                conn.execute(
                    f"UPDATE log_session SET {', '.join(f'{c} = ?' for c in cols)} WHERE uid = ?",
                    (*(data[c] for c in cols), uid))
            self._save_blobs(conn, {"uid": uid, **{k: data.get(k) for k in (
                "bundle_sha", "bundle_bytes", "raw_sha", "raw_bytes")}})
            self._saw(conn, LOG_SESSION, uid, ch["seq"])
            conn.execute("DELETE FROM sync_pending WHERE tbl = ? AND uid = ?", (LOG_SESSION, uid))
        rep.applied.add(LOG_SESSION)

    def _fetch_file(self, conn, ch: dict, data: dict, rep: Report) -> None:
        uid = ch["uid"]
        path = docs.file_path(uid)
        if path is None:
            conn.execute("DELETE FROM sync_pending WHERE tbl = ? AND uid = ?", (ch["tbl"], uid))
            return
        self._say(f"Downloading {path.name}…")
        if data.get("codec") == "zstd":
            tmp = codec.temp_path(".zst", scratch_dir())
            part = path.with_name(path.name + ".part")
            try:
                self.client.get_blob(data["blob"], tmp)
                rep.downloaded_bytes += tmp.stat().st_size
                got, _size = codec.decompress_file(tmp, part)
                if got != data["sha"]:
                    part.unlink(missing_ok=True)
                    raise SyncError(f"{path.name} didn't match its checksum after "
                                    "decompressing; it will be fetched again.")
                os.replace(part, path)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            self.client.get_blob(data["sha"], path)
            rep.downloaded_bytes += path.stat().st_size
        with self._guarded(conn):
            self._set_doc(conn, docs.FILE, uid, data["sha"])
            self._saw(conn, docs.FILE, uid, ch["seq"])
            conn.execute("DELETE FROM sync_pending WHERE tbl = ? AND uid = ?", (ch["tbl"], uid))
        rep.applied.add(f"file:{uid.partition('/')[0]}")


def _label(tbl: str, uid: str, data: dict | None) -> str:
    name = (data or {}).get("name") or (data or {}).get("text") or (data or {}).get("label")
    what = tbl.replace("_", " ")
    return f"{what} “{name}”" if name else f"{what} {uid[:12]}"
