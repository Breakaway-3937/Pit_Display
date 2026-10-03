"""
Batch import from a folder (app/robot/batch.py and the Robot Logs panel). Exit 0/1.

    uv run tools/batch_check.py

Builds a fake log drive from TEST_LOGS/ shaped like a real one (logs at the
top, a roboRIO dump folder, a session folder, macOS `._` twins, an empty log,
the same log in two folders), then on a fresh scratch install:

* finds every log in every subfolder and nothing else;
* copies all of them onto the machine before importing anything, atomically;
* imports the new ones newest first, recording each local copy;
* run again: everything is a duplicate, nothing is copied twice, nothing imports;
* the duplicates pop-up offers re-import only for local sessions with the admin
  lock open, and a re-import replaces the session rather than doubling it;
* the panel runs the whole thing on its worker threads.

Never this Mac's data: PIT_DISPLAY_DATA and PIT_LOG_ARCHIVE point at scratch.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
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


def main() -> int:
    use_utf8()
    tmp = Path(tempfile.mkdtemp(prefix="batch-check-"))
    os.environ.update(PIT_DISPLAY_DATA=str(tmp / "pit"), PIT_LOG_ARCHIVE=str(tmp / "archive"),
                      PIT_LOG_SCRATCH=str(tmp / "owlet"), QT_QPA_PLATFORM="offscreen",
                      PIT_LEDS_FAKE="1", PIT_NEXUS_QUIET="1", PIT_SYNC_QUIET="1")
    (tmp / "pit" / "data").mkdir(parents=True)
    logs = ROOT / "TEST_LOGS"
    drive = tmp / "New_Logs"
    (drive / "2024-12-18_14-03-51").mkdir(parents=True)
    (drive / "2026-08-17_03-42-50").mkdir()
    shutil.copy2(logs / "akit_26-08-17_02-43-54.wpilog", drive)
    shutil.copy2(logs / "akit_26-08-17_02-59-21.wpilog", drive)
    shutil.copy2(logs / "rio_2026-08-17_03-42-54.hoot", drive / "2024-12-18_14-03-51")
    # The same log twice (a dump folder and its session folder).
    shutil.copy2(logs / "akit_26-08-17_02-59-21.wpilog", drive / "2026-08-17_03-42-50")
    (drive / "akit_9b0c7ffeea6cb854.wpilog").write_bytes(b"")             # empty
    (drive / "._akit_26-08-17_02-43-54.wpilog").write_bytes(b"\0" * 4096)  # macOS twin
    (drive / "notes.txt").write_text("not a log")
    (drive / ".hidden").mkdir()
    shutil.copy2(logs / "akit_26-08-17_02-59-21.wpilog", drive / ".hidden")

    import app.db.migrations  # noqa: F401
    from app.config import init_config
    from app.db import init_db
    from app.robot import batch, import_log
    init_config()
    db = init_db()

    print("\nFinding")
    plan = batch.mark_duplicates(batch.find_logs(drive), Path(db.path))
    names = sorted(str(f.rel) for f in plan.found)
    check("every log in every subfolder, nothing else (no ._, no .txt, no hidden folder)",
          names == ["2024-12-18_14-03-51/rio_2026-08-17_03-42-54.hoot",
                    "2026-08-17_03-42-50/akit_26-08-17_02-59-21.wpilog",
                    "akit_26-08-17_02-43-54.wpilog", "akit_26-08-17_02-59-21.wpilog"],
          ", ".join(names))
    check("the empty log is set aside, not imported", [p.name for p in plan.empty]
          == ["akit_9b0c7ffeea6cb854.wpilog"])
    check("the same log twice in the folder imports once",
          sum(f.twin_of is not None for f in plan.found) == 1 and len(plan.queue) == 3)
    check("newest first", [f.name for f in plan.queue] == [
        "rio_2026-08-17_03-42-54.hoot", "akit_26-08-17_02-59-21.wpilog",
        "akit_26-08-17_02-43-54.wpilog"], ", ".join(f.name for f in plan.queue))

    print("\nCopying (before any import)")
    batch.copy_in(plan)
    arch = tmp / "archive" / "New_Logs"
    on_disk = sorted(str(p.relative_to(arch)) for p in arch.rglob("*") if p.is_file())
    check("all four are on this machine, in their folders, byte for byte",
          len(on_disk) == 4 and all((arch / f.rel).read_bytes() == f.source.read_bytes()
                                    for f in plan.found), ", ".join(on_disk))
    check("no half-written files left", not list(arch.rglob("*.part")))
    check("nothing imported yet", db.fetchone("SELECT COUNT(*) n FROM log_session")["n"] == 0)

    print("\nImporting")
    for f in plan.queue:
        import_log(f.dest, db.path, archive_path=f.dest)
    rows = db.fetchall("SELECT source_name, source_file, archive_path FROM log_session")
    check("three sessions, each from (and pointing at) its local copy",
          len(rows) == 3 and all(r["archive_path"] and r["archive_path"].startswith(str(arch))
                                 for r in rows))

    print("\nThe same folder again")
    again = batch.mark_duplicates(batch.find_logs(drive), Path(db.path))
    before = {p: p.stat().st_mtime_ns for p in arch.rglob("*") if p.is_file()}
    batch.copy_in(again)
    after = {p: p.stat().st_mtime_ns for p in arch.rglob("*") if p.is_file()}
    check("everything is recognised as imported before", len(again.duplicates) == 3
          and again.queue == [])
    check("nothing is copied twice", before == after and not any(f.copied for f in again.found))
    # From somewhere else entirely (another drive): still a duplicate, by name and size.
    other = tmp / "Other_Drive"
    other.mkdir()
    shutil.copy2(logs / "akit_26-08-17_02-43-54.wpilog", other)
    elsewhere = batch.mark_duplicates(batch.find_logs(other), Path(db.path))
    check("the same log from another drive is a duplicate too", len(elsewhere.duplicates) == 1)

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
    dlg._boxes[0][0].setChecked(True)
    chosen = dlg.chosen()
    check("unlocked: a ticked log is offered for re-import, replacing its session",
          len(chosen) == 1 and chosen[0][1] == chosen[0][0].duplicate_of["id"])
    # A synced session can't be replaced from here.
    with db.transaction():
        db.execute("UPDATE log_session SET source_file = 'sync:x' WHERE source_name = ?",
                   (again.duplicates[1].name,))
    synced = _DuplicatesDialog(batch.mark_duplicates(batch.find_logs(drive), Path(db.path)).duplicates,
                               unlocked=True)
    check("a session synced from another pit can't be replaced here",
          sum(not b.isEnabled() for b, _f in synced._boxes) == 1)

    print("\nThe panel, end to end")
    from app.robot import delete_session
    for r in db.fetchall("SELECT id FROM log_session"):
        delete_session(r["id"], db.path)
    shutil.rmtree(tmp / "archive")
    from PyQt6.QtCore import QTimer
    import app.widgets.robot_panel as rp
    rp._DuplicatesDialog.exec = lambda self: 0      # nothing to stage on a fresh install
    panel = RobotLogPanel()
    panel.start_batch(drive)
    deadline = 240_000
    waited = 0
    while not panel._import_status.text().startswith(("Imported", "No .hoot", "Stopped")) and waited < deadline:
        qapp.processEvents()
        QTimer.singleShot(50, lambda: None)
        import time
        time.sleep(0.05)
        waited += 50
    qapp.processEvents()
    n = db.fetchone("SELECT COUNT(*) n FROM log_session")["n"]
    check("Import a folder: copies, then imports all three, and says so",
          n == 3 and panel._import_status.text().startswith("Imported 3 log(s)."),
          panel._import_status.text()[:140])
    check("…and the panel's session list shows them", panel._sessions.rowCount() == 3)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'All batch checks passed.' if not _failures else f'{len(_failures)} failed.'}")
    sys.stdout.flush()
    os._exit(0 if not _failures else 1)


if __name__ == "__main__":
    sys.exit(main())
