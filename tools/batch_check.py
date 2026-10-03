"""
Batch import from a folder (app/robot/batch.py and the Robot Logs panel). Exit 0/1.

    uv run tools/batch_check.py

Builds a fake log drive from TEST_LOGS/ shaped like a real one (logs at the
top, a roboRIO dump folder, a session folder, macOS `._` twins, an empty log,
the same log in two folders, a fake .wpilog), then on a fresh scratch install:

* finds every log in every subfolder and nothing else;
* copies all of them onto the machine before importing anything, by content;
* identity is the contents, never the name: a renamed copy is a duplicate, a
  different file with the same name isn't, a shorter copy of a recording is
  "the same recording";
* imports the new ones newest first, recording fingerprints;
* deletes each copy once it's in the database (and skipped duplicates'),
  keeps it while team sync still has to upload the original;
* the duplicates pop-up offers re-import only for local sessions with the admin
  lock open, and pre-ticks a longer copy;
* the panel runs the whole thing on its worker threads.

Never this Mac's data: PIT_DISPLAY_DATA and PIT_LOG_ARCHIVE point at scratch.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(name)
    return ok


def waiting(arch: Path) -> list[str]:
    """Logs copied onto the machine and not yet deleted."""
    return sorted(p.name for p in arch.rglob("*") if p.is_file() and ".incoming" not in p.parts)


def main() -> int:
    use_utf8()
    tmp = Path(tempfile.mkdtemp(prefix="batch-check-"))
    arch = tmp / "archive"
    os.environ.update(PIT_DISPLAY_DATA=str(tmp / "pit"), PIT_LOG_ARCHIVE=str(arch),
                      PIT_LOG_SCRATCH=str(tmp / "owlet"), QT_QPA_PLATFORM="offscreen",
                      PIT_LEDS_FAKE="1", PIT_NEXUS_QUIET="1", PIT_SYNC_QUIET="1")
    (tmp / "pit" / "data").mkdir(parents=True)
    logs = ROOT / "TEST_LOGS"
    A = "akit_26-08-17_02-43-54.wpilog"
    B = "akit_26-08-17_02-59-21.wpilog"
    HOOT = "rio_2026-08-17_03-42-54.hoot"
    drive = tmp / "New_Logs"
    (drive / "2024-12-18_14-03-51").mkdir(parents=True)
    (drive / "2026-08-17_03-42-50").mkdir()
    shutil.copy2(logs / A, drive)
    shutil.copy2(logs / B, drive)
    shutil.copy2(logs / HOOT, drive / "2024-12-18_14-03-51")
    shutil.copy2(logs / B, drive / "2026-08-17_03-42-50")                  # same bytes twice
    (drive / "akit_9b0c7ffeea6cb854.wpilog").write_bytes(b"")             # empty
    (drive / f"._{A}").write_bytes(b"\0" * 4096)                         # macOS twin
    (drive / "akit_fake.wpilog").write_bytes(b"this is not a log" * 100)   # wrong format
    (drive / "notes.txt").write_text("not a log")
    (drive / ".hidden").mkdir()
    shutil.copy2(logs / B, drive / ".hidden")

    import app.db.migrations  # noqa: F401
    from app.config import init_config
    from app.db import init_db
    from app.robot import ImportError_, batch, import_log
    init_config()
    db = init_db()

    print("\nFinding and fingerprinting")
    plan = batch.find_logs(drive)
    names = sorted(str(f.rel) for f in plan.found)
    check("every log in every subfolder, nothing else (no ._, no .txt, no hidden folder)",
          names == sorted(["2024-12-18_14-03-51/" + HOOT, "2026-08-17_03-42-50/" + B, A,
                           "akit_fake.wpilog", B]), ", ".join(names))
    check("the empty log is set aside", [p.name for p in plan.empty]
          == ["akit_9b0c7ffeea6cb854.wpilog"])
    batch.copy_in(plan)
    batch.mark_duplicates(plan, Path(db.path))
    check("a .wpilog with no WPILOG header isn't a log", [f.name for f in plan.invalid]
          == ["akit_fake.wpilog"] and "WPILOG" in plan.invalid[0].fp.problem)
    hdr = {f.name: f.fp.header for f in plan.found if f.fp.valid}
    check("headers read: WPILOG + logger, the hoot's bus",
          hdr[A] == "WPILOG 1.0 AdvantageKit" and hdr[HOOT] == "roboRIO Native CAN Bus",
          f"{hdr[A]!r}, {hdr[HOOT]!r}")
    check("the same bytes twice in the folder import once",
          sum(f.twin_of is not None for f in plan.found) == 1 and len(plan.queue) == 3)
    check("newest first", [f.name for f in plan.queue] == [HOOT, B, A],
          ", ".join(f.name for f in plan.queue))
    check("copies are stored by content: three logs, three files, nothing half-written",
          waiting(arch) == sorted([A, B, HOOT]) and not list(arch.rglob("*.part")),
          ", ".join(waiting(arch)))
    check("nothing imported yet", db.fetchone("SELECT COUNT(*) n FROM log_session")["n"] == 0)

    print("\nImporting, then the copy goes")
    for f in plan.queue:
        import_log(f.dest, db.path, archive_path=f.dest, fp=f.fp)
        batch.discard(f.dest, Path(db.path))
    rows = db.fetchall("SELECT source_name, source_sha256, source_head_sha256, source_header, "
                       "archive_path FROM log_session")
    check("three sessions, each fingerprinted", len(rows) == 3 and all(
        r["source_sha256"] and r["source_head_sha256"] and r["source_header"] for r in rows))
    check("every copy deleted once imported, archive_path cleared",
          waiting(arch) == [] and all(r["archive_path"] is None for r in rows))

    print("\nIdentity is the contents, never the name")
    other = tmp / "Other_Drive"
    other.mkdir()
    shutil.copy2(logs / A, other / "renamed_by_someone.wpilog")          # same bytes, new name
    raw_b = (logs / B).read_bytes()
    (other / B).write_bytes(raw_b[: len(raw_b) // 2])                     # same name, cut short
    raw_a = (logs / A).read_bytes()
    (other / "x.wpilog").write_bytes(raw_a[:-4096] + b"\0" * 4096)       # same start, other end
    p2 = batch.mark_duplicates(batch.copy_in(batch.find_logs(other)), Path(db.path))
    by = {f.name: f for f in p2.found}
    check("a renamed copy (same bytes) is a duplicate", by["renamed_by_someone.wpilog"].match == "exact")
    check("a shorter copy of a recording is 'the same recording', not new",
          by[B].match == "same_recording" and not by[B].longer)
    check("same start, different ending: the same recording, not the same file",
          by["x.wpilog"].match == "same_recording")
    try:
        import_log(other / "renamed_by_someone.wpilog", db.path)
        check("one-file import refuses a renamed copy", False)
    except ImportError_ as e:
        check("one-file import refuses a renamed copy (same contents)", "same file contents" in str(e))
    for f in p2.found:
        batch.discard(f.dest)

    print("\nThe same folder again")
    again = batch.mark_duplicates(batch.copy_in(batch.find_logs(drive)), Path(db.path))
    check("everything is recognised by content", len(again.duplicates) == 3 and again.queue == []
          and all(f.match == "exact" for f in again.duplicates))

    print("\nThe duplicates pop-up")
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    qapp = QApplication(sys.argv[:1])
    from app.admin import init_admin
    from app.cad_assets import init_cad_assets
    init_admin()
    init_cad_assets()
    from app.widgets.robot_panel import RobotLogPanel, _DuplicatesDialog
    locked = _DuplicatesDialog(again.duplicates, unlocked=False)
    check("locked: nothing can be replaced (deleting a session is admin-only)",
          all(not b.isEnabled() for b, _f in locked._boxes) and locked.chosen() == [])
    dlg = _DuplicatesDialog(again.duplicates, unlocked=True)
    check("same files start unticked", dlg.chosen() == [])
    dlg._boxes[0][0].setChecked(True)
    chosen = dlg.chosen()
    check("a ticked log is offered for re-import, replacing its session",
          len(chosen) == 1 and chosen[0][1] == chosen[0][0].duplicate_of["id"])
    longer = by[B]
    longer.size = 10 ** 9                                               # pretend it's longer
    check("a longer copy of a recording is pre-ticked",
          _DuplicatesDialog([longer], unlocked=True).chosen() != [])
    with db.transaction():
        db.execute("UPDATE log_session SET source_file = 'sync:x' WHERE source_name = ?", (A,))
    for f in again.duplicates:
        f.duplicate_of = dict(db.fetchone(
            "SELECT id, source_file, imported_at, source_bytes FROM log_session WHERE id = ?",
            (f.duplicate_of["id"],)))
    synced = _DuplicatesDialog(again.duplicates, unlocked=True)
    check("a session synced from another pit can't be replaced here",
          sum(not b.isEnabled() for b, _f in synced._boxes) == 1)
    for f in again.found:
        batch.discard(f.dest)

    print("\nThe panel, end to end")
    from app.robot import delete_session
    for r in db.fetchall("SELECT id FROM log_session"):
        delete_session(r["id"], db.path)
    import app.widgets.robot_panel as rp
    rp._DuplicatesDialog.exec = lambda self: 0

    def run(panel, folder) -> str:
        panel._import_status.setText("")
        panel.start_batch(folder)
        waited = 0
        while not panel._import_status.text().startswith(
                ("Imported", "No .hoot", "Stopped", "Couldn't", "Not enough")) \
                and waited < 240_000:
            qapp.processEvents()
            time.sleep(0.05)
            waited += 50
        qapp.processEvents()
        return panel._import_status.text()

    panel = RobotLogPanel()
    text = run(panel, drive)
    n = db.fetchone("SELECT COUNT(*) n FROM log_session")["n"]
    check("Import a folder: copies, imports all three, says what it skipped",
          n == 3 and text.startswith("Imported 3 log(s).") and "not real logs" in text, text[:160])
    check("…the session list shows them", panel._sessions.rowCount() == 3)
    check("…and nothing is left waiting on this machine", waiting(arch) == [],
          ", ".join(waiting(arch)))
    text = run(panel, drive)
    check("again: nothing new, the duplicates' copies deleted too",
          "already imported" in text and waiting(arch) == [], text[:140])

    print("\nTeam sync still has to upload the originals")
    for r in db.fetchall("SELECT id FROM log_session"):
        delete_session(r["id"], db.path)
    from app.db.sync import settings as sync_settings
    sync_settings.save(enabled=True, upload_raw=True)
    check("upload_raw on: copies are kept for the upload", batch.keep_for_upload())
    run(RobotLogPanel(), drive)
    kept = waiting(arch)
    check("…the imported logs' copies wait on this machine", len(kept) == 3, ", ".join(kept))
    path = db.fetchone("SELECT archive_path FROM log_session WHERE source_name = ?",
                       (A,))["archive_path"]
    check("…and the sync engine's hook deletes one after uploading it",
          bool(path) and batch.release_after_upload(path) and not Path(path).exists())

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'All batch checks passed.' if not _failures else f'{len(_failures)} failed.'}")
    sys.stdout.flush()
    os._exit(0 if not _failures else 1)


if __name__ == "__main__":
    sys.exit(main())
