"""
Batch import: point at a folder (a USB stick, the roboRIO's log dump) and every
robot log under it, in every subfolder, comes onto this machine and into the
database.

Brayden, 2026-10-03: "I sometimes only have 5 minutes with the robot and I
can't spend that time navigating around the system looking for the files I
haven't imported." So the order of work is:

1. **Copy first, everything** (`copy_in`). Every `.hoot` / `.wpilog` found is
   copied into this machine's log archive (`robot_logs/` in the data tree)
   before anything is imported. Copying is the only step that needs the drive
   or the robot. A file is written under a temporary name and renamed when
   complete, so a pulled drive never leaves a half file that looks whole.
2. **Identity is the contents, never the name** (Brayden, same day). While a
   file streams in it is fingerprinted (`Fingerprint`): SHA-256 of every byte,
   SHA-256 of the first 64 KiB (the format header and the start of the
   recording), and the header itself as text. A `.wpilog` without a WPILOG
   header isn't a log and is set aside. Copies are stored by content
   (`robot_logs/<sha>/name`), so the same log twice is one file.
3. **Duplicates are staged, not decided** (`Plan.duplicates`):
   * *exact*: every byte matches a session already imported (here or synced
     from another pit). Skipped unless the operator ticks re-import.
   * *same recording*: the start matches but the length differs, a longer or
     shorter copy of a log imported before. Listed too; re-import is
     pre-ticked when the new copy is longer.
   Re-importing replaces the earlier session, so it's offered only for a
   session imported on this machine and only with the admin lock open.
4. **Import newest first** (`Plan.queue`), one file at a time, from the copy.
5. **Auto-delete the copy** once its data is in the database (`discard`):
   after a successful import, or for an exact duplicate that was skipped. A
   failed import or an un-chosen different copy is kept. When team sync
   uploads original logs (`upload_raw`), the copy waits for that upload and
   the sync engine deletes it (`release_after_upload`).

Sessions imported before fingerprints existed are fingerprinted when their
file is still on disk (`backfill`); one whose file is gone can't be compared.

No Qt here; `robot_panel.py` runs it on worker threads.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app import paths
from app.robot.naming import parse_log_name

LOG_SUFFIXES = (".hoot", ".wpilog")
ARCHIVE_DIR = "robot_logs"
HEAD_BYTES = 64 * 1024
_CHUNK = 8 * 1024 * 1024


def archive_root() -> Path:
    """Where copied-in logs wait to be imported. `PIT_LOG_ARCHIVE` overrides (tests)."""
    override = os.environ.get("PIT_LOG_ARCHIVE")
    root = Path(override) if override else paths.data_dir(ARCHIVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Fingerprints ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Fingerprint:
    sha256: str            # every byte
    head_sha256: str       # the first HEAD_BYTES: header + start of the recording
    header: str            # the header as text
    size: int
    valid: bool = True     # the header is the format the suffix claims
    problem: str = ""


class _Hasher:
    """Feeds a file's bytes, in order, into the full and head digests."""

    def __init__(self):
        self.full = hashlib.sha256()
        self.head = hashlib.sha256()
        self.first = bytearray()
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self.full.update(chunk)
        if len(self.first) < HEAD_BYTES:
            take = chunk[:HEAD_BYTES - len(self.first)]
            self.first += take
            self.head.update(take)
        self.size += len(chunk)

    def result(self, suffix: str) -> Fingerprint:
        header, valid, problem = describe_header(bytes(self.first[:512]), suffix)
        return Fingerprint(self.full.hexdigest(), self.head.hexdigest(), header,
                           self.size, valid, problem)


def describe_header(start: bytes, suffix: str) -> tuple[str, bool, str]:
    """(the header as text, whether it's the format `suffix` claims, why not)."""
    suffix = suffix.lower()
    if suffix == ".wpilog":
        if not start.startswith(b"WPILOG") or len(start) < 12:
            return "", False, "not a WPILib log (no WPILOG header)"
        minor, major = start[6], start[7]
        n = int.from_bytes(start[8:12], "little")
        extra = start[12:12 + n].decode("utf-8", "replace").strip()
        return f"WPILOG {major}.{minor}" + (f" {extra}" if extra else ""), True, ""
    if suffix == ".hoot":
        text = start.split(b"\0", 1)[0].decode("ascii", "replace").strip()
        if not text or not text.isprintable():
            return "", False, "not a Phoenix hoot (no bus name in its header)"
        return text, True, ""
    return "", False, "not a robot log"


def fingerprint(path: Path) -> Fingerprint:
    """Read a whole file once and fingerprint it."""
    h = _Hasher()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.result(path.suffix)


# ── The plan ─────────────────────────────────────────────────────────────

@dataclass
class Found:
    """One log under the chosen folder."""
    source: Path                  # on the drive
    rel: Path                     # its path under the chosen folder
    size: int
    started: str = ""
    dest: Path | None = None      # its copy on this machine
    fp: Fingerprint | None = None
    match: str = ""               # "" | "exact" | "same_recording"
    duplicate_of: dict | None = None   # the earlier session
    twin_of: "Found | None" = None     # the same bytes earlier in this folder

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def longer(self) -> bool:
        return bool(self.duplicate_of) and self.size > (self.duplicate_of.get("source_bytes") or 0)


@dataclass
class Plan:
    root: Path
    found: list[Found] = field(default_factory=list)
    empty: list[Path] = field(default_factory=list)      # 0-byte files: nothing to import

    @property
    def invalid(self) -> list[Found]:
        return [f for f in self.found if f.fp is not None and not f.fp.valid]

    @property
    def duplicates(self) -> list[Found]:
        return [f for f in self._usable() if f.duplicate_of is not None]

    @property
    def queue(self) -> list[Found]:
        """What imports without asking: logs not seen before, newest first."""
        new = [f for f in self._usable() if f.duplicate_of is None]
        return sorted(new, key=lambda f: f.started or "", reverse=True)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.found)

    def _usable(self) -> list[Found]:
        return [f for f in self.found
                if f.twin_of is None and f.fp is not None and f.fp.valid]


def find_logs(root: Path) -> Plan:
    """Every robot log under `root`, every subfolder. Skips macOS's `._`
    metadata twins (same suffix, not logs) and hidden folders."""
    root = Path(root)
    plan = Plan(root=root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.startswith(".") or not fn.lower().endswith(LOG_SUFFIXES):
                continue
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size == 0:
                plan.empty.append(p)
                continue
            plan.found.append(Found(source=p, rel=p.relative_to(root), size=size,
                                    started=parse_log_name(fn).started or ""))
    return plan


class CopyError(Exception):
    """Operator-actionable: not enough room, or the drive went away."""


def copy_in(plan: Plan, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> Plan:
    """Copy and fingerprint every log in the plan (see the module note)."""
    root = archive_root()
    incoming = root / ".incoming"
    incoming.mkdir(exist_ok=True)
    free = shutil.disk_usage(root).free
    if plan.total_bytes > free - 512 * 1024 * 1024:   # leave half a GB for the database
        raise CopyError(
            f"Not enough room on this machine: the logs need {plan.total_bytes / 1e9:.1f} GB "
            f"and {free / 1e9:.1f} GB is free. Free some space and run it again; "
            f"nothing was imported.")
    done = 0
    total = max(1, plan.total_bytes)
    by_sha: dict[str, Found] = {}
    for i, f in enumerate(plan.found, 1):
        if cancelled():
            break
        say = f"Copying {i} of {len(plan.found)}: {f.rel}"
        if progress:
            progress(say, done / total)
        part = incoming / f"{i}-{f.name}.part"
        h = _Hasher()
        try:
            with open(f.source, "rb") as src, open(part, "wb") as out:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(say, done / total)
            if part.stat().st_size != f.size:
                raise CopyError(f"{f.rel} copied short; the drive may have been pulled.")
        except OSError as err:
            part.unlink(missing_ok=True)
            raise CopyError(f"Couldn't copy {f.rel}: {err}. The logs copied so far "
                            f"are safe on this machine.") from err
        f.fp = h.result(f.source.suffix)
        if not f.fp.valid:
            part.unlink(missing_ok=True)            # not a log: nothing to keep
            continue
        f.dest = root / f.fp.sha256[:16] / f.name
        if f.fp.sha256 in by_sha:
            f.twin_of = by_sha[f.fp.sha256]
            f.dest = f.twin_of.dest
            part.unlink(missing_ok=True)
            continue
        by_sha[f.fp.sha256] = f
        if f.dest.is_file() and f.dest.stat().st_size == f.size:
            part.unlink(missing_ok=True)            # this exact log is already waiting here
        else:
            f.dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, f.dest)
    return plan


def mark_duplicates(plan: Plan, db_path: Path) -> Plan:
    """Compare every copied log, by content, with the sessions already
    imported (see the module note). Call after `copy_in`."""
    backfill(db_path)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    cols = ("id, source_name, source_file, source_bytes, started_at, imported_at, "
            "origin_name, source_sha256")
    try:
        for f in plan._usable():
            row = conn.execute(f"SELECT {cols} FROM log_session WHERE source_sha256 = ? "
                               "ORDER BY id LIMIT 1", (f.fp.sha256,)).fetchone()
            if row is not None:
                f.match, f.duplicate_of = "exact", dict(row)
                continue
            row = conn.execute(f"SELECT {cols} FROM log_session WHERE source_head_sha256 = ? "
                               "ORDER BY source_bytes DESC LIMIT 1", (f.fp.head_sha256,)).fetchone()
            if row is not None:
                f.match, f.duplicate_of = "same_recording", dict(row)
    finally:
        conn.close()
    return plan


def backfill(db_path: Path) -> int:
    """Fingerprint sessions imported before fingerprints existed, where the
    file is still on this machine. Returns how many were filled."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    filled = 0
    try:
        rows = conn.execute(
            "SELECT id, archive_path, source_file FROM log_session "
            "WHERE source_sha256 IS NULL").fetchall()
        for sid, archive, source in rows:
            for cand in (archive, source):
                if cand and not str(cand).startswith("sync:") and Path(cand).is_file():
                    fp = fingerprint(Path(cand))
                    conn.execute("UPDATE log_session SET source_sha256 = ?, "
                                 "source_head_sha256 = ?, source_header = ? WHERE id = ?",
                                 (fp.sha256, fp.head_sha256, fp.header, sid))
                    filled += 1
                    break
        conn.commit()
    finally:
        conn.close()
    return filled


def is_local(session: dict) -> bool:
    """Imported on this machine (so this machine may replace it)."""
    return not str(session.get("source_file") or "").startswith("sync:")


# ── Auto-delete ──────────────────────────────────────────────────────────

def keep_for_upload() -> bool:
    """Team sync uploads original logs: a copy must wait for that upload."""
    try:
        from app.db.sync import settings as sync_settings
        prefs = sync_settings.load()
        return bool(prefs.get("enabled") and prefs.get("upload_raw"))
    except Exception:
        return False


def in_archive(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(archive_root().resolve())
        return True
    except ValueError:
        return False


def discard(path: str | Path | None, db_path: Path | None = None) -> bool:
    """Delete one copied-in log (only ever inside the archive) and its empty
    folder; clear `archive_path` on any session that pointed at it."""
    if not in_archive(path):
        return False
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
        if p.parent != archive_root() and not any(p.parent.iterdir()):
            p.parent.rmdir()
    except OSError:
        return False
    if db_path is not None:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        try:
            conn.execute("UPDATE log_session SET archive_path = NULL WHERE archive_path = ?",
                         (str(p),))
            conn.commit()
        finally:
            conn.close()
    return True


def release_after_upload(path: str | Path | None) -> bool:
    """The sync engine uploaded this original; a copied-in one can go now.
    The caller clears `archive_path` on its own connection."""
    return discard(path) if in_archive(path) else False
