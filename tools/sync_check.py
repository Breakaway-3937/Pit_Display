"""
Check sync end to end: two pit machines and the home role against a real hub.
Exit 0/1.

    uv run tools/sync_check.py --local        starts its own `wrangler dev` hub
                                              (fresh state, temp dir), then checks
    uv run tools/sync_check.py --url URL      an already-running hub; needs
                                              PIT_SECRET_SYNC_TOKEN and SYNC_HOME_TOKEN

Each "machine" is its own data directory with its own database, run through
the same `Engine` the app uses, so a pass here is a pass for the app.

What it proves:

* a first sync of two fresh machines converges (the stock rows merge, not
  duplicate), and a further cycle moves nothing (no echo, no ping-pong);
* checklists, items, EQ presets, CAN names, the admin password travel; a
  checklist tick does not;
* two machines editing the same row: the first to reach the hub wins, the
  second is told and takes it;
* deletes travel; a name collision adopts instead of duplicating;
* the Nexus event key travels, the updater channel doesn't;
* a judges slide travels as a file;
* a real robot log imported on A arrives on B with the same series, samples
  and faults, and B's differing enum codes are remapped;
* a home-forced write overrides, and an analysis board lands.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.console import use_utf8  # noqa: E402

use_utf8()

import app.db.migrations  # noqa: E402,F401 — registers migrations
from app.db.database import _Database  # noqa: E402
from app.db.sync import docs  # noqa: E402
from app.db.sync.client import HubClient  # noqa: E402
from app.db.sync.engine import Engine  # noqa: E402

_failures: list[str] = []
from devhub import HOME_TOKEN, OLD_PIT_TOKEN, PIT_TOKEN, start_hub  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


class Machine:
    """One pit laptop: a data directory, a database, an engine."""

    def __init__(self, name: str, base: Path, url: str, token: str):
        self.name = name
        self.dir = base / name
        self.dir.mkdir(parents=True)
        self.url, self.token = url, token
        with self.env():
            self.db = _Database(self.dir / "data" / "pit_display.db")
            client = HubClient(url, token, f"check-{name}", name, "check")
            self.engine = Engine(self.db.path, client, f"check-{name}",
                                 {"pull_logs": True, "upload_raw": True, "sync_files": True})

    def env(self):
        machine = self

        class _Env:
            def __enter__(self):
                self.old = os.environ.get("PIT_DISPLAY_DATA")
                os.environ["PIT_DISPLAY_DATA"] = str(machine.dir)

            def __exit__(self, *exc):
                if self.old is None:
                    os.environ.pop("PIT_DISPLAY_DATA", None)
                else:
                    os.environ["PIT_DISPLAY_DATA"] = self.old
        return _Env()

    def sync(self):
        with self.env():
            rep = self.engine.cycle()
        if rep.errors:
            print(f"    [{self.name}] errors: {rep.errors}")
        return rep

    def sql(self, q: str, p: tuple = ()):
        with self.db.transaction():
            return self.db.execute(q, p)

    def one(self, q: str, p: tuple = ()):
        row = self.db.fetchone(q, p)
        return None if row is None else row[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--url")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="sync-check-"))
    proc = None
    pit_token, home_token = PIT_TOKEN, HOME_TOKEN
    try:
        if args.local:
            print("Starting a throwaway hub (wrangler dev)…")
            proc, url = start_hub(tmp)
        elif args.url:
            url = args.url
            pit_token = os.environ.get("PIT_SECRET_SYNC_TOKEN", "")
            home_token = os.environ.get("SYNC_HOME_TOKEN", "")
            if not (pit_token and home_token):
                print("--url needs PIT_SECRET_SYNC_TOKEN and SYNC_HOME_TOKEN")
                return 1
        else:
            ap.print_help()
            return 1
        print(f"hub {url}\n")
        docs.SETTLE_S = 0          # files written by this check are settled at once
        run(tmp, url, pit_token, home_token)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {', '.join(_failures)}")
        return 1
    print("All sync checks passed.")
    return 0


# Home's award feeds as home/REQUESTS.md (R5, "Supersedes…") shows them.
HOME_FEEDS = [
    {"tbl": "tba_team", "uid": "frc3937", "op": "upsert", "base": 0,
     "data": {"team_number": "3937", "nickname": "Breakaway", "state_prov": "Arkansas",
              "rookie_year": 2012, "total_awards": 53, "blue_banners": 13,
              "award_streak": 13, "quality_awards": 10, "quality_last_year": 2026,
              "quality_rank": 5}},
    {"tbl": "tba_team", "uid": "frc16", "op": "upsert", "base": 0,
     "data": {"team_number": "16", "nickname": "Bomb Squad", "total_awards": 117,
              "award_streak": 25}},
    {"tbl": "tba_rival", "uid": "frc16", "op": "upsert", "base": 0,
     "data": {"team_number": "16", "nickname": "Bomb Squad", "finals_together": 2,
              "won_together": 2, "beat_us": 5, "we_beat": 0,
              "beat_us_years": "2023, 2022, 2020, 2018, 2016"}},
    {"tbl": "tba_fact", "uid": "our_streak", "op": "upsert", "base": 0,
     "data": {"category": "3937", "team_number": "3937", "sort": 2,
              "text": "Breakaway has brought home an award 13 seasons in a row (since 2014)."}},
]


# Home's standard datasets (R10/R11), with home's own example values.
_SEASONS = [  # year, robot, events, W, L, T, win_pct, best_finish, awards
    (2026, None, 3, 26, 18, 0, 59, "finalist", 4), (2025, None, 4, 44, 20, 0, 69, "won an event", 5),
    (2024, None, 3, 36, 15, 0, 71, "won an event", 5), (2023, None, 4, 47, 16, 0, 75, "won an event", 7),
    (2022, None, 3, 34, 13, 0, 72, "finalist", 4), (2021, None, 1, None, None, None, None, "quals only", 2),
    (2020, "Vanguard", 2, 10, 5, 0, 67, "finalist", 3), (2019, None, 3, 21, 16, 1, 55, "semifinals", 2),
    (2018, "Q*Bert", 3, 27, 14, 0, 66, "finalist", 3), (2017, "Dreadnought", 3, 25, 17, 1, 58, "won an event", 2),
    (2016, "Freedom", 3, 26, 15, 0, 63, "finalist", 5), (2015, "Samson", 2, 2, 0, 1, 67, "won an event", 2),
    (2014, None, 3, 31, 14, 0, 69, "won an event", 6), (2013, None, 2, 15, 11, 1, 56, "quarterfinals", 0),
    (2012, None, 2, 20, 13, 0, 61, "quarterfinals", 3)]
HOME_DATASETS = [
    {"tbl": "home_dataset", "uid": "quality", "op": "upsert", "base": 0, "data": {
        "title": "Quality Award leaders", "description": "Most Quality Awards won, every FRC team",
        "category": "3937", "sort": 1,
        "columns": ["Team_key", "Top_Quality", "Most_Recent_Year", "Quality_Rank"],
        "rows": [["frc254", 16, 2026, 1], ["frc67", 16, 2025, 1], ["frc148", 15, 2024, 2],
                 ["frc2056", 13, 2025, 3], ["frc118", 11, 2026, 4], ["frc3937", 10, 2026, 5]],
        "highlight": {"column": "Team_key", "value": "frc3937", "rows": [5]},
        "total_rows": 1186, "truncated": False}},
    {"tbl": "home_dataset", "uid": "bk_seasons", "op": "upsert", "base": 0, "data": {
        "title": "Breakaway season by season", "category": "3937", "sort": 20,
        "columns": ["year", "robot_name", "events", "wins", "losses", "ties", "win_pct",
                    "best_finish", "awards"],
        "rows": [list(r) for r in _SEASONS], "highlight": None,
        "total_rows": 15, "truncated": False}},
    {"tbl": "home_dataset", "uid": "bk_records", "op": "upsert", "base": 0, "data": {
        "title": "Breakaway's records", "category": "3937", "sort": 10,
        "columns": ["rank", "record", "value", "detail"],
        "rows": [[1, "Longest match win streak", "21", "2024arli to 2024mosl; #133 all-time"],
                 [2, "Events in a row making the playoffs", "33", "2015–2026, still going; #68 all-time"]],
        "highlight": None, "total_rows": 14, "truncated": False}},
    {"tbl": "home_dataset", "uid": "ar_leaderboard", "op": "upsert", "base": 0, "data": {
        "title": "Arkansas all-time awards", "category": "arkansas", "sort": 30,
        "columns": ["rank", "team_key", "team_number", "awards"],
        "rows": [[1, "frc16", "16", 117], [2, "frc3937", "3937", 53]],
        "highlight": {"column": "team_key", "value": "frc3937", "rows": [1]},
        "total_rows": 81, "truncated": False}},
    {"tbl": "home_dataset", "uid": "fun_facts", "op": "upsert", "base": 0, "data": {
        "title": "Fun facts", "category": "facts", "sort": 5,
        "columns": ["category", "fact_key", "team_key", "fact_text", "sort"],
        "rows": [["3937", "our_streak", "frc3937",
                  "Breakaway has brought home an award 13 seasons in a row (since 2014).", 1],
                 ["arkansas", "ar_teams", None, "Arkansas has had 81 FRC teams; 8 played in 2026.", 101]],
        "highlight": None, "total_rows": 37, "truncated": False}},
]


def relay_section(a, b, url: str, home_token: str) -> None:
    """R8: R2 as a relay. The pit side of home's checks (a), (b), (c), (e):
    the night window holds bytes (not rows), a pit asks home for bytes the
    hub evicted and gets them when home re-uploads, songs travel, an admin's
    delete is a mark, home's approval removes only the team folder's copy,
    and a manifest goes up and home's verdict comes back."""
    import hashlib
    from datetime import datetime, timedelta
    from app.db.sync import docs, transfer
    print("\nR2 as a relay (R8)")
    home = HubClient(url, home_token, "home", "home", "check")
    now = datetime.now()
    closed = {"enforce": True, "start": (now + timedelta(hours=2)).strftime("%H:%M"),
              "end": (now + timedelta(hours=3)).strftime("%H:%M")}
    for m in (a, b):
        with m.env():
            transfer.save(**closed)

    # A song on A, in A's own library folder (not the team folder).
    song = a.dir / "my music" / "01 Track.mp3"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"ID3" + os.urandom(300_000))
    a.sql("INSERT INTO tracks (path, title, artist) VALUES (?, 'Pit Song', 'Breakaway')",
          (str(song),))
    sha = hashlib.sha256(song.read_bytes()).hexdigest()
    uid = f"music/{sha[:16]}_01 Track.mp3"
    ra = a.sync()
    check("window closed: A holds the song's upload (bytes and row wait)",
          ra.held_uploads >= 1 and a.one("SELECT COUNT(*) FROM sync_outbox WHERE uid = ?",
                                         (uid,)) == 1, f"held {ra.held_uploads}")
    with a.env():
        a.engine.force_files = True
        ra = a.engine.cycle()
    check("'Sync files now' sends it outside the window", ra.ok and
          a.one("SELECT COUNT(*) FROM sync_outbox WHERE uid = ?", (uid,)) == 0, str(ra.errors))
    check("…as music/<sha16>_<name>, the scanned file left where it was",
          a.one("SELECT hash FROM sync_doc WHERE tbl = 'file' AND uid = ?", (uid,)) == sha
          and song.is_file())

    rb = b.sync()
    with b.env():
        landed = docs.root("music") / f"{sha[:16]}_01 Track.mp3"
    check("window closed: B learns the song but moves no bytes",
          rb.held_downloads >= 1 and not landed.exists(), f"held {rb.held_downloads}")
    check("…and asks nothing while the hub still holds them",
          b.one("SELECT COUNT(*) FROM sync_blob_request") == 0)

    # Home evicts the blob (R8 step 4). B must ask for it.
    blob = b.one("SELECT data FROM sync_pending WHERE uid = ?", (uid,))
    blob = json.loads(blob)["blob"]
    req = urllib.request.Request(f"{url}/v1/blob/{blob}", method="DELETE", headers={
        "Authorization": f"Bearer {home_token}", "X-Pit-Machine": "home"})
    urllib.request.urlopen(req).read()
    b.engine._present.clear()                 # a fresh start, as after a night off
    rb = b.sync()
    check("evicted: B posts a request any time of day (window still closed)",
          rb.open_requests == 1 and not landed.exists(), f"open {rb.open_requests}")
    rows = home.changes(0, 1000)["changes"]
    asked = [c for c in rows if c["tbl"] == "blob_request" and c["uid"] == f"check-b:{blob}"]
    check("…as blob_request <machine>:<blob sha> with the file it's for",
          asked and asked[-1]["data"]["file_uid"] == uid and asked[-1]["data"]["done_at"] is None,
          str(asked[-1]["data"] if asked else rows[-3:]))

    # Home answers from its archive (here: A's copy).
    home.put_blob(song, blob, "file", uid)
    with b.env():
        b.engine.force_files = True
        rb = b.engine.cycle()
    check("answered: B downloads it, sha verified, into the team music folder",
          rb.ok and landed.is_file() and landed.read_bytes() == song.read_bytes(), str(rb.errors))
    rows = home.changes(0, 1000)["changes"]
    done = [c for c in rows if c["tbl"] == "blob_request" and c["uid"] == f"check-b:{blob}"]
    check("…and marks its request done", done and done[-1]["data"]["done_at"],
          str(done[-1]["data"] if done else ""))

    # (c) the manifest and home's verdict.
    manifests = [c for c in rows if c["tbl"] == "machine_manifest" and c["uid"] == "check-b"]
    check("B's manifest is at the hub (files by uid → sha, playlists hashed)",
          manifests and manifests[-1]["data"].get("files") is not None
          and "playlists" in manifests[-1]["data"], str(manifests[-1]["data"] if manifests else ""))
    home.push([{"tbl": "sync_verdict", "uid": "check-b", "op": "upsert", "base": 0,
                "data": {"checked_at": "2026-10-02T05:01:00-05:00", "in_sync": False,
                         "manifest_at": manifests[-1]["data"]["at"] if manifests else "",
                         "missing": ["cad/robot.glb"], "different": [], "extra": [],
                         "playlists_differ": []}}], force=True)
    b.sync()
    from app.widgets.network_panel import verdict_line
    held = b.one("SELECT data FROM sync_verdict WHERE uid = 'check-b'")
    line = verdict_line(json.loads(held) if held else None)
    check("home's verdict lands and reads as one line",
          "Out of sync with home" in line and "robot.glb" in line, line)

    # An admin's delete is a mark; home's approval removes the team copy only.
    a.sql("UPDATE tracks SET team_deleted = 1 WHERE path = ?", (str(song),))
    a.sync()
    b.sync()
    rows = home.changes(0, 1000)["changes"]
    mark = [c for c in rows if c["tbl"] == "file" and c["uid"] == uid]
    check("admin delete goes up as a mark, not a delete",
          mark and mark[-1]["op"] == "upsert" and mark[-1]["data"].get("deleted") is True)
    check("…B keeps the file, and doesn't fetch it again",
          landed.is_file() and b.one("SELECT hash FROM sync_doc WHERE uid = ?", (uid,)) == "deleted")
    home.push([{"tbl": "file", "uid": uid, "op": "delete", "base": 0}], force=True)
    a.sync(); b.sync()
    check("home approves: the team folder's copy goes, A's own file stays",
          not landed.exists() and song.is_file())

    for m in (a, b):
        with m.env():
            transfer.save(enforce=False)
    a.sync(); b.sync()


def run(tmp: Path, url: str, pit_token: str, home_token: str) -> None:
    a = Machine("a", tmp, url, pit_token)
    b = Machine("b", tmp, url, pit_token)
    from app.db.sync import transfer
    for m in (a, b):
        with m.env():
            transfer.save(enforce=False)     # the night window gets its own section

    print("First contact")
    ra = a.sync()
    rb = b.sync()
    check("both machines reach the hub", ra.ok and rb.ok, str(ra.errors + rb.errors))
    check("B's first sync adopts the team's stock rows without calling it a conflict",
          rb.adopted > 0 and not rb.conflicts, f"adopted {rb.adopted}, conflicts {rb.conflicts}")
    check("B merges the stock rows instead of duplicating",
          b.one("SELECT COUNT(*) FROM eq_presets") == 5 and
          b.one("SELECT COUNT(*) FROM checklist WHERE name = 'Pit Checklist'") == 1)
    a.sync(); b.sync()
    ra, rb = a.sync(), b.sync()
    check("a settled pair moves nothing", ra.pushed == rb.pushed == ra.pulled == rb.pulled == 0,
          f"A {ra.pushed}/{ra.pulled}, B {rb.pushed}/{rb.pulled}")
    check("outboxes are empty", ra.outbox == rb.outbox == 0, f"{ra.outbox}/{rb.outbox}")

    print("\nTeam rows")
    a.sql("INSERT INTO checklist (name, position) VALUES ('Load Out', 1)")
    cid = a.one("SELECT id FROM checklist WHERE name = 'Load Out'")
    a.sql("INSERT INTO checklist_item (checklist_id, text, position) VALUES (?, 'Pack battery cart', 0)", (cid,))
    a.sql("INSERT INTO checklist_item (checklist_id, text, position) VALUES (?, 'Coil extension cords', 1)", (cid,))
    a.sql("INSERT INTO eq_presets (name, preamp, gains) VALUES ('Finals', -1, '1,1,1,1,1,1,1,1,1,1')")
    a.sql("INSERT INTO device (device_type, can_id, label, subsystem) VALUES ('TalonFX', 11, 'Front-Left Drive', 'Drivetrain')")
    a.sql("INSERT INTO admin_credential (id, algo, iterations, salt, hash, is_default) "
          "VALUES (1, 'pbkdf2', 1, 'salt', 'hash-a', 0) "
          "ON CONFLICT (id) DO UPDATE SET hash = excluded.hash, is_default = 0")
    a.sync(); b.sync()
    bid = b.one("SELECT id FROM checklist WHERE name = 'Load Out'")
    check("a new checklist arrives", bid is not None)
    check("its items arrive, in order",
          [r[0] for r in b.db.fetchall("SELECT text FROM checklist_item WHERE checklist_id = ? ORDER BY position", (bid,))]
          == ["Pack battery cart", "Coil extension cords"])
    check("an EQ preset arrives", b.one("SELECT gains FROM eq_presets WHERE name = 'Finals'") == "1,1,1,1,1,1,1,1,1,1")
    check("a CAN name arrives", b.one("SELECT label FROM device WHERE device_type = 'TalonFX' AND can_id = 11") == "Front-Left Drive")
    check("the admin password arrives", b.one("SELECT hash FROM admin_credential WHERE id = 1") == "hash-a")

    a.sql("UPDATE checklist_item SET done = 1 WHERE text = 'Pack battery cart'")
    a.sync(); rb = b.sync()
    check("a tick stays on its own pit",
          b.one("SELECT done FROM checklist_item WHERE text = 'Pack battery cart'") == 0 and rb.pulled == 0)

    print("\nConflicts")
    a.sql("UPDATE checklist_item SET text = 'Pack BOTH battery carts' WHERE text = 'Pack battery cart'")
    b.sql("UPDATE checklist_item SET text = 'Pack the battery cart' WHERE text = 'Pack battery cart'")
    a.sync(); rb = b.sync()
    check("the first edit to reach the hub wins",
          b.one("SELECT COUNT(*) FROM checklist_item WHERE text = 'Pack BOTH battery carts'") == 1)
    check("the second machine is told", len(rb.conflicts) == 1, str(rb.conflicts))
    ra = a.sync()
    check("the winner keeps its edit",
          a.one("SELECT COUNT(*) FROM checklist_item WHERE text = 'Pack BOTH battery carts'") == 1)

    print("\nDeletes and collisions")
    b.sql("DELETE FROM checklist_item WHERE text = 'Coil extension cords'")
    b.sync(); a.sync()
    check("a delete travels", a.one("SELECT COUNT(*) FROM checklist_item WHERE text = 'Coil extension cords'") == 0)
    # Both create a list with the same name before either syncs.
    a.sql("INSERT INTO checklist (name, position) VALUES ('Day 2', 3)")
    b.sql("INSERT INTO checklist (name, position) VALUES ('day 2', 4)")
    a.sync(); b.sync(); a.sync(); b.sync()
    check("same-named lists become one",
          a.one("SELECT COUNT(*) FROM checklist WHERE name = 'Day 2' COLLATE NOCASE") == 1 and
          b.one("SELECT COUNT(*) FROM checklist WHERE name = 'Day 2' COLLATE NOCASE") == 1)
    a.sql("DELETE FROM eq_presets WHERE name = 'Crowded'")
    a.sync(); b.sync()
    check("a deleted stock preset stays deleted everywhere",
          b.one("SELECT COUNT(*) FROM eq_presets WHERE name = 'Crowded'") == 0)

    print("\nSettings documents")
    with a.env():
        from app.nexus import settings as nexus_settings
        from app.update import settings as update_settings
        nexus_settings.save(event_key="2026mokc", poll_interval_s=20)
        update_settings.save(channel="beta")
    a.sync(); b.sync()
    with b.env():
        check("the event key travels", nexus_settings.get("event_key") == "2026mokc")
        check("the poll interval travels", nexus_settings.get("poll_interval_s") == 20)
        check("the updater channel does not", update_settings.get("channel") == "stable")

    print("\nFiles")
    with a.env():
        slide = docs.root("judges_slides") / "01 Robot.png"
        slide.write_bytes(os.urandom(200_000))
    a.sync(); b.sync(); b.sync()
    with b.env():
        got = docs.root("judges_slides") / "01 Robot.png"
        check("a judges slide travels", got.is_file() and got.read_bytes() == slide.read_bytes())
    with a.env():
        notes = docs.root("judges_slides") / "02 Notes.svg"
        notes.write_text("<svg>" + "<rect x='1' y='2' width='3' height='4'/>" * 60_000 + "</svg>")
    ra = a.sync(); b.sync(); b.sync()
    with b.env():
        got = docs.root("judges_slides") / "02 Notes.svg"
        check("a compressible file travels compressed and lands exact",
              got.is_file() and got.read_bytes() == notes.read_bytes()
              and ra.uploaded_bytes < notes.stat().st_size / 10,
              f"sent {ra.uploaded_bytes} for {notes.stat().st_size}")
    # A big file goes up in parts (the CAD model is ~300 MB; this is 11 MB
    # with the part size shrunk to R2's 5 MB minimum).
    from app.db.sync import client as client_mod
    old = client_mod.SINGLE_PUT_MAX, client_mod.PART_SIZE
    client_mod.SINGLE_PUT_MAX, client_mod.PART_SIZE = 1024 * 1024, 5 * 1024 * 1024
    try:
        with a.env():
            model = docs.root("cad") / "robot.glb"
            model.write_bytes(os.urandom(11 * 1024 * 1024))
        ra = a.sync(); b.sync(); b.sync()
    finally:
        client_mod.SINGLE_PUT_MAX, client_mod.PART_SIZE = old
    with b.env():
        got = docs.root("cad") / "robot.glb"
        check("an incompressible large file travels in parts, uncompressed", ra.ok and got.is_file() and
              got.read_bytes() == model.read_bytes(), str(ra.errors))
    with a.env():
        slide.unlink()
    a.sync(); b.sync()
    with b.env():
        check("a removed slide is removed", not (docs.root("judges_slides") / "01 Robot.png").exists())

    relay_section(a, b, url, home_token)

    print("\nRobot logs")
    log = ROOT / "TEST_LOGS" / "akit_26-08-17_02-59-21.wpilog"
    if not check("test log present", log.is_file(), str(log)):
        return
    from app.robot import ingest
    # Give B a different enum code order first, so the remap has work to do.
    # A interns '' as 0 and 'HF Joystick' as 1 (order of appearance); B holds
    # them the other way round.
    b.sql("INSERT INTO signal (device_type, name, value_kind) "
          "VALUES ('Robot', 'DriverStation/Joystick0/Name', 'enum')")
    jsig = b.one("SELECT id FROM signal WHERE name = 'DriverStation/Joystick0/Name'")
    b.sql("INSERT INTO signal_enum (signal_id, code, label) VALUES (?, 0, 'HF Joystick'), (?, 1, '')",
          (jsig, jsig))
    with a.env():
        res = ingest.import_log(log, a.db.path)
    check("A imports the log", res.stored_rows > 0)
    ra = a.sync()
    check("A uploads its bundle", ra.uploaded_bytes > 0 and ra.ok, str(ra.errors))
    sizes = a.db.fetchone("SELECT bundle_bytes, raw_bytes FROM sync_session_blob")
    print(f"    log {log.stat().st_size / 1e6:.2f} MB → bundle {sizes[0] / 1e6:.2f} MB, "
          f"original zstd'd {(sizes[1] or 0) / 1e6:.2f} MB")
    rb = b.sync()
    uid = a.one("SELECT uid FROM log_session")
    sid_b = b.one("SELECT id FROM log_session WHERE uid = ?", (uid,))
    check("the session arrives on B", sid_b is not None, str(rb.errors))
    check("B knows A imported it (origin from the bundle's blob, A's name)",
          b.one("SELECT origin || '|' || origin_name FROM log_session WHERE uid = ?",
                (uid,)) == "check-a|a",
          str(b.one("SELECT origin || '|' || origin_name FROM log_session WHERE uid = ?", (uid,))))
    check("A's own import has no origin (it's the root)",
          a.one("SELECT origin FROM log_session WHERE uid = ?", (uid,)) is None)
    if sid_b is not None:
        sid_a = a.one("SELECT id FROM log_session WHERE uid = ?", (uid,))
        q_series = "SELECT COUNT(*) FROM series WHERE session_id = ?"
        check("same series", a.one(q_series, (sid_a,)) == b.one(q_series, (sid_b,)))
        q_bits = ("SELECT d.device_type, d.can_id, g.device_type, g.name, s.t_ms, s.ord, "
                  "CASE WHEN g.value_kind = 'enum' THEN NULL ELSE hex(CAST(s.v AS BLOB)) END, "
                  "quote(s.v) FROM samples.sample s JOIN series se ON se.id = s.series_id "
                  "JOIN device d ON d.id = se.device_id JOIN signal g ON g.id = se.signal_id "
                  "WHERE se.session_id = ? AND g.value_kind = 'num' ORDER BY 1, 2, 3, 4, 5, 6")
        sa = [tuple(r) for r in a.db.fetchall(q_bits, (sid_a,))]
        sb = [tuple(r) for r in b.db.fetchall(q_bits, (sid_b,))]
        check("every numeric sample identical, bit for bit", sa == sb and len(sa) > 0,
              f"{len(sa)} vs {len(sb)}")
        q_1s = ("SELECT d.device_type, d.can_id, g.name, r.t_s, quote(r.v_min), quote(r.v_max), "
                "quote(r.v_avg), r.n FROM samples.sample_1s r JOIN series se ON se.id = r.series_id "
                "JOIN device d ON d.id = se.device_id JOIN signal g ON g.id = se.signal_id "
                "WHERE se.session_id = ? AND g.value_kind = 'num' ORDER BY 1, 2, 3, 4")
        check("every per-second rollup identical",
              [tuple(r) for r in a.db.fetchall(q_1s, (sid_a,))] ==
              [tuple(r) for r in b.db.fetchall(q_1s, (sid_b,))])
        q_fault = "SELECT COUNT(*) FROM fault_event WHERE session_id = ?"
        check("same faults", a.one(q_fault, (sid_a,)) == b.one(q_fault, (sid_b,)))
        q_enum = ("SELECT g.device_type || '/' || g.name, e.label, s.t_ms FROM samples.sample s "
                  "JOIN series se ON se.id = s.series_id JOIN signal g ON g.id = se.signal_id "
                  "JOIN signal_enum e ON e.signal_id = g.id AND e.code = s.v "
                  "WHERE se.session_id = ? ORDER BY 1, 3")
        ea = [tuple(r) for r in a.db.fetchall(q_enum, (sid_a,))]
        eb = [tuple(r) for r in b.db.fetchall(q_enum, (sid_b,))]
        check("enum samples read the same labels", ea == eb and len(ea) > 0, f"{len(ea)} vs {len(eb)}")
        joy = [r for r in eb if r[0] == "Robot/DriverStation/Joystick0/Name"]
        check("…including one B coded differently", len(joy) > 0 and
              any(r[1] == "HF Joystick" for r in joy), str(joy[:3]))
        # A format-1 bundle (gzip, samples as tables) still lands: make one
        # from A's session and import it on a third machine.
        import gzip as _gzip
        import sqlite3 as _sqlite3
        from app.db.sync import bundle as bundle_mod
        with a.env():
            v2 = bundle_mod.build(a.db.path, sid_a, tmp / "legacy")
            flat = bundle_mod.open_bundle(v2, tmp / "legacy")
        lc = _sqlite3.connect(flat)
        lc.execute("DROP TABLE colblob")
        lc.execute("UPDATE bundle_meta SET v = '1' WHERE k = 'format'")
        lc.commit()
        lc.close()
        v1 = tmp / "legacy" / "old.pitlog.gz"
        with open(flat, "rb") as src, _gzip.open(v1, "wb") as dst:
            dst.write(src.read())
        c = Machine("c", tmp, url, pit_token)
        with c.env():
            sid_c = bundle_mod.import_bundle(c.db.path, v1, tmp / "legacy")
        check("a format-1 bundle still imports",
              [tuple(r) for r in c.db.fetchall(q_bits, (sid_c,))] == sa)
        b.sql("UPDATE log_session SET match_key = 'qm14' WHERE uid = ?", (uid,))
        b.sync(); a.sync()
        check("a match key typed on B reaches A",
              a.one("SELECT match_key FROM log_session WHERE uid = ?", (uid,)) == "qm14")
        blob = a.one("SELECT raw_sha FROM sync_session_blob WHERE uid = ?", (uid,))
        check("the original log went up for the archive", bool(blob))
        a.sync(); b.sync()
        ra, rb = a.sync(), b.sync()
        check("still nothing to move afterwards",
              ra.pushed == rb.pushed == ra.pulled == rb.pulled == 0,
              f"A {ra.pushed}/{ra.pulled}, B {rb.pushed}/{rb.pulled}")
        a.sql("DELETE FROM log_session WHERE uid = ?", (uid,))
        a.sync(); b.sync()
        check("deleting a session travels", b.one("SELECT COUNT(*) FROM log_session WHERE uid = ?", (uid,)) == 0)

    print("\nAnalysis: runs, the crew's verdicts, a pit's own boards")
    transcript = json.dumps({"analyst": [{"role": "tool", "content": "x" * 64}] * 6000})
    a.sql("""INSERT INTO analysis_run (session_uids, question, analyst, status, insights,
             transcript, stats) VALUES ('["s1"]', 'q', 'qwen3:8b', 'published',
             '{"findings": [{"id": "sag"}]}', ?, '{}')""", (transcript,))
    run_uid = a.one("SELECT uid FROM analysis_run ORDER BY id DESC LIMIT 1")
    run_a = a.one("SELECT id FROM analysis_run WHERE uid = ?", (run_uid,))
    a.sql("INSERT INTO analysis_feedback (run_id, finding_id, rating) VALUES (?, 'sag', 'useful')",
          (run_a,))
    a.sql("""INSERT INTO analysis_board (uid, title, spec, session_uid)
             VALUES ('s1:pit-check-a-1', 'A board', '{"title": "A board", "schema": 1}', 's1')""")
    a.sync(); b.sync()
    run_b = b.one("SELECT id FROM analysis_run WHERE uid = ?", (run_uid,))
    check("a run made on A reaches B", run_b is not None)
    check(f"its transcript survives the trip, packed ({len(transcript) // 1024} KB > the "
          f"hub's 256 KB row limit unpacked)",
          b.one("SELECT transcript FROM analysis_run WHERE uid = ?", (run_uid,)) == transcript)
    check("A's rating reaches B, on B's own copy of the run",
          b.one("SELECT rating FROM analysis_feedback WHERE run_id = ? AND finding_id = 'sag'",
                (run_b,)) == "useful")
    check("a pit's own board reaches the other pit",
          b.one("SELECT title FROM analysis_board WHERE uid = 's1:pit-check-a-1'") == "A board")
    b.sql("""INSERT INTO analysis_feedback (run_id, finding_id, score) VALUES (?, '', 5)
             ON CONFLICT (run_id, finding_id) DO UPDATE SET score = excluded.score""", (run_b,))
    b.sql("UPDATE analysis_feedback SET rating = 'wrong', acted = 1 "
          "WHERE run_id = ? AND finding_id = 'sag'", (run_b,))
    b.sync(); a.sync()
    check("B's rank for the run reaches A",
          a.one("SELECT score FROM analysis_feedback WHERE run_id = ? AND finding_id = ''",
                (run_a,)) == 5)
    check("B's change of mind reaches A (one row per finding, everywhere)",
          a.one("SELECT rating || acted FROM analysis_feedback WHERE run_id = ? "
                "AND finding_id = 'sag'", (run_a,)) == "wrong1"
          and a.one("SELECT COUNT(*) FROM analysis_feedback WHERE run_id = ?", (run_a,)) == 2)
    ra, rb = a.sync(), b.sync()
    check("and then nothing more to move", ra.pushed == rb.pushed == 0,
          f"A {ra.pushed}, B {rb.pushed}")

    print("\nHome")
    home = HubClient(url, home_token, "home", "home", "check")
    target = b.one("SELECT uid FROM checklist WHERE name = 'Load Out'")
    r = home.push([{"tbl": "checklist", "uid": target, "op": "upsert", "base": 0,
                    "data": {"name": "Load Out (home)", "position": 1}}], force=True)
    check("home may force", r["results"][0]["status"] == "ok", str(r))
    pit = HubClient(url, pit_token, "check-a", "a", "check")
    r = pit.push([{"tbl": "checklist", "uid": target, "op": "upsert", "base": 0,
                   "data": {"name": "nope", "position": 1}}], force=True)
    check("a pit may not", r["results"][0]["status"] == "conflict", str(r))
    home.push([{"tbl": "analysis_board", "uid": "board-check", "op": "upsert", "base": 0,
                "data": {"title": "Check board", "status": "ok", "vitals": []}}], force=True)
    a.sync()
    check("home's edit lands", a.one("SELECT COUNT(*) FROM checklist WHERE name = 'Load Out (home)'") == 1)
    check("an analysis board lands", a.one("SELECT title FROM analysis_board WHERE uid = 'board-check'") == "Check board")
    home.push([
        {"tbl": "tba_event", "uid": "2026check", "op": "upsert", "base": 0,
         "data": {"name": "Check Regional", "start_date": "2026-10-07",
                  "end_date": "2026-10-09", "timezone": "America/Chicago"}},
        {"tbl": "tba_match", "uid": "2026check_qm14", "op": "upsert", "base": 0,
         "data": {"event_key": "2026check", "comp_level": "qm", "match_number": 14,
                  "red_teams": ["3937", "16", "118"], "blue_teams": ["254", "1678", "971"],
                  "red_score": 120, "blue_score": 98, "winning_alliance": "red",
                  "actual_time": 1791398400000}}], force=True)
    a.sync()
    check("home's TBA event and match land on a pit",
          a.one("SELECT name FROM tba_event WHERE uid = '2026check'") == "Check Regional"
          and a.one("SELECT match_key FROM tba_match WHERE uid = '2026check_qm14'") == "qm14")
    check("…and a pit never sends them back (pulled only)",
          a.one("SELECT COUNT(*) FROM sync_outbox WHERE tbl LIKE 'tba_%'") == 0)
    home.push(HOME_FEEDS + HOME_DATASETS, force=True)
    a.sync()
    q = json.loads(a.one("SELECT data FROM home_dataset WHERE uid = 'quality'") or "{}")
    check("home's datasets land (home_dataset, pulled only), rows and highlight intact",
          a.one("SELECT COUNT(*) FROM home_dataset") == len(HOME_DATASETS)
          and q.get("highlight", {}).get("rows") == [5] and q.get("total_rows") == 1186
          and a.one("SELECT COUNT(*) FROM sync_outbox WHERE tbl = 'home_dataset'") == 0)
    us = json.loads(a.one("SELECT data FROM tba_team WHERE uid = 'frc3937'") or "{}")
    check("home's award feeds land: tba_team, tba_rival, tba_fact (pulled only)",
          a.one("SELECT nickname FROM tba_team WHERE uid = 'frc16'") == "Bomb Squad"
          and us.get("quality_awards") == 10 and us.get("award_streak") == 13
          and a.one("SELECT team_number FROM tba_rival WHERE uid = 'frc16'") == "16"
          and a.one("SELECT text FROM tba_fact WHERE uid = 'our_streak'").startswith("Breakaway")
          and a.one("SELECT COUNT(*) FROM sync_outbox WHERE tbl LIKE 'tba_%'") == 0)
    # B as a build from before the table: it pulled past the rows and kept
    # none. Its first cycle on this build must fetch them once.
    from app.db.sync import tables as sync_tables
    b.sync()
    for t in ("tba_team", "tba_rival", "tba_fact", "home_dataset"):
        b.sql(f"DELETE FROM {t}")
    b.sql("UPDATE sync_meta SET v = ? WHERE k = 'known_tables'",
          (json.dumps(sorted(sync_tables.CATCH_UP_BASELINE)),))
    rb = b.sync()
    check("a pit that skipped the table on an older build catches up on it",
          b.one("SELECT COUNT(*) FROM tba_team") == 2
          and b.one("SELECT COUNT(*) FROM tba_rival") == 1
          and b.one("SELECT COUNT(*) FROM tba_fact") == 1
          and b.one("SELECT COUNT(*) FROM home_dataset") == len(HOME_DATASETS)
          and "tba_fact" in json.loads(
              b.one("SELECT v FROM sync_meta WHERE k = 'known_tables'")),
          str(rb.errors))
    rb = b.sync()
    check("…once", b.one("SELECT v FROM sync_meta WHERE k = 'catch_up'") == "null"
          and rb.ok, str(rb.errors))
    status = home.status()
    check("the hub lists both machines",
          {"check-a", "check-b"} <= {m["id"] for m in status.get("machines", [])})
    import urllib.error
    req = urllib.request.Request(url + "/v1/machine/check-b", method="DELETE", headers={
        "Authorization": f"Bearer {pit_token}", "X-Pit-Machine": "check-a"})
    try:
        urllib.request.urlopen(req, timeout=10)
        refused = False
    except urllib.error.HTTPError as e:
        refused = e.code == 403
    check("a pit may not forget a machine", refused)
    req = urllib.request.Request(url + "/v1/machine/check-b", method="DELETE", headers={
        "Authorization": f"Bearer {home_token}", "X-Pit-Machine": "home"})
    urllib.request.urlopen(req, timeout=10).close()
    check("home may", "check-b" not in {m["id"] for m in home.status().get("machines", [])})
    if pit_token == PIT_TOKEN:
        old = HubClient(url, OLD_PIT_TOKEN, "check-old", "old", "check")
        check("while rotating, the old pit token still works", "head" in old.status())


if __name__ == "__main__":
    sys.exit(main())
