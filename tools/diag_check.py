"""
Check diagnostics end to end: the spool, the crash-log harvest (fed by a
real crashing process), sessions, the check suite, the service and panel,
and the sync engine carrying `pit_event` / `pit_status` home. Exit 0/1.

    uv run tools/diag_check.py

Runs in its own scratch data tree (never this Mac's `data/`) unless
`tools/validate.py` hands it one. The engine is driven against a stub hub
that records what it's sent; `tools/sync_check.py --local` proves the real
hub takes any table, which is all these two need from it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not os.environ.get("PIT_DISPLAY_DATA"):
    os.environ["PIT_DISPLAY_DATA"] = tempfile.mkdtemp(prefix="diag_check_")
for k in [k for k in os.environ if k.startswith("PIT_SECRET_")]:
    del os.environ[k]
SECRET = "pit-test-secret-9f8e7d6c5b4a"
os.environ["PIT_SECRET_DIAG_TEST"] = SECRET
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PIT_LEDS_FAKE", "1")
os.environ.setdefault("PIT_NEXUS_QUIET", "1")
os.environ.setdefault("PIT_SYNC_QUIET", "1")
os.environ.setdefault("PIT_AI_QUIET", "1")
os.environ.pop("PIT_DIAG_QUIET", None)

from app.console import use_utf8  # noqa: E402

use_utf8()

from app import paths  # noqa: E402
from app.diagnostics import harvest, settings as diag_settings, spool  # noqa: E402

_failures: list[str] = []
_QAPP = None     # held: a QApplication in a local is torn down when it goes


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


def _clear_spool() -> None:
    for where in ("outbox", "sent"):
        for f in (spool.root() / where).glob("*.json"):
            f.unlink()
    spool._recent.clear()


def spool_basics() -> None:
    print("Spool")
    _clear_spool()
    eid = spool.record("crash", f"boom with token {SECRET}", detail=f"Bearer {SECRET}\nmore",
                       attachments={"x.log": f"key={SECRET}"})
    ev = spool.load(eid)
    check("an event is written and read back", ev is not None and ev["kind"] == "crash")
    blob = json.dumps(ev)
    check("this machine's credentials never reach a report", SECRET not in blob, blob[:200])
    check("an event names its build and session", "version" in ev.get("build", {}) and "session" in ev)
    ids = [spool.record("loop", "same thing") for _ in range(spool.MAX_PER_KIND_PER_HOUR + 3)]
    check("a runaway reporter is throttled per kind",
          ids.count(None) == 3 and all(ids[:spool.MAX_PER_KIND_PER_HOUR]))
    big = spool.record("report", "big", detail="d" * 500_000,
                       attachments={f"f{i}.log": os.urandom(150_000).hex() for i in range(4)})
    wired = spool.wire(spool.load(big))
    size = len(json.dumps(wired))
    check("a huge report still fits the hub's 256 KB row", size <= spool.MAX_ROW, f"{size} bytes")
    check("…and says which attachments it left out", bool(wired.get("dropped_attachments")))
    small = spool.wire(spool.load(eid))
    check("short text travels plain", isinstance(small["detail"], str))
    packed = spool.pack("abc" * 5000)
    check("long text travels zstd-packed and unpacks", isinstance(packed, dict)
          and spool.unpack(packed) == "abc" * 5000)
    spool.mark_sent(eid)
    check("a sent event leaves the outbox and is kept", eid not in spool.pending()
          and (spool.root() / "sent" / f"{eid}.json").exists())
    recent = spool.recent(50)
    check("recent lists newest first, sent and waiting",
          recent and recent[0]["id"] >= recent[-1]["id"] and any(e["sent"] for e in recent))


def harvest_synthetic() -> None:
    print("Harvest (written crash.log)")
    _clear_spool()
    spool.save_state(crash_offset=0, seen=[])
    log = paths.data("crash.log")
    log.write_text(
        "2026-10-18 10:00:00  launch  0.2.1-beta.9  \n"
        "2026-10-18 10:01:00  UNHANDLED KeyError\n"
        "Traceback (most recent call last):\n"
        '  File "C:\\pit\\_internal\\app\\rotation.py", line 88, in _tick\n'
        "    x = d['k']\n"
        "KeyError: 'k'\n"
        "2026-10-18 10:02:00  UNHANDLED OSError in thread sync-cycle\n"
        "Traceback (most recent call last):\n"
        '  File "/x/app/db/sync/engine.py", line 12, in cycle\n'
        "OSError: disk\n"
        "2026-10-18 10:03:00  FROZEN: the GUI thread has not responded for 21s. Every thread's stack follows (the main thread is the frozen one):\n"
        "Current thread 0x0001 (most recent call first):\n"
        '  File "/x/app/crash_log.py", line 132 in watch\n'
        "\n"
        "Thread 0x0002 (most recent call first):\n"
        '  File "/x/app/robot/stage.py", line 300 in merge\n'
        '  File "/x/main.py", line 400 in <module>\n'
        "2026-10-18 10:03:30  responsive again after 30s\n"
        "2026-10-18 11:00:00  launch  0.2.1-beta.9  \n"
        "Fatal Python error: Segmentation fault\n"
        "\n"
        "Current thread 0x0002 (most recent call first):\n"
        '  File "/x/app/music/engine.py", line 55 in play\n'
        '  File "/x/main.py", line 400 in <module>\n',
        encoding="utf-8")
    ids = harvest.crash_log(startup=True)
    kinds = sorted(spool.load(i)["kind"] for i in ids)
    check("each incident is one event", kinds == ["crash", "crash", "fatal", "freeze", "freeze_end"], kinds)
    evs = {spool.load(i)["kind"] + str(n): spool.load(i) for n, i in enumerate(ids)}
    crash = next(e for e in evs.values() if e["kind"] == "crash")
    check("a crash's signature names the app frame, on any OS",
          "app/rotation.py:88 _tick" in crash["signature"], crash["signature"])
    check("a crash keeps its time from the log", crash["at"].startswith("2026-10-18T10:01:00"))
    thread = [e for e in evs.values() if "thread sync-cycle" in e["summary"]]
    check("a summary doesn't repeat the exception's name",
          not [e for e in evs.values() if "KeyError: KeyError" in e["summary"]
               or "OSError: OSError" in e["summary"]], [e["summary"] for e in evs.values()])
    check("a worker thread's exception is reported", bool(thread))
    freeze = next(e for e in evs.values() if e["kind"] == "freeze")
    check("a freeze names where the main thread was stuck",
          "app/robot/stage.py:300 merge" in freeze["signature"], freeze["signature"])
    fatal = next(e for e in evs.values() if e["kind"] == "fatal")
    check("a native crash is fatal and names its frame", fatal["severity"] == "fatal"
          and "app/music/engine.py:55" in fatal["signature"], fatal["signature"])
    check("nothing is harvested twice", harvest.crash_log() == [])
    text = log.read_text(encoding="utf-8")
    log.write_text("[trimmed]\n" + text[-600:], encoding="utf-8")
    check("a trimmed log isn't sent again", harvest.crash_log() == [])
    with open(log, "a", encoding="utf-8") as f:
        f.write("2026-10-18 12:00:00  UNHANDLED ValueError\nTraceback (most recent call last):\n"
                '  File "/x/app/x.py", line 1, in f\nValueError: v\n')
    check("…but a new crash after the trim is", len(harvest.crash_log()) == 1)

    # The first harvest on a machine: its crash.log may hold months of history.
    _clear_spool()
    (spool.root() / "state.json").unlink()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.write_text(
        "2026-01-02 10:00:00  UNHANDLED KeyError\nTraceback (most recent call last):\n"
        '  File "/x/app/old.py", line 1, in f\nKeyError: old\n'
        f"{now}  UNHANDLED KeyError\nTraceback (most recent call last):\n"
        '  File "/x/app/new.py", line 1, in f\nKeyError: new\n', encoding="utf-8")
    ids = harvest.crash_log(startup=True)
    sigs = [spool.load(i)["signature"] for i in ids]
    check("a machine's first harvest sends the last week, not old history",
          len(sigs) == 1 and "app/new.py" in sigs[0], sigs)
    check("…and the old history isn't sent later either", harvest.crash_log() == [])


def harvest_real_crash() -> None:
    print("Harvest (a real crashing process)")
    _clear_spool()
    code = (
        "import sys, threading; sys.path.insert(0, %r)\n"
        "from app import crash_log; crash_log.install()\n"
        "def bad(): raise RuntimeError('worker blew up')\n"
        "t = threading.Thread(target=bad, name='diag-worker'); t.start(); t.join()\n"
        "raise ZeroDivisionError('main blew up')\n") % str(ROOT)
    subprocess.run([sys.executable, "-c", code], capture_output=True, env=os.environ, timeout=60)
    ids = harvest.crash_log()
    summaries = [spool.load(i)["summary"] for i in ids]
    check("crash_log's own hooks are read back: the main thread",
          any("ZeroDivisionError" in s for s in summaries), summaries)
    check("…and a worker thread (threading.excepthook)",
          any("RuntimeError" in s and "thread diag-worker" in s for s in summaries), summaries)


def sessions() -> None:
    print("Sessions")
    spool._session = None
    spool._previous = None
    (spool.root() / "session.json").unlink(missing_ok=True)
    check("a first launch finds no unclean exit", spool.begin_session() is None)
    sid = spool.current_session_id()
    spool.beat()
    spool._session = None          # the process "dies" here: no end_session()
    previous = spool.begin_session()
    check("a launch after one that never closed finds it",
          previous is not None and previous.get("id") == sid)
    spool.end_session()
    check("a clean exit removes the mark", not (spool.root() / "session.json").exists())
    spool._session = None
    spool._previous = None
    spool.begin_session()


def settings_defaults() -> None:
    print("Who reports")
    check("a checkout reports by default", diag_settings.default_on() and diag_settings.enabled())
    diag_settings.save(enabled=False)
    check("an admin can turn it off", not diag_settings.enabled())
    diag_settings.save(enabled=None)
    check("…and back to the default", diag_settings.enabled())


def service_and_panel():
    print("Service, check suite, panel")
    from PyQt6.QtCore import QEventLoop, QTimer, Qt
    from PyQt6.QtWidgets import QApplication
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    global _QAPP
    _QAPP = QApplication.instance() or QApplication(sys.argv[:1])
    import app.db.migrations  # noqa: F401
    from app.admin import init_admin
    from app.config import init_config
    from app.db import init_db
    init_config()
    init_db()
    init_admin()
    from app.db.sync.service import init_sync
    init_sync()
    from app.diagnostics import checks
    from app.diagnostics.service import init_diagnostics
    svc = init_diagnostics()
    _clear_spool()
    svc.start()
    kinds = [e["kind"] for e in spool.recent(20)]
    check("a launch is recorded", "launch" in kinds, kinds)

    results = svc.run_quick()
    by = {r.name: r for r in results}
    check("the quick suite covers machine and services",
          {"process", "disk", "database", "sync", "leds", "responsive"} <= set(by))
    check("a service that isn't started is skipped, not failed",
          by["leds"].status == checks.SKIP and by["nexus"].status in (checks.SKIP, checks.PASS),
          f"leds {by['leds'].status}: {by['leds'].detail}")
    check("the database check runs quick_check", "quick_check ok" in by["database"].detail,
          by["database"].detail)
    check("no quick check fails on a healthy scratch install",
          not [r for r in results if r.status == checks.FAIL],
          "; ".join(f"{r.name}: {r.detail[:80]}" for r in results if r.status == checks.FAIL))

    r = checks.run_one("broken", lambda: 1 / 0)
    check("a check that raises is a failure with its traceback",
          r.status == checks.FAIL and "ZeroDivisionError" in r.detail)

    # A check going bad then good is two events; staying bad is none.
    svc._prev_status["disk"] = checks.PASS
    svc._transitions([checks.Check("disk", checks.FAIL, "0.5 GB free")])
    svc._transitions([checks.Check("disk", checks.FAIL, "0.5 GB free")])
    svc._transitions([checks.Check("disk", checks.PASS, "fine")])
    ks = [e["kind"] for e in spool.recent(50) if "disk" in e["summary"]]
    check("a check changing state is an event, steady state isn't",
          sorted(ks) == ["check_failed", "check_recovered"], ks)

    done = []
    svc.suite_done.connect(done.append)
    svc.run_full()
    loop = QEventLoop()
    deadline = time.monotonic() + 180
    while not done and time.monotonic() < deadline:
        loop.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
    full = done[0] if done else []
    names = {r.name for r in full}
    check("the full suite adds the install checks",
          {"install.network", "install.owlet", "install.diagnostics"} <= names, sorted(names))
    check("the full suite is recorded with every result",
          any(e["kind"] == "check_suite" and len(e["data"].get("results", [])) == len(full)
              for e in spool.recent(50)))

    snap = svc.snapshot()
    check("the status names build, platform, process, services and checks",
          {"build", "platform", "process", "services", "checks", "session", "counts"} <= set(snap),
          sorted(snap))
    check("the status is small", len(json.dumps(snap)) < 64 * 1024, str(len(json.dumps(snap))))
    svc.write_status()
    check("the status is written for the engine", (spool.read_status() or {}).get("at") == snap["at"]
          or bool(spool.read_status()))

    paths.data("qt_warnings.log").write_text("2026-10-18 QObject::disconnect thing\n", encoding="utf-8")
    eid = svc.report_problem(f"Screen B went black at 2:15 (token {SECRET})")
    ev = spool.load(eid)
    check("Report a problem carries the words, the checks and the log tails",
          ev is not None and ev["kind"] == "report" and "Screen B went black" in ev["summary"]
          and "crash.log" in ev["attachments"] and "qt_warnings.log" in ev["attachments"]
          and "── checks ──" in ev["detail"])
    check("…scrubbed of credentials", SECRET not in json.dumps(ev))

    from app.widgets.diagnostics_panel import DiagnosticsPanel
    from app.admin import admin
    panel = DiagnosticsPanel()
    panel.resize(700, 1400)
    panel.show()
    _QAPP.processEvents()
    check("the panel builds and lists recent reports", panel._recent.count() > 0)
    check("switching reporting is hidden while locked", not panel._admin_box.isVisibleTo(panel)
          if not admin.unlocked else True)

    svc.shutdown()
    kinds = [e["kind"] for e in spool.recent(5)]
    check("a clean exit is recorded and ends the session",
          "exit" in kinds and not (spool.root() / "session.json").exists(), kinds)
    panel.close()
    del loop
    QTimer.singleShot(0, lambda: None)


class _StubHub:
    """Takes pushes the way the hub does (any table, a seq each) and keeps them."""

    def __init__(self):
        self.rows: list[dict] = []
        self.seq = 100
        self.timeout = 30.0

    def push(self, changes):
        out = []
        for c in changes:
            self.seq += 1
            self.rows.append(c)
            assert len(json.dumps(c["data"])) < 256 * 1024, "row over the hub's cap"
            out.append({"tbl": c["tbl"], "uid": c["uid"], "status": "ok", "seq": self.seq})
        return {"head": self.seq, "results": out}


def engine() -> None:
    print("Sync engine → pit_event / pit_status")
    import sqlite3
    from app.db import db
    from app.db.sync import tables
    from app.db.sync.engine import Engine, Report
    hub = _StubHub()
    eng = Engine(db.path, hub, "diag-mac", {"machine_name": "Diag Mac"})
    conn = eng._connect()
    try:
        conn.execute("DELETE FROM sync_outbox")
        _clear_spool()
        a = spool.record("crash", "one")
        b = spool.record("freeze", "two")
        spool.write_status({"at": "2026-10-18T12:00:00-05:00", "schema": 1})
        rep = Report()
        eng._diagnostics(conn, rep)
        eng._push(conn, rep)
        sent = {(r["tbl"], r["uid"]): r for r in hub.rows}
        check("each waiting event is pushed once, uid <machine>:<id>",
              (tables.PIT_EVENT, f"diag-mac:{a}") in sent and (tables.PIT_EVENT, f"diag-mac:{b}") in sent)
        st = sent.get((tables.PIT_STATUS, "diag-mac"))
        check("the status is pushed as one row per machine", st is not None and st["op"] == "upsert")
        ev = sent[(tables.PIT_EVENT, f"diag-mac:{a}")]["data"]
        check("rows name the machine", ev.get("machine_id") == "diag-mac"
              and ev.get("machine_name") == "Diag Mac" and st["data"].get("machine_id") == "diag-mac")
        check("pushed events move to sent", spool.pending() == [])
        check("the status the hub took is remembered",
              eng._meta(conn, "status_at") == "2026-10-18T12:00:00-05:00")
        n = len(hub.rows)
        eng._diagnostics(conn, rep)
        eng._push(conn, rep)
        check("nothing is sent twice", len(hub.rows) == n)

        diag_settings.save(enabled=False)
        c = spool.record("crash", "three")
        spool.write_status({"at": "2026-10-18T12:10:00-05:00"})
        eng._diagnostics(conn, rep)
        eng._push(conn, rep)
        check("reporting off: nothing leaves, it waits", len(hub.rows) == n and c in spool.pending())
        diag_settings.save(enabled=None)
        eng._diagnostics(conn, rep)
        eng._push(conn, rep)
        check("…and goes once it's back on", any(r["uid"] == f"diag-mac:{c}" for r in hub.rows[n:]))

        # A queued event the spool dropped must not loop or push a tombstone.
        conn.execute("INSERT INTO sync_outbox (tbl, uid, op) VALUES (?, ?, 'upsert')",
                     (tables.PIT_EVENT, "diag-mac:gone"))
        n = len(hub.rows)
        eng._diagnostics(conn, rep)
        eng._push(conn, rep)
        check("an event the spool dropped leaves the outbox quietly",
              len(hub.rows) == n and not conn.execute(
                  "SELECT 1 FROM sync_outbox WHERE uid = 'diag-mac:gone'").fetchone())

        before = conn.execute("SELECT COUNT(*) FROM sync_pending").fetchone()[0]
        eng._apply(conn, {"tbl": tables.PIT_EVENT, "uid": "other:x", "seq": 5, "op": "upsert",
                          "data": {"kind": "crash"}}, rep)
        eng._apply(conn, {"tbl": tables.PIT_STATUS, "uid": "other", "seq": 6, "op": "upsert",
                          "data": {}}, rep)
        check("other machines' reports are ignored, never applied",
              conn.execute("SELECT COUNT(*) FROM sync_pending").fetchone()[0] == before
              and not rep.errors)
        check("a build doesn't catch up on pushed-only tables",
              not set(tables.PUSHED_ONLY) & set(tables.APPLIED))

        # Quit: the exit event and the final status go at once, nothing else.
        conn.execute("INSERT INTO sync_outbox (tbl, uid, op) VALUES ('setting', 'diag-x', 'upsert')")
        x = spool.record("exit", "Closed normally after 5 min", severity="info")
        spool.write_status({"at": "2026-10-18T12:20:00-05:00", "final": True})
        n = len(hub.rows)
        pushed = eng.farewell()
        took = [(r["tbl"], r["uid"]) for r in hub.rows[n:]]
        check("at quit, the exit event and the final status are pushed at once",
              pushed == 2 and (tables.PIT_EVENT, f"diag-mac:{x}") in took
              and (tables.PIT_STATUS, "diag-mac") in took, took)
        check("…and nothing else goes at quit", all(t in tables.PUSHED_ONLY for t, _ in took)
              and conn.execute("SELECT 1 FROM sync_outbox WHERE uid = 'diag-x'").fetchone())
        check("…on a short timeout, restored after", hub.timeout == 30.0)
        conn.execute("DELETE FROM sync_outbox WHERE uid = 'diag-x'")
    except sqlite3.Error as e:
        check("engine", False, str(e))
    finally:
        conn.close()


def main() -> int:
    print(f"data: {paths.data_root()}\n")
    spool_basics()
    harvest_synthetic()
    harvest_real_crash()
    sessions()
    settings_defaults()
    service_and_panel()
    engine()
    print()
    if _failures:
        print(f"FAILED — {len(_failures)}: {', '.join(_failures)}")
        return 1
    print("OK — diagnostics check out.")
    return 0


if __name__ == "__main__":
    status = main()
    sys.stdout.flush()
    os._exit(status)
