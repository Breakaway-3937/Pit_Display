#!/usr/bin/env python3
"""
Does a version change keep this machine's data?

    uv run tools/upgrade_check.py

An update replaces the whole install folder. Everything the crew typed —
checklists, the CAN-id names, EQ and LED presets, the music index, the admin
password — lives in the *data* directory instead, and the promise is that an
update never touches it. This proves it rather than repeating it:

1. Build a data directory and fill every user-owned table with known rows.
2. Ship a "new version" at it — a freshly registered migration, and the
   seed database that rides along inside every build.
3. Re-open, and check every row is still there.

**The seed database is the dangerous half.** A build carries
`data/pit_display.db` so a *fresh* install has a schema; if that ever landed on
top of an existing one, a season of imported logs and every CAN-id name would
go with it. `paths.seed_user_data()` is the only thing that copies it, and its
rule is "what you get when you have nothing, not a factory reset".

Exit status is 0 when everything survived, 1 otherwise.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PIT_LEDS_FAKE", "1")
os.environ.setdefault("PIT_NEXUS_QUIET", "1")

from app.console import use_utf8  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    return bool(ok)


# What the crew owns. Each entry is a table, the rows to plant, and the query
# that proves they are still there afterwards.
MARKERS = {
    "checklist": "UPGRADE-CHECK list",
    "checklist_item": "UPGRADE-CHECK item",
    "device_label": "UPGRADE-CHECK Front-Left Drive",
    "eq_preset": "UPGRADE-CHECK eq",
    "led_preset": "UPGRADE-CHECK led",
    "track": "UPGRADE-CHECK track",
}


def plant(conn: sqlite3.Connection) -> None:
    """Write one identifiable row into every table a person fills in."""
    cur = conn.cursor()
    cur.execute("INSERT INTO checklist (name) VALUES (?)",
                (MARKERS["checklist"],))
    list_id = cur.lastrowid
    cur.execute("INSERT INTO checklist_item (checklist_id, text, position, done) "
                "VALUES (?, ?, 0, 1)", (list_id, MARKERS["checklist_item"]))
    # A CAN-id name: the one thing here typed in by hand, match after match.
    cur.execute("INSERT INTO device (device_type, can_id, label) VALUES (?, ?, ?)",
                ("TalonFX", 99, MARKERS["device_label"]))
    cur.execute("INSERT INTO eq_presets (name, gains, preamp) VALUES (?, ?, ?)",
                (MARKERS["eq_preset"], "0,0,0,0,0,0,0,0,0,0", 0))
    cur.execute("INSERT INTO led_presets (name, mode, speed, brightness, color) "
                "VALUES (?, ?, ?, ?, ?)",
                (MARKERS["led_preset"], 1, 128, 180, "#BA141A"))
    cur.execute("INSERT INTO tracks (path, title) VALUES (?, ?)",
                ("/tmp/upgrade-check.mp3", MARKERS["track"]))
    conn.commit()


def survivors(conn: sqlite3.Connection) -> dict[str, bool]:
    found = {}
    q = [
        ("checklist", "SELECT COUNT(*) FROM checklist WHERE name = ?"),
        ("checklist_item", "SELECT COUNT(*) FROM checklist_item WHERE text = ?"),
        ("device_label", "SELECT COUNT(*) FROM device WHERE label = ?"),
        ("eq_preset", "SELECT COUNT(*) FROM eq_presets WHERE name = ?"),
        ("led_preset", "SELECT COUNT(*) FROM led_presets WHERE name = ?"),
        ("track", "SELECT COUNT(*) FROM tracks WHERE title = ?"),
    ]
    for key, sql in q:
        try:
            found[key] = conn.execute(sql, (MARKERS[key],)).fetchone()[0] > 0
        except sqlite3.Error:
            found[key] = False
    return found


def main() -> int:
    use_utf8()
    scratch = ROOT / ".upgrade_check"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    os.environ["PIT_DISPLAY_DATA"] = str(scratch)

    import app.db.migrations  # noqa: F401  — registers the schema
    from app import paths
    from app.db import database, init_db, register_migration

    print("\n── A machine that has been used ───────────────────────────", flush=True)
    db = init_db()
    before_version = db.fetchone("PRAGMA user_version")[0]
    plant(db._conn)
    planted = survivors(db._conn)
    check("every user-owned table took a row", all(planted.values()),
          ", ".join(k for k, v in planted.items() if not v) or
          f"{len(planted)} tables, schema v{before_version}")
    db_path = paths.data("data", "pit_display.db")
    check("the database is in the data directory, not the install",
          str(scratch) in str(db_path), str(db_path))
    db.close() if hasattr(db, "close") else None

    print("\n── The new version's seed database arrives ────────────────", flush=True)
    # Exactly what happens on first launch after an update: the build carries
    # `data/pit_display.db`, and this is the only code that ever copies it.
    size_before = db_path.stat().st_size
    copied = paths.seed_user_data()
    size_after = db_path.stat().st_size
    check("the shipped seed does NOT overwrite an existing database",
          "data/pit_display.db" not in copied and size_before == size_after,
          f"copied={copied or 'nothing'}")

    print("\n── …and the new version adds a migration ──────────────────", flush=True)

    @register_migration
    def _v_next(conn: sqlite3.Connection) -> None:
        """Stands in for whatever the next release adds."""
        conn.executescript(
            "CREATE TABLE upgrade_check_new (id INTEGER PRIMARY KEY, note TEXT);")

    database._DB = None           # re-open the way a fresh launch would
    db2 = init_db()
    after_version = db2.fetchone("PRAGMA user_version")[0]
    check("the pending migration ran", after_version == before_version + 1,
          f"v{before_version} -> v{after_version}")
    check("the new table exists",
          bool(db2.fetchone("SELECT name FROM sqlite_master "
                            "WHERE type='table' AND name='upgrade_check_new'")))

    kept = survivors(db2._conn)
    for key, ok in kept.items():
        check(f"{key} survived the version change", ok)

    print("\n── Opening it again changes nothing ───────────────────────", flush=True)
    database._DB = None
    db3 = init_db()
    check("already-applied migrations do not re-run",
          db3.fetchone("PRAGMA user_version")[0] == after_version,
          f"v{db3.fetchone('PRAGMA user_version')[0]}")
    again = survivors(db3._conn)
    check("every row is still there", all(again.values()),
          ", ".join(k for k, v in again.items() if not v))

    print("\n── The disposable half ────────────────────────────────────", flush=True)
    samples = paths.data("data", "pit_display_samples.db")
    check("telemetry lives in its own file", samples.exists(), str(samples.name))
    database._DB = None
    samples.unlink()
    db4 = init_db()
    check("deleting it is survivable and rebuilds the schema",
          samples.exists()
          and db4.fetchone("SELECT COUNT(*) c FROM samples.sample")["c"] == 0)
    final = survivors(db4._conn)
    check("and the main database is untouched by that", all(final.values()),
          ", ".join(k for k, v in final.items() if not v))

    print("\n── What is NOT in the database ────────────────────────────", flush=True)
    # Not failures — facts an operator needs, because they behave differently.
    from app.webcast import settings as wset
    from app.nexus import settings as nset
    for label, path in (("nexus.json", nset.path()),
                        ("webcast.json", wset.path()),
                        ("update.json", paths.data("update.json")),
                        ("secrets/", paths.data_root() / "secrets")):
        print(f"  (file) {label:<14} {path}", flush=True)
    print("  (memory) per-screen settings — theme, screen content, slide index,\n"
          "           checklist_id — are held in `config` only. They reset on\n"
          "           every launch, not just on a version change.", flush=True)

    shutil.rmtree(scratch, ignore_errors=True)
    print()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if failed:
        print("\nFAILED:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    os._exit(status)
