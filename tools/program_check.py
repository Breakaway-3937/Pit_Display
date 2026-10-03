"""
The two overhead screens move as one unit. Exit 0/1.

    uv run tools/program_check.py

Boots both presentation screens offscreen on a fresh install with a robot log
(the fixture), an event with our matches (a snapshot from the Nexus spec's
examples, as team 16, the examples' team), and facts from home, then walks
the whole program (`app/program.py`) through the real driver
(`app/rotation.py`) and checks, at every stop, that both screens are on the
same stop and show the pair the program names: Diagnostics opposite Robot
Info, Next match opposite Our schedule, a fact opposite a fact. Also: a jump
on one screen's picker moves both; a screen pinned to a face keeps its place
in the program and rejoins in step; a stop whose data goes away leaves both
screens together; the pit-network state agrees with the native screens.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

SCRATCH = ROOT / ".validation" / "program-check"
FIXTURE = ROOT / "TEST_LOGS" / "fixture_faults_2026-07-29.pitlog.zst"
_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        _failures.append(name)
    return ok


def main() -> int:
    use_utf8()
    shutil.rmtree(SCRATCH, ignore_errors=True)
    (SCRATCH / "data").mkdir(parents=True)
    os.environ.update(PIT_DISPLAY_DATA=str(SCRATCH), QT_QPA_PLATFORM="offscreen",
                      PIT_LEDS_FAKE="1", PIT_NEXUS_FAKE="1", PIT_NEXUS_QUIET="1",
                      PIT_SYNC_QUIET="1", PIT_AI_QUIET="1", PIT_CAD_PORT="0")
    from PyQt6 import QtWebEngineWidgets  # noqa: F401  (before QApplication)
    from PyQt6.QtWidgets import QApplication
    qapp = QApplication(sys.argv[:1])

    import app.db.migrations  # noqa: F401
    from app.config import config, init_config
    from app.db import init_db
    init_config()
    db = init_db()
    from app.cad_assets import init_cad_assets
    from app.checklist import init_checklist
    from app.judges_slides import init_judges_slides
    from app.leds import init_leds
    from app.nexus import init_nexus, nexus
    from app.nexus.alerts import init_alerts
    from app.rotation import init_rotation, rotation
    for fn in (init_judges_slides, init_cad_assets, init_leds, init_checklist,
               init_nexus, init_alerts):
        fn()
    init_rotation()

    # A robot log, an event with our matches, and facts from home.
    from app.db.sync import bundle
    (SCRATCH / "bundle").mkdir()
    bundle.import_bundle(db.path, FIXTURE, SCRATCH / "bundle")
    config.set_team(16)
    from app.nexus import api
    # The spec's examples use made-up teams; the latest mid-event snapshot,
    # with team 700's slots given to 16 (an app team), is our event.
    import json
    snaps = api.load_fixtures()["event_status"]
    snap = api.EventStatus.from_json(json.loads(json.dumps(snaps[-1]).replace('"700"', '"16"')))
    if not check("an event mid-way with our matches (Nexus spec examples)",
                 len(snap.matches_for("16")) >= 3 and snap.next_for("16") is not None,
                 f"{len(snap.matches_for('16'))} matches"):
        return 1
    nexus._event_key = snap.event_key or "2025inind"
    nexus._status = snap
    for i, (cat, text) in enumerate([
            ("3937", "Breakaway has won 53 awards and 10 blue banners since 2012."),
            ("3937", "Breakaway has brought home an award 13 seasons in a row (since 2014)."),
            ("arkansas", "Arkansas has had 81 FRC teams; 8 played in 2026."),
            ("arkansas", "An Arkansas team has won the Arkansas Regional 11 of 13 years.")]):
        db.execute("INSERT INTO tba_fact (uid, category, text, sort, data) "
                   "VALUES (?, ?, ?, ?, '{}')", (f"f{i}", cat, text, i))

    from app import program
    rotation.refresh()
    stops = program.stops()
    keys = [s.key for s in stops]
    print("\nThe program")
    print("   ", " · ".join(keys))
    check("the robot stop is in (a log exists)", "robot" in keys)
    check("the event stop is in (our matches exist)", "event" in keys)
    check("one \"Did you know?\" pair is in (examples only)", sum(k.startswith("fact:") for k in keys) == 1)

    from app.windows.presentation_a import PresentationScreenA
    from app.windows.presentation_b import PresentationScreenB
    a, b = PresentationScreenA(), PresentationScreenB()
    for w in (a, b):
        w.resize(1920, 1080)
        w.show()
    config.set_mode("standard")
    qapp.processEvents()

    def page_of(w) -> str:
        cur = w._stack.currentIndex()
        if cur == w._PAGE_NORMAL:
            return "slide:" + (w._slides.current_slide.title if w._slides.current_slide else "")
        return next((k for k, v in w._CONTENT_PAGES.items() if v == cur), f"page{cur}")

    print("\nEvery stop, both screens")
    check("both screens have the same number of stops",
          a._slides.count == b._slides.count == len(stops),
          f"A {a._slides.count}, B {b._slides.count}, program {len(stops)}")
    rotation._set(0)
    qapp.processEvents()
    for n in range(len(stops)):
        stop = stops[rotation.index]
        want_a = stop.a.face or "slide:" + stop.a.title
        want_b = stop.b.face or "slide:" + stop.b.title
        got_a, got_b = page_of(a), page_of(b)
        check(f"stop {rotation.index + 1} {stop.key}: A {want_a[:34]} | B {want_b[:34]}",
              got_a == want_a and got_b == want_b
              and a._slides.current_index == b._slides.current_index == rotation.index,
              "" if got_a == want_a and got_b == want_b else f"got A {got_a[:40]} | B {got_b[:40]}")
        rotation._step()
        qapp.processEvents()
    check("a full pass comes back round to the first stop on both", rotation.index == 0
          and a._slides.current_index == b._slides.current_index == 0)

    print("\nOperator actions")
    robot = keys.index("robot")
    config.set("presentation_b", "slide_index", robot)       # B's picker
    qapp.processEvents()
    check("a jump on B's picker moves A too",
          page_of(a) == "diagnostics" and page_of(b) == "robot_info", f"{page_of(a)} | {page_of(b)}")
    # A matched set at all times (app/overhead.py): choosing checklists on
    # either screen puts both on a checklist; the program keeps its place.
    config.set("presentation_b", "content", "checklist")
    qapp.processEvents()
    rotation._step()
    qapp.processEvents()
    check("checklists chosen on B: both screens show a checklist",
          page_of(a) == page_of(b) == "checklist", f"{page_of(a)} | {page_of(b)}")
    config.set("presentation_b", "content", "rotation")
    qapp.processEvents()
    check("back to the rotation: both rejoin on the same stop",
          page_of(a) == "next_match" and page_of(b) == "schedule", f"{page_of(a)} | {page_of(b)}")

    print("\nThe pit network agrees")
    from app.webcast import state
    sa, sb = state.screen_state("presentation_a"), state.screen_state("presentation_b")
    check("network A and B are on the same stop as the screens",
          sa["rotation"]["index"] == sb["rotation"]["index"] == rotation.index
          and sa["rotation"]["face"] == "next_match" and sb["rotation"]["face"] == "schedule",
          f"{sa['rotation'].get('face')} | {sb['rotation'].get('face')}")
    check("network B's schedule carries our matches and the Nexus credit",
          not sb["rotation"]["schedule"]["empty"] and "FRC.NEXUS" in sb["ledger_right"],
          sb.get("ledger_right", ""))
    fact = next(i for i, k in enumerate(program.keys()) if k.startswith("fact:"))
    rotation._set(fact)
    qapp.processEvents()
    fa = state.screen_state("presentation_a")
    check("a fact stop credits The Blue Alliance (native and network)",
          a._slides.current_slide.credit.startswith("Powered by")
          and "BLUE ALLIANCE" in fa["ledger_right"], fa["ledger_right"])

    print("\nMatched sets: one choice, both screens")
    from app import overhead
    config.set("presentation_b", "content", "rotation")
    want = {
        "next_match": ("next_match", "schedule"),
        "robot":      ("diagnostics", "robot_info"),
        "checklist":  ("checklist", "checklist"),
        "facts":      ("facts", "facts"),
        "analysis":   ("analysis", "robot_info"),
    }
    for name, (wa, wb) in want.items():
        overhead.choose(name)
        qapp.processEvents()
        check(f"{overhead.LABELS[name]}: A {wa} | B {wb}",
              page_of(a) == wa and page_of(b) == wb and overhead.current() == name,
              f"got {page_of(a)} | {page_of(b)}")
    overhead.choose("facts")
    qapp.processEvents()
    check("facts split: A Breakaway's only, B Arkansas's only",
          a._facts._column("3937") and b._facts._column("league")
          and state.facts_state("presentation_a")["league"] == []
          and state.facts_state("presentation_b")["ours"] == [])
    config.set("presentation_b", "content", "robot_info")        # any path, one screen
    qapp.processEvents()
    check("changing one screen's content brings its partner along",
          page_of(a) == "diagnostics" and page_of(b) == "robot_info",
          f"{page_of(a)} | {page_of(b)}")
    config.set("presentation_a", "theme", "light")
    qapp.processEvents()
    check("per-screen controls stay separate: A's theme doesn't change B's",
          config.screen_theme("presentation_a") == "light"
          and config.screen_theme("presentation_b") != "light")
    config.set("presentation_a", "theme", "dark")
    b_list = config.get("presentation_b", "checklist_id")
    config.set("presentation_a", "checklist_id", 987)
    check("…nor does A's choice of checklist",
          config.get("presentation_b", "checklist_id") == b_list,
          f"B's list {config.get('presentation_b', 'checklist_id')!r}")
    config.set("presentation_a", "checklist_id", None)

    print("\nThe event schedule (B's half of Next match)")
    from app import event_schedule
    d0 = event_schedule.build("16")
    ours = [r for r in d0["rows"] if r["ours"]]
    check("only Breakaway's matches (Brayden), no results yet",
          len(d0["rows"]) == len(ours) > 0 and d0["record"] is None,
          f"{len(d0['rows'])} rows, {len(ours)} ours")
    ev = nexus.event_key
    played = [r for r in ours if event_schedule.tba_suffix(r["label"])][:3]
    for r, (red, blue, win) in zip(played, ((120, 98, "red"), (90, 101, "blue"), (77, 77, ""))):
        db.execute("INSERT INTO tba_match (uid, event_key, comp_level, match_key, data) "
                   "VALUES (?, ?, 'qm', ?, ?)",
                   (f"{ev}_{event_schedule.tba_suffix(r['label'])}", ev,
                    event_schedule.tba_suffix(r["label"]),
                    json.dumps({"red_score": red, "blue_score": blue, "winning_alliance": win})))
    d1 = event_schedule.build("16")
    outcomes = [r["outcome"] for r in d1["rows"] if r["ours"] and r["result"]]
    w = outcomes.count("W"); l = outcomes.count("L"); t = outcomes.count("T")
    check("TBA results mark our outcomes and our record counts them",
          d1["record"] == (w, l, t) and len(outcomes) == len(played),
          f"record {d1['record']}, outcomes {outcomes}")
    sb = state.schedule_state()
    check("the network page gets the same record and the TBA credit",
          sb["record"] == list(d1["record"]) and sb["has_results"],
          str(sb["record"]))

    print("\nHome's datasets (R10/R11)")
    sys.path.insert(0, str(ROOT / "tools"))
    from sync_check import HOME_DATASETS
    from app import dataset_settings, datasets
    import json as _json
    for row in HOME_DATASETS:
        db.execute("INSERT INTO home_dataset (uid, title, category, sort, data) VALUES (?, ?, ?, ?, ?)",
                   (row["uid"], row["data"]["title"], row["data"]["category"],
                    row["data"]["sort"], _json.dumps(row["data"])))
    db.execute("DELETE FROM tba_fact")
    check("off until an adult turns them on", datasets.all_datasets() == []
          and state.quality_state()["empty"])
    tf = __import__("app.tba_facts", fromlist=["x"])
    check("fun_facts stays off the screens until an adult clears it", tf.facts() == [])
    dataset_settings.save(enabled=["fun_facts"])
    check("cleared, facts come from home's fun_facts dataset (tba_fact is empty)",
          [f.key for f in tf.facts()] == ["our_streak", "ar_teams"])
    dataset_settings.save(enabled=["quality", "bk_seasons", "bk_records", "ar_leaderboard",
                                   "fun_facts"])
    rotation.refresh()
    qapp.processEvents()
    check("cleared: the program gains the Team stats stop (A quality | B seasons)",
          "stats" in program.keys())
    overhead.choose("stats")
    qapp.processEvents()
    check("Team stats: A the Quality leaderboard | B season by season",
          page_of(a) == "quality" and page_of(b) == "bk_seasons", f"{page_of(a)} | {page_of(b)}")
    qs = state.quality_state()
    check("Quality: ties read T-1, Breakaway flagged by home's highlight, 'Showing 6 of 1,186'",
          [r["rank"] for r in qs["rows"][:2]] == ["T-1", "T-1"]
          and [r["team"] for r in qs["rows"] if r["ours"]] == ["3937"]
          and qs["shown_of"] == "Showing 6 of 1,186", str([r["rank"] for r in qs["rows"]]))
    ss = state.seasons_state()["seasons"]
    gap = next(x for x in ss if x["year"] == 2021)
    check("Seasons: oldest first, 2021 a gap (no record), robot names kept",
          [x["year"] for x in ss] == list(range(2012, 2027)) and gap["wins"] is None
          and next(x for x in ss if x["year"] == 2017)["robot"] == "Dreadnought")
    sa = state.screen_state("presentation_a")
    check("both credit The Blue Alliance (native ledger and network)",
          a._quality.footer_items()[1].startswith("POWERED BY")
          and b._seasons.footer_items()[1].startswith("POWERED BY")
          and "BLUE ALLIANCE" in sa["ledger_right"], sa["ledger_right"])
    overhead.choose("datasets")
    qapp.processEvents()
    from app.widgets.dataset_overlay import side_dataset
    da, dbb = side_dataset("presentation_a", 0), side_dataset("presentation_b", 0)
    check("Datasets: the generic set is every other cleared one (records have cards)",
          page_of(a) == page_of(b) == "dataset" and da and da.key == "ar_leaderboard",
          f"{da and da.key} | {dbb and dbb.key}")
    wd = state.dataset_state("presentation_b")
    ar = next(x for x in wd["sets"] if x["key"] == "ar_leaderboard")
    check("generic: team_key hidden beside team_number, our row flagged",
          "Team key" not in ar["headers"] and [r["ours"] for r in ar["rows"]] == [False, True],
          str(ar["headers"]))
    overhead.choose("fun")
    qapp.processEvents()
    ca, cb = datasets.card("presentation_a", 0), datasets.card("presentation_b", 0)
    check("Fun facts: A a Breakaway record card | B a 'Did you know?' card",
          page_of(a) == page_of(b) == "fact_card" and ca["kind"] == "record"
          and ca["value"] == "21" and cb["kind"] == "fact"
          and cb["text"].startswith("Breakaway has brought"), f"{ca} | {cb}")
    check("cards turn together by the clock (15 s)",
          datasets.card("presentation_a", 15)["record"] != ca["record"]
          and datasets.card("presentation_b", 15)["text"] != cb["text"])
    fc = state.screen_state("presentation_b")
    check("network: B's deck and the TBA credit",
          len(fc["fact_card"]["deck"]) == 2 and "BLUE ALLIANCE" in fc["ledger_right"])
    # Paint every new face once, on both screens: a paint error is a crash.
    painted = []
    for page in ("quality", "bk_seasons", "dataset", "fact_card", "schedule", "facts"):
        for w in (a, b):
            face = w._stack.widget(w._CONTENT_PAGES[page])
            face.resize(1920, 1080)
            face.grab()
            painted.append(page)
    check("every new face paints on both screens", len(painted) == 12)

    print("\nScreen wording (admins edit it in the app)")
    from app import wording
    from app.db.sync.docs import SETTING_DOCS
    from app import overhead
    overhead.choose("rotation")
    rotation._set(0)
    qapp.processEvents()
    keys_before = program.keys()
    first = page_of(a)
    wording.set_texts({"slide/welcome/title": "Hello from the pit",
                       "slide/ask/body": "Ask the crew anything at all."})
    qapp.processEvents()
    check("an edited slide headline shows on A at once, still paired",
          page_of(a) == "slide:Hello from the pit" and a._slides.count == b._slides.count,
          f"{first} → {page_of(a)}")
    check("B's edited sentence is on its slide",
          b._slides.current_slide is not None
          and b._slides.current_slide.body == "Ask the crew anything at all.")
    check("the program's stops don't move for a wording edit",
          program.keys() == keys_before)
    from app.widgets.interactive_board import InteractiveBoard
    board = InteractiveBoard()
    board.resize(1080, 1920)
    board.grab()
    texts = lambda: {l.text() for l, _k in board._worded}
    before = id(board.layout())
    check("the board shows shipped text by default", {"Act 472", "What we run", "Sponsors"} <= texts())
    wording.set_texts({"board/act/title": "Act 472 (2025)",
                       "board/card/lego_club/blurb": "Ten weeks of LEGO robots for K–6."})
    qapp.processEvents()
    check("board edits land in place (labels updated, nothing rebuilt)",
          {"Act 472 (2025)", "Ten weeks of LEGO robots for K–6."} <= texts()
          and id(board.layout()) == before)
    wording.set_texts({"board/act/title": "", "slide/welcome/title": "Welcome to Breakaway"})
    qapp.processEvents()
    check("emptied or set back to shipped, a field falls back (and isn't stored)",
          "Act 472" in texts() and not wording.is_edited("board/act/title")
          and not wording.is_edited("slide/welcome/title")
          and page_of(a) == "slide:Welcome to Breakaway")
    check("too long is capped at the field's limit",
          len(wording.text("slide/ask/body")) <= 160)
    check("wording is a team setting (syncs to every pit)",
          SETTING_DOCS.get("wording") == ("app.wording", ("texts",))
          and "slide/ask/body" in wording.load()["texts"])
    from app.admin import init_admin, admin
    init_admin()
    from app.widgets.wording_panel import WordingPanel
    panel = WordingPanel()
    groups = [panel._groups.itemText(i) for i in range(panel._groups.count())]
    check("the editor lists the front panel and both screens' slides",
          any(g.startswith("Front panel") for g in groups)
          and "Overhead slides · Screen A" in groups and "Overhead slides · Screen B" in groups,
          f"{len(groups)} groups, {len(wording.fields())} fields")
    check("hidden while the admin lock is closed",
          not panel._body.isVisibleTo(panel) and panel._locked.isVisibleTo(panel))
    panel._groups.setCurrentText("Overhead slides · Screen A")
    row = next(r for r in panel._field_rows if r.field.key == "slide/robot/title")
    row.editor.setText("Meet the robot")
    panel._on_save()
    qapp.processEvents()
    check("saving from the editor reaches the screens",
          wording.text("slide/robot/title") == "Meet the robot"
          and program._authored()["robot"].title == "Meet the robot",
          panel._status.text())
    wording.set_texts({k: "" for k in wording.load()["texts"]})

    print("\nHome Datasets panel (admin)")
    from app.widgets.dataset_panel import DatasetPanel
    from app.widgets.toggle_switch import ToggleSwitch
    from PyQt6.QtWidgets import QLabel
    pa, pb = DatasetPanel(), DatasetPanel()
    rows = len(datasets.all_datasets(enabled_only=False))
    for _ in range(3):
        pa._shown = None                # force real rebuilds
        pa.rebuild()
    qapp.processEvents()
    seen = lambda p, cls: [w for w in p._body.findChildren(cls) if w.isVisibleTo(p._body)]
    check("rebuilding leaves one row per dataset, no ghosts piled top-left",
          len(seen(pa, QLabel)) == rows and len(seen(pa, ToggleSwitch)) == rows,
          f"{len(seen(pa, QLabel))} labels, {len(seen(pa, ToggleSwitch))} switches for {rows}")
    sw = seen(pa, ToggleSwitch)[0]
    key, was = sw.property("dataset"), sw.isChecked()
    sw.setChecked(not was)
    qapp.processEvents()
    check("a switch on one screen's panel shows on the other's; its own stays put",
          any(w.property("dataset") == key and w.isChecked() == (not was)
              for w in seen(pb, ToggleSwitch))
          and sw.isVisibleTo(pa._body) and sw.isChecked() == (not was))
    dataset_settings.set_on(str(key), was)
    pa.deleteLater()
    pb.deleteLater()
    panel.deleteLater()
    board.deleteLater()
    qapp.processEvents()

    print("\nWhen something goes wrong, the logs say so (the windowed build)")
    import time as _time
    from app import crash_log, paths, qt_log
    from PyQt6.QtCore import qWarning
    crash_log.install()
    qt_log.install()
    real_err, sys.stderr = sys.stderr, None          # the windowed exe has no stderr
    try:
        qWarning(b"probe: a warning nobody listed in advance")
    finally:
        sys.stderr = real_err
    log = paths.data("qt_warnings.log").read_text(encoding="utf-8", errors="replace")
    check("with no console, a Qt warning is still written down (it used to raise instead)",
          "probe: a warning nobody listed in advance" in log)
    crash_log.start_watchdog(stall_s=3.0)
    _time.sleep(6.5)                                 # the GUI thread, frozen
    qapp.processEvents()
    _time.sleep(2.5)
    qapp.processEvents()
    _time.sleep(2.5)
    text = crash_log.path().read_text(encoding="utf-8", errors="replace")
    check("a frozen GUI thread is recorded with every thread's stack, then its recovery",
          "FROZEN" in text and "program_check.py" in text and "responsive again" in text)
    from app.widgets.robot_panel import _DuplicatesDialog
    from PyQt6.QtCore import Qt as _Qt
    check("the 'Already imported' pop-up stays in front of full-screen windows",
          bool(_DuplicatesDialog([], unlocked=False).windowFlags()
               & _Qt.WindowType.WindowStaysOnTopHint))

    print("\nData goes away")
    nexus._status = None
    rotation.refresh()
    qapp.processEvents()
    check("no event: the event stop leaves both screens, still paired",
          "event" not in program.keys() and a._slides.count == b._slides.count
          == len(program.keys()), f"A {a._slides.count}, B {b._slides.count}")

    for w in (a, b):
        w.close()
        w.deleteLater()
    qapp.processEvents()
    print(f"\n{'All program checks passed.' if not _failures else f'{len(_failures)} failed.'}")
    # Exit hard here, while the QApplication is still alive, as --self-check
    # does: letting it be destroyed tears Qt down under the simulated LED
    # link's thread and aborts (134), turning a pass into a crash.
    sys.stdout.flush()
    os._exit(0 if not _failures else 1)


if __name__ == "__main__":
    sys.exit(main())
