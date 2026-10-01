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
PIT_TOKEN = "check-pit-token"
HOME_TOKEN = "check-home-token"
OLD_PIT_TOKEN = "check-old-pit-token"      # a token mid-rotation (--local only)


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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_hub(tmp: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    hub_dir = ROOT / "sync-hub"
    if not (hub_dir / "node_modules").exists():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=hub_dir, check=True)
    proc = subprocess.Popen(
        ["npx", "wrangler", "dev", "--local", "--ip", "127.0.0.1", "--port", str(port),
         "--persist-to", str(tmp / "hub-state"),
         "--var", f"PIT_TOKEN:{PIT_TOKEN},{OLD_PIT_TOKEN}", "--var", f"HOME_TOKEN:{HOME_TOKEN}"],
        cwd=hub_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url + "/healthz", timeout=2):
                return proc, url
        except Exception:
            time.sleep(1)
    proc.kill()
    raise SystemExit("wrangler dev didn't come up on :%d" % port)


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


def run(tmp: Path, url: str, pit_token: str, home_token: str) -> None:
    a = Machine("a", tmp, url, pit_token)
    b = Machine("b", tmp, url, pit_token)

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
