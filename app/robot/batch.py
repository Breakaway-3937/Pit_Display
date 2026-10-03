"""
Batch import: point at a folder (a USB stick, the roboRIO's log dump) and every
robot log under it, in every subfolder, comes onto this machine and into the
database.

Brayden, 2026-10-03: "I sometimes only have 5 minutes with the robot and I
can't spend that time navigating around the system looking for the files I
haven't imported." So the order of work is:

1. **Copy first, everything** (`copy_in`). Every `.hoot` / `.wpilog` found is
   copied into this machine's log archive (`robot_logs/` in the data tree)
   before anything is imported, duplicates included. Copying is the only step
   that needs the drive or the robot; when it's done they can go. A file is
   written under a temporary name and renamed when complete, so a pulled drive
   never leaves a half file that looks whole. An identical copy already in the
   archive (same name and size) is reused, not copied twice.
2. **Duplicates are staged, not decided** (`Plan.duplicates`). A log that was
   imported before (here, from another path, or on another pit and synced:
   same file name and same size) is listed for the operator, who chooses per
   file: skip it, or re-import it (replacing the earlier session; only for a
   session imported on this machine, and only with the admin lock open,
   because deleting a session is admin-only).
3. **Then import, newest first** (`Plan.queue`), one file at a time, from the
   local copy. The newest log is the one the crew wants on the boards.

No Qt here; `robot_panel.py` runs it on worker threads.
"""

from __future__ import annotations

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


def archive_root() -> Path:
    """Where copied-in logs live. `PIT_LOG_ARCHIVE` overrides (tests)."""
    override = os.environ.get("PIT_LOG_ARCHIVE")
    root = Path(override) if override else paths.data_dir(ARCHIVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class Found:
    """One log under the chosen folder."""
    source: Path                  # on the drive
    rel: Path                     # its path under the chosen folder
    size: int
    dest: Path | None = None      # its copy on this machine
    copied: bool = False          # copied this run (False: an identical copy was there)
    duplicate_of: dict | None = None   # the earlier session, when there is one
    twin_of: "Found | None" = None     # the same log twice in this folder
    started: str = ""

    @property
    def name(self) -> str:
        return self.source.name


@dataclass
class Plan:
    root: Path
    found: list[Found] = field(default_factory=list)
    empty: list[Path] = field(default_factory=list)      # 0-byte files: nothing to import

    @property
    def duplicates(self) -> list[Found]:
        return [f for f in self.found if f.duplicate_of is not None and f.twin_of is None]

    @property
    def queue(self) -> list[Found]:
        """What imports without asking: new logs, newest first."""
        new = [f for f in self.found if f.duplicate_of is None and f.twin_of is None]
        return sorted(new, key=lambda f: f.started or "", reverse=True)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.found)


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
            meta = parse_log_name(fn)
            plan.found.append(Found(source=p, rel=p.relative_to(root), size=size,
                                    started=meta.started or ""))
    return plan


def mark_duplicates(plan: Plan, db_path: Path) -> Plan:
    """Mark logs imported before (same name and size, any machine) and logs
    that appear twice in this folder."""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        seen: dict[tuple[str, int], Found] = {}
        for f in plan.found:
            key = (f.name, f.size)
            if key in seen:
                f.twin_of = seen[key]
                continue
            seen[key] = f
            row = conn.execute(
                """SELECT id, source_name, source_file, started_at, imported_at,
                          origin_name
                   FROM log_session WHERE source_name = ? AND source_bytes = ?
                   ORDER BY id LIMIT 1""", key).fetchone()
            if row is not None:
                f.duplicate_of = dict(row)
    finally:
        conn.close()
    return plan


def is_local(session: dict) -> bool:
    """Imported on this machine (so this machine may replace it)."""
    return not str(session.get("source_file") or "").startswith("sync:")


class CopyError(Exception):
    """Operator-actionable: not enough room, or the drive went away."""


def copy_in(plan: Plan, progress: Callable[[str, float], None] | None = None,
            cancelled: Callable[[], bool] = lambda: False) -> Plan:
    """Copy every log in the plan into the archive (see the module note)."""
    dest_root = archive_root() / _safe(plan.root.name or "logs")
    need = sum(f.size for f in plan.found
               if not _same(dest_root / f.rel, f.size))
    free = shutil.disk_usage(archive_root()).free
    if need > free - 512 * 1024 * 1024:          # leave half a GB for the database
        raise CopyError(
            f"Not enough room on this machine: the logs need {need / 1e9:.1f} GB "
            f"and {free / 1e9:.1f} GB is free. Free some space and run it again; "
            f"nothing was imported.")
    done = 0
    total = max(1, plan.total_bytes)
    for i, f in enumerate(plan.found, 1):
        if cancelled():
            break
        dest = dest_root / f.rel
        f.dest = dest
        if _same(dest, f.size):
            done += f.size
            continue
        if progress:
            progress(f"Copying {i} of {len(plan.found)}: {f.rel}", done / total)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with open(f.source, "rb") as src, open(part, "wb") as out:
                while True:
                    chunk = src.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(f"Copying {i} of {len(plan.found)}: {f.rel}", done / total)
            if part.stat().st_size != f.size:
                raise CopyError(f"{f.rel} copied short; the drive may have been pulled.")
            os.replace(part, dest)
            f.copied = True
        except OSError as err:
            part.unlink(missing_ok=True)
            raise CopyError(f"Couldn't copy {f.rel}: {err}. The logs copied so far "
                            f"are safe on this machine.") from err
    for f in plan.found:
        if f.dest is None:
            f.dest = dest_root / f.rel
    return plan


def _same(p: Path, size: int) -> bool:
    try:
        return p.stat().st_size == size
    except OSError:
        return False


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_. " else "_" for c in name).strip() or "logs"
