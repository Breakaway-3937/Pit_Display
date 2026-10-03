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

import base64
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app import paths
from app.db.sync import bundle, codec, docs, tables, transfer
from app.db.sync.client import HubClient, SyncError, sha256_file
from app.db.sync.tables import LOG_SESSION, ANALYSIS_BOARD, Pending

PUSH_BATCH = 100
PULL_PAGE = 500
FETCH_PER_CYCLE = 3                 # robot-log bundles
# Team files inside the night window: keep downloading until this much of a
# cycle has gone, then let the next cycle carry on (one in flight finishes).
FILE_BUDGET_S = 240
# Checking whether the hub still holds bytes this pit lacks: HEADs per cycle.
REQUEST_CHECKS = 200
# A manifest goes out after each night window closes, and at least this often.
MANIFEST_MAX_AGE = timedelta(hours=24)
# Past this a manifest's lists travel zstd'd ({"zstd": …}), under the hub's
# 256 KB row cap.
MANIFEST_PACK_AT = 200_000
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
    # The night window (R8): team-file uploads and downloads held for it,
    # and this pit's requests home hasn't answered yet.
    held_uploads: int = 0
    held_downloads: int = 0
    open_requests: int = 0

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
        # "Sync files now": the next cycle moves team files outside the window.
        self.force_files = False
        # Blobs the hub was seen to hold this session (no need to ask again).
        self._present: set[str] = set()

    def _files_open(self) -> bool:
        return self.force_files or transfer.is_open()

    def _music_on(self) -> bool:
        return bool(self.prefs.get("sync_files", True) and self.prefs.get("sync_music", True))

    @staticmethod
    def _team_deleted(conn, uid: str):
        """The deleted track behind music `uid`, or None."""
        parts = docs.split(uid)
        if parts is None or parts[0] != docs.MUSIC:
            return None
        return conn.execute(
            "SELECT sha FROM tracks WHERE team_deleted = 1 AND sha LIKE ? LIMIT 1",
            (parts[1][:16] + "%",)).fetchone()

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
            # The second push sends what this cycle queued: requests, their
            # done marks, the manifest. Same cycle, not a minute later.
            for step in (self._scan, self._push, self._catch_up, self._pull, self._retry,
                         self._fetch, self._status, self._origins, self._manifest,
                         self._push):
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
            rep.open_requests = conn.execute(
                "SELECT COUNT(*) FROM sync_blob_request WHERE done_at IS NULL").fetchone()[0]
            rep.held_uploads = conn.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE tbl = 'file' AND op = 'upsert'"
            ).fetchone()[0] if not self._files_open() else 0
            rep.held_downloads = conn.execute(
                "SELECT COUNT(*) FROM sync_pending WHERE reason = 'download'"
            ).fetchone()[0] if not self._files_open() else 0
        finally:
            self.force_files = False
            conn.close()
        rep.seconds = round(time.monotonic() - t0, 2)
        return rep

    def _status(self, conn, rep: Report) -> None:
        rep.machines = list(self.client.status().get("machines", []))

    def _origins(self, conn, rep: Report) -> None:
        """Name the machine each pulled session was imported on.

        The importer is the only machine that uploads a session's bundle, so
        the hub's `origin` on that blob is the session's root. Asked only while
        a pulled session still lacks one; names follow renames every cycle.
        Local bookkeeping: `origin` columns aren't watched, nothing is pushed.
        """
        missing = conn.execute(
            """SELECT ls.id, b.bundle_sha FROM log_session ls
               JOIN sync_session_blob b ON b.uid = ls.uid
               WHERE ls.source_file LIKE 'sync:%' AND ls.origin IS NULL
                 AND b.bundle_sha IS NOT NULL""").fetchall()
        changed = 0
        if missing:
            by_sha = {b["sha"]: b.get("origin") for b in self.client.blobs()}
            for sid, sha in missing:
                if by_sha.get(sha):
                    changed += conn.execute("UPDATE log_session SET origin = ? WHERE id = ?",
                                            (by_sha[sha], sid)).rowcount
        for m in rep.machines:
            if m.get("id"):
                name = m.get("name") or m["id"]
                changed += conn.execute(
                    "UPDATE log_session SET origin_name = ? WHERE origin = ? "
                    "AND origin_name IS NOT ?", (name, m["id"], name)).rowcount
        if changed:
            rep.applied.add(LOG_SESSION)

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
        local = self.hasher.local_files(conn if self._music_on() else None)
        for uid, (_path, sha, _size) in local.items():
            if known.get((docs.FILE, uid)) != sha and uid not in waiting:
                self._enqueue(conn, docs.FILE, uid, "upsert")
        if self._music_on():
            # An admin's "delete for the team": sent as a mark, never a
            # delete. Home approves the real deletion (R8).
            for sha, path in conn.execute(
                    "SELECT sha, path FROM tracks WHERE team_deleted = 1 AND sha IS NOT NULL"):
                uid = f"{docs.MUSIC}/{docs.music_name(sha, Path(path).name)}"
                if docs.split(uid) is not None and known.get((docs.FILE, uid)) != "deleted":
                    self._enqueue(conn, docs.FILE, uid, "upsert")
        for (tbl, uid) in known:
            # A song gone from one disk isn't a team delete: it's missing
            # there, which the nightly verdict reports.
            if tbl == docs.FILE and uid not in local and uid not in waiting \
                    and not uid.startswith(docs.MUSIC + "/") \
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
        if tbl == tables.BLOB_REQUEST:
            row = conn.execute(
                "SELECT sha, file_uid, file_sha, bytes, codec, requested_at, done_at "
                "FROM sync_blob_request WHERE sha = ?", (uid.partition(":")[2],)).fetchone()
            return dict(row) if row else None
        if tbl == tables.MACHINE_MANIFEST:
            return self._manifest_data(conn)
        if tbl == docs.FILE and (gone := self._team_deleted(conn, uid)) is not None:
            return {"sha": gone[0], "name": docs.split(uid)[1], "deleted": True,
                    "deleted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "deleted_by": self.me}
        if tbl == docs.FILE:
            path = docs.file_path(uid)
            if uid.startswith(docs.MUSIC + "/") and (path is None or not path.is_file()):
                path = docs.music_source(conn, uid)
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
                if e["tbl"] == docs.FILE and e["op"] != "delete" and not self._files_open() \
                        and self._team_deleted(conn, e["uid"]) is None:
                    continue        # bytes wait for the night window; the row with them
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
                        self._set_doc(conn, tbl, uid, None if not data else
                                      "deleted" if data.get("deleted") else data["sha"])
                    elif tbl == tables.MACHINE_MANIFEST and data:
                        self._set_meta(conn, "manifest_at", data["at"])
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

    # ── catch-up: tables this build learned ───────────────────────────────

    def _catch_up(self, conn, rep: Report) -> None:
        """Fetch, once, the rows of tables an older build skipped.

        The pull moves its cursor past a table it doesn't know, so after an
        upgrade those rows would never arrive. `known_tables` records what the
        build that last pulled could apply; for anything new, scan the feed
        from 0 to today's head applying only those tables (the feed holds each
        row's latest version, so this is every live row once). Resumable: the
        scan's position is saved after every page.
        """
        recorded = json.loads(self._meta(conn, "known_tables", "null") or "null")
        known = set(recorded if recorded is not None else tables.CATCH_UP_BASELINE)
        cursor = int(self._meta(conn, "cursor", "0") or 0)
        state = json.loads(self._meta(conn, "catch_up", "null") or "null")
        if state is None:
            if cursor == 0:
                # Never pulled (or the hub was rebuilt): the pull gets it all.
                self._set_meta(conn, "known_tables", json.dumps(sorted(tables.APPLIED)))
                return
            new = sorted(set(tables.APPLIED) - known)
            if not new:
                return
            state = {"tables": new, "at": 0, "until": cursor}
        want = set(state["tables"])
        at, until = int(state["at"]), int(state["until"])
        while at < until:
            changes = self.client.changes(at, PULL_PAGE).get("changes", [])
            if not changes:
                break
            for ch in changes:
                if int(ch["seq"]) > until:
                    break
                if ch["tbl"] in want:
                    self._apply(conn, ch, rep)
            at = min(until, int(changes[-1]["seq"]))
            self._set_meta(conn, "catch_up", json.dumps({**state, "at": at}))
        self._set_meta(conn, "catch_up", "null")
        self._set_meta(conn, "known_tables", json.dumps(sorted(set(state["tables"]) | known)))

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

        if tbl in (tables.BLOB_REQUEST, tables.MACHINE_MANIFEST):
            return          # every pit's requests and manifests are home's to read

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
            music = uid.startswith(docs.MUSIC + "/")
            if music and not self._music_on():
                return
            with self._guarded(conn):
                conn.execute(drop, (tbl, uid))
                path = docs.file_path(uid)
                if music and (op == "delete" or data.get("deleted")):
                    # A mark hides the song everywhere and keeps the file; a
                    # delete (home approved it) removes the team folder's
                    # copy. A file in someone's own library is never touched.
                    if op == "delete":
                        docs.remove_file(uid)
                    conn.execute("UPDATE tracks SET team_deleted = 1 WHERE sha LIKE ?",
                                 (docs.split(uid)[1][:16] + "%",))
                    self._set_doc(conn, tbl, uid, None if op == "delete" else "deleted")
                    conn.execute(clear, (tbl, uid))
                    self._saw(conn, tbl, uid, seq)
                    rep.applied.add("file:music")
                    return
                if music and not path.is_file():
                    path = docs.music_source(conn, uid) or path
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

        if tbl in tables.PULLED_ONLY:
            with self._guarded(conn):
                if op == "delete":
                    conn.execute(f"DELETE FROM {tbl} WHERE uid = ?", (uid,))
                elif tbl == tables.HOME_DATASET:
                    conn.execute(
                        """INSERT INTO home_dataset (uid, title, category, sort, data)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT (uid) DO UPDATE SET title = excluded.title,
                             category = excluded.category, sort = excluded.sort,
                             data = excluded.data, updated_at = datetime('now')""",
                        (uid, data.get("title"), data.get("category"), data.get("sort"),
                         json.dumps(data)))
                elif tbl == tables.SYNC_VERDICT:
                    conn.execute(
                        """INSERT INTO sync_verdict (uid, data) VALUES (?, ?)
                           ON CONFLICT (uid) DO UPDATE SET data = excluded.data,
                             updated_at = datetime('now')""", (uid, json.dumps(data)))
                elif tbl in (tables.TBA_TEAM, tables.TBA_RIVAL):
                    number = str(data.get("team_number") or uid.removeprefix("frc"))
                    if tbl == tables.TBA_TEAM:
                        conn.execute(
                            """INSERT INTO tba_team (uid, team_number, nickname, data)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT (uid) DO UPDATE SET team_number = excluded.team_number,
                                 nickname = excluded.nickname, data = excluded.data,
                                 updated_at = datetime('now')""",
                            (uid, number, data.get("nickname"), json.dumps(data)))
                    else:
                        conn.execute(
                            """INSERT INTO tba_rival (uid, team_number, data) VALUES (?, ?, ?)
                               ON CONFLICT (uid) DO UPDATE SET team_number = excluded.team_number,
                                 data = excluded.data, updated_at = datetime('now')""",
                            (uid, number, json.dumps(data)))
                elif tbl == tables.TBA_FACT:
                    conn.execute(
                        """INSERT INTO tba_fact (uid, category, team_number, text, sort, data)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT (uid) DO UPDATE SET category = excluded.category,
                             team_number = excluded.team_number, text = excluded.text,
                             sort = excluded.sort, data = excluded.data,
                             updated_at = datetime('now')""",
                        (uid, data.get("category"), data.get("team_number"), data.get("text"),
                         data.get("sort"), json.dumps(data)))
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

    def _waiting(self, conn, reason: str, limit: int) -> list[dict]:
        rows = conn.execute(
            "SELECT tbl, uid, seq, op, data, reason FROM sync_pending WHERE reason = ? "
            "ORDER BY seq LIMIT ?", (reason, limit)).fetchall()
        return [{"tbl": r[0], "uid": r[1], "seq": r[2], "op": r[3],
                 "data": json.loads(r[4]) if r[4] else None, "reason": r[5]} for r in rows]

    def _fetch(self, conn, rep: Report) -> None:
        """Robot-log bundles any time, as before. Team files (R8): ask for
        bytes the hub no longer holds at any time; move bytes only in the
        night window (or after "Sync files now")."""
        for ch in self._waiting(conn, "bundle", FETCH_PER_CYCLE):
            self._land(conn, ch, rep, self._fetch_bundle)
        self._request_missing(conn, rep)
        if not self._files_open():
            return
        t0 = time.monotonic()
        for ch in self._waiting(conn, "download", 10_000):
            if time.monotonic() - t0 > FILE_BUDGET_S:
                break
            blob = self._blob_of(ch["data"] or {})
            if blob not in self._present and not self.client.has_blob(blob):
                self._request(conn, blob, ch["uid"], ch["data"] or {})
                continue
            if self._land(conn, ch, rep, self._fetch_file):
                self._present.discard(blob)
                if conn.execute("UPDATE sync_blob_request SET done_at = datetime('now') "
                                "WHERE sha = ? AND done_at IS NULL", (blob,)).rowcount:
                    self._enqueue(conn, tables.BLOB_REQUEST, f"{self.me}:{blob}", "upsert")

    def _land(self, conn, ch: dict, rep: Report, fetch) -> bool:
        try:
            fetch(conn, ch, ch["data"] or {}, rep)
            return True
        except SyncError as e:
            if e.status == 404 and ch["reason"] == "download":
                # Evicted between the check and the download: ask for it.
                self._request(conn, self._blob_of(ch["data"] or {}), ch["uid"], ch["data"] or {})
                return False
            raise
        except Exception as e:
            conn.execute("UPDATE sync_pending SET tries = tries + 1 WHERE tbl = ? AND uid = ?",
                         (ch["tbl"], ch["uid"]))
            rep.errors.append(f"couldn't land {ch['tbl']} {ch['uid'][:12]}: "
                              f"{type(e).__name__}: {e}")
            return False

    @staticmethod
    def _blob_of(data: dict) -> str:
        return data.get("blob") or data.get("sha") or ""

    def _request_missing(self, conn, rep: Report) -> None:
        """Ask home for team-file bytes the hub no longer holds. Any time of
        day: home answers as requests come, so the bytes are there when the
        window opens. One request per blob; asked again only once answered."""
        asked = {r[0] for r in conn.execute(
            "SELECT sha FROM sync_blob_request WHERE done_at IS NULL")}
        checks = 0
        for ch in self._waiting(conn, "download", 10_000):
            blob = self._blob_of(ch["data"] or {})
            if not blob or blob in asked or blob in self._present:
                continue
            if checks >= REQUEST_CHECKS:
                break
            checks += 1
            if self.client.has_blob(blob):
                self._present.add(blob)
            else:
                self._request(conn, blob, ch["uid"], ch["data"] or {})
                asked.add(blob)

    def _request(self, conn, blob: str, file_uid: str, data: dict) -> None:
        self._present.discard(blob)
        conn.execute(
            """INSERT INTO sync_blob_request (sha, file_uid, file_sha, bytes, codec)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (sha) DO UPDATE SET file_uid = excluded.file_uid,
                 requested_at = datetime('now'), done_at = NULL
               WHERE sync_blob_request.done_at IS NOT NULL""",
            (blob, file_uid, data.get("sha"), data.get("blob_bytes") or data.get("bytes"),
             data.get("codec") or "none"))
        self._enqueue(conn, tables.BLOB_REQUEST, f"{self.me}:{blob}", "upsert")

    # ── nightly manifest (R8) ─────────────────────────────────────────────

    def _manifest(self, conn, rep: Report) -> None:
        """Queue this machine's manifest when it's due: after each night window
        closes (so the verdict reflects that night), and at least daily."""
        last = self._meta(conn, "manifest_at")
        if conn.execute("SELECT 1 FROM sync_outbox WHERE tbl = ?",
                        (tables.MACHINE_MANIFEST,)).fetchone():
            return
        now = datetime.now().astimezone()
        due = not last
        if last:
            sent = datetime.fromisoformat(last)
            due = now - sent > MANIFEST_MAX_AGE or (
                transfer.load()["enforce"]
                and sent < transfer.last_close(now.replace(tzinfo=None)).astimezone())
        if due:
            self._enqueue(conn, tables.MACHINE_MANIFEST, self.me, "upsert")

    def _manifest_data(self, conn) -> dict:
        from app import version
        files = {uid: sha for uid, (_p, sha, _n) in
                 self.hasher.local_files(conn if self._music_on() else None).items()}
        data = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "build": version.VERSION, "files": files,
                "playlists": playlist_hashes(conn)}
        if len(json.dumps(data)) > MANIFEST_PACK_AT:
            packed = json.dumps({"files": data.pop("files"),
                                 "playlists": data.pop("playlists")}).encode("utf-8")
            data["packed"] = {"zstd": base64.b64encode(
                codec.zstd.compress(packed, 19)).decode("ascii")}
        return data

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


def playlist_hashes(conn) -> dict[str, str]:
    """playlist uid → sha256 of `{"app_mode", "name", "tracks": [track_key, …]}`
    (items by position, ties by the item's uid, which every machine shares;
    JSON with sorted keys and `(",", ":")` separators). Home computes the
    same from `sync.row_state` (R8)."""
    out = {}
    for pid, puid, name, mode in conn.execute(
            "SELECT id, uid, name, app_mode FROM playlists WHERE uid IS NOT NULL"):
        keys = [tables._track_key(conn, r[0]) for r in conn.execute(
            "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position, uid",
            (pid,))]
        body = json.dumps({"name": name, "app_mode": mode, "tracks": keys},
                          sort_keys=True, separators=(",", ":"))
        out[puid] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return out

