"""
Validate everything on this Mac, never against production. Exit 0/1.

    uv run tools/validate.py              every automated check, each isolated
    uv run tools/validate.py --model      …plus a real analysis on the built-in engine
    uv run tools/validate.py --hardware   …plus the LED controller (plugged in)
    uv run tools/validate.py --only sync_check,ai_check
    uv run tools/validate.py --app        the full app on a fresh test install

**The checks** each build their own data: a scratch data tree from a fresh
install's database (the migrations, exactly as CI's seed), the test logs and
the fixture in `TEST_LOGS/`, and throwaway Workers (`devhub.py`) for the sync
hub and the Nexus relay. None reads this Mac's own `data/`, its `secrets/`, or
anything on the network beyond `npm` packages. Each check's whole output goes
to `.validation/logs/<check>.log`; the summary names the failing line.

**`--app`** is the deploy machine's first boot, made whole: a fresh data tree
in `.validation/pit` (rebuilt every run), the test logs imported, team sync
pointed at a private hub on this Mac, a simulated home that has pushed a TBA
schedule, Quality Award counts, its datasets (off until an admin turns
each on) and a verdict, and a simulated second pit that
has shared a judges slide and a checklist. Then the real app opens on it.
LEDs, Nexus and the battery cart are simulated unless `--leds` /
`--live-nexus`; the analysis uses the real model in `models/`. Close the app
to stop the hub. Nothing reaches the team's hub, the VM or the home server.

What this Mac can't validate: the Windows-only paths (the self-updater's
junctions, the installer, Vulkan on the pit's GPU). CI's packaged self-check
and a test Windows machine cover those.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

import devhub  # noqa: E402

VALIDATION = ROOT / ".validation"
LOGS = VALIDATION / "logs"
FIXTURE = ROOT / "TEST_LOGS" / "fixture_faults_2026-07-29.pitlog.zst"
TEST_LOGS = ("akit_26-08-17_02-59-21.wpilog", "rio_2026-08-17_03-42-54.hoot")

# (name, argv after `python`, needs the local relay, flag that enables it)
CHECKS: list[tuple[str, list[str], bool, str]] = [
    ("self_check", ["main.py", "--self-check"], False, ""),
    ("upgrade_check", ["tools/upgrade_check.py"], False, ""),
    ("eq_check", ["tools/eq_check.py"], False, ""),
    ("battery_check", ["tools/battery_check.py"], False, ""),
    ("webcast_check", ["tools/webcast_check.py"], False, ""),
    ("program_check", ["tools/program_check.py"], False, ""),
    ("ai_check", ["tools/ai_check.py"], False, ""),
    ("import_check", ["tools/import_check.py"], False, ""),
    ("sync_check", ["tools/sync_check.py", "--local"], False, ""),
    ("relay_check", ["tools/relay_check.py", "--local"], True, ""),
    ("check_installer", ["tools/check_installer.py"], False, ""),
    ("ai_model", ["tools/ai_check.py", "--engine", "llama"], False, "model"),
    ("led_diag", ["tools/led_diag.py"], False, "hardware"),
]


def _env(scratch: Path | None) -> dict[str, str]:
    """A check's environment: offscreen, simulated hardware, quiet services,
    never this Mac's data or credentials."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PIT_SECRET_", "PIT_DISPLAY_DATA", "SYNC_HOME_TOKEN"))}
    env.update(QT_QPA_PLATFORM="offscreen", PIT_LEDS_FAKE="1", PYTHONUNBUFFERED="1")
    if scratch is not None:
        env["PIT_DISPLAY_DATA"] = str(scratch)
    return env


def run_checks(opts) -> int:
    use_utf8()
    LOGS.mkdir(parents=True, exist_ok=True)
    wanted = set(opts.only.split(",")) if opts.only else None
    plan = []
    for name, argv, needs_relay, flag in CHECKS:
        if wanted is not None and name not in wanted:
            continue
        if flag and not getattr(opts, flag) and (wanted is None or name not in wanted):
            continue
        if name == "led_diag":
            argv = argv[:]            # the real controller: never simulated
        plan.append((name, argv, needs_relay))

    relay = None
    results = []
    tmp = Path(tempfile.mkdtemp(prefix="validate-"))
    try:
        for name, argv, needs_relay in plan:
            if needs_relay and relay is None:
                if not devhub.port_free(devhub.RELAY_PORT):
                    results.append((name, None, 0.0, f":{devhub.RELAY_PORT} is in use; "
                                    "stop whatever holds it (an old wrangler dev?)"))
                    continue
                print("  starting a local Nexus relay (wrangler dev)…", flush=True)
                relay, _ = devhub.start_relay(tmp)
            # self_check gets a fresh install's data tree, not the checkout's.
            scratch = tmp / "self-check" if name == "self_check" else None
            env = _env(scratch)
            if name == "led_diag":
                env.pop("PIT_LEDS_FAKE", None)
            t0 = time.monotonic()
            live = sys.stdout.isatty()
            if live:
                print(f"  {name:<16} …", end="", flush=True)
            log = LOGS / f"{name}.log"
            with open(log, "w", encoding="utf-8") as out:
                code = subprocess.run([sys.executable, *argv], cwd=ROOT, env=env,
                                      stdout=out, stderr=subprocess.STDOUT).returncode
            dt = time.monotonic() - t0
            text = log.read_text(encoding="utf-8", errors="replace").splitlines()
            fail = next((ln.strip() for ln in text
                         if ln.lstrip().startswith(("✗", "[FAIL]", "FAIL", "Traceback"))), "")
            tail = fail or (text[-1].strip() if text else "")
            results.append((name, code == 0, dt, tail))
            print(f"{chr(13) if live else ''}  {name:<16} {'PASS' if code == 0 else 'FAIL'}  "
                  f"{dt:5.0f}s  {tail[:90]}", flush=True)
    finally:
        devhub.stop(relay)
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if r[1] is not True]
    print()
    for name, ok, _dt, tail in failed:
        state = "SKIPPED" if ok is None else "FAILED"
        print(f"{state}: {name} — {tail}   (log: .validation/logs/{name}.log)")
    print(f"{len(results) - len(failed)}/{len(results)} passed. "
          f"Logs in .validation/logs/.")
    return 0 if not failed else 1


# ── the full app on a fresh test install ────────────────────────────────

def _seed_peer_and_home(url: str, pit_dir: Path) -> None:
    """What a deploy machine finds on its first sync: a home that has pushed
    TBA data and another pit that has shared a slide and a checklist."""
    from app import paths
    from app.db.database import _Database
    from app.db.sync import docs
    from app.db.sync.client import HubClient
    from app.db.sync.engine import Engine

    home = HubClient(url, devhub.HOME_TOKEN, "home", "home", "validate")
    fixture_start = int(datetime(2026, 7, 29, 14, 30).timestamp() * 1000)
    home.push([
        {"tbl": "tba_event", "uid": "2026valid", "op": "upsert", "base": 0,
         "data": {"name": "Validation Regional", "short_name": "Validation",
                  "start_date": "2026-07-29", "end_date": "2026-07-31",
                  "timezone": "America/Chicago"}},
        # Played two minutes after the fixture log starts: match_context finds it.
        {"tbl": "tba_match", "uid": "2026valid_qm14", "op": "upsert", "base": 0,
         "data": {"event_key": "2026valid", "comp_level": "qm", "set_number": 1,
                  "match_number": 14, "red_teams": ["3937", "16", "118"],
                  "blue_teams": ["254", "1678", "971"], "red_score": 120,
                  "blue_score": 98, "winning_alliance": "red",
                  "scheduled_time": fixture_start, "actual_time": fixture_start}},
    ], force=True)
    from sync_check import HOME_DATASETS, HOME_FEEDS
    home.push(HOME_FEEDS, force=True)
    # Home's datasets (R10/R11), all off until an admin turns each on in
    # Control → Presentation A/B → Home Datasets.
    home.push(HOME_DATASETS, force=True)

    peer_dir = VALIDATION / "peer"
    old = os.environ.get("PIT_DISPLAY_DATA")
    os.environ["PIT_DISPLAY_DATA"] = str(peer_dir)
    try:
        (peer_dir / "data").mkdir(parents=True, exist_ok=True)
        import app.db.migrations  # noqa: F401
        peer = _Database(paths.data("data", "pit_display.db"))
        with peer.transaction() as conn:
            conn.execute("INSERT INTO checklist (name, position) VALUES ('Validation run', 9)")
            cid = conn.execute("SELECT id FROM checklist WHERE name = 'Validation run'").fetchone()[0]
            for i, text in enumerate(("Sync shows In step", "The slide from the peer arrived",
                                      "Analysis panel runs on the fixture")):
                conn.execute("INSERT INTO checklist_item (checklist_id, text, position) "
                             "VALUES (?, ?, ?)", (cid, text, i))
        slide = docs.root("judges_slides") / "Validation slide.png"
        shutil.copy2(ROOT / "assets" / "logos" / "2026 Wordmark.png", slide)
        os.utime(slide, (time.time() - 60, time.time() - 60))     # settled
        client = HubClient(url, devhub.PIT_TOKEN, "validation-peer", "Validation peer", "validate")
        engine = Engine(peer.path, client, "validation-peer",
                        {"pull_logs": False, "sync_files": True, "sync_music": False})
        for _ in range(2):
            engine.force_files = True
            rep = engine.cycle()
            if rep.errors:
                print("  peer:", rep.errors)
    finally:
        if old is None:
            os.environ.pop("PIT_DISPLAY_DATA", None)
        else:
            os.environ["PIT_DISPLAY_DATA"] = old


def _home_datasets() -> list:
    from sync_check import HOME_DATASETS
    return HOME_DATASETS


def _first_sync(db, url: str, prefs: dict) -> int:
    """The validation pit's first cycle, as the app's service runs it, and a
    report of what arrived: the environment proving itself before anyone
    spends time in the app."""
    from app import credentials
    from app.db.sync import docs, settings as sync_settings
    from app.db.sync.client import HubClient
    from app.db.sync.engine import Engine
    client = HubClient(url, credentials.read(sync_settings.TOKEN_NAME), prefs["machine_id"],
                       prefs["machine_name"], "validate")
    engine = Engine(db.path, client, prefs["machine_id"], prefs)
    engine.force_files = True
    rep = engine.cycle()
    if not rep.ok:
        print("  first sync errors:", rep.errors)
    one = lambda q: db.fetchone(q)[0]  # noqa: E731
    got = {
        "hub is this Mac's private one": url.startswith("http://127.0.0.1:"),
        "the peer's checklist": one("SELECT COUNT(*) FROM checklist WHERE name = 'Validation run'") == 1,
        "the peer's judges slide": (docs.root("judges_slides") / "Validation slide.png").is_file(),
        "home's TBA event and match": one("SELECT COUNT(*) FROM tba_match") == 1,
        "home's award feeds (teams, rivals, facts)": one("SELECT COUNT(*) FROM tba_team") == 2
            and one("SELECT COUNT(*) FROM tba_rival") == 1 and one("SELECT COUNT(*) FROM tba_fact") == 1,
        "home's datasets (off until an admin turns them on)":
            one("SELECT COUNT(*) FROM home_dataset") == len(_home_datasets()),
        "home's verdict for this machine": one(
            f"SELECT COUNT(*) FROM sync_verdict WHERE uid = '{prefs['machine_id']}'") == 1,
        "three logs (fixture + test logs)": one("SELECT COUNT(*) FROM log_session") == 3,
    }
    for what, ok in got.items():
        print(f"  {'✓' if ok else '✗'} {what}")
    bad = [w for w, ok in got.items() if not ok]
    print("Test install ready." if not bad else f"{len(bad)} missing.")
    return 0 if not bad and rep.ok else 1


def run_app(opts) -> int:
    use_utf8()
    shutil.rmtree(VALIDATION / "pit", ignore_errors=True)
    shutil.rmtree(VALIDATION / "peer", ignore_errors=True)
    shutil.rmtree(VALIDATION / "hub", ignore_errors=True)
    pit = VALIDATION / "pit"
    (pit / "data").mkdir(parents=True)
    os.environ["PIT_DISPLAY_DATA"] = str(pit)

    print("Starting a private sync hub (wrangler dev)…", flush=True)
    hub, url = devhub.start_hub(VALIDATION / "hub")
    try:
        import app.db.migrations  # noqa: F401
        from app import credentials
        from app.db import init_db
        from app.db.sync import bundle
        from app.db.sync import settings as sync_settings
        from app.robot.ingest import import_log
        db = init_db()
        credentials.write(sync_settings.TOKEN_NAME, devhub.PIT_TOKEN)
        prefs = sync_settings.save(url=url, enabled=True, machine_name="Validation Mac")
        print(f"Fresh install at {pit} (schema from the migrations, as CI's seed)")

        print("Importing the test logs…", flush=True)
        scratch = pit / "import-scratch"
        scratch.mkdir()
        bundle.import_bundle(db.path, FIXTURE, scratch)
        for name in TEST_LOGS:
            res = import_log(ROOT / "TEST_LOGS" / name, db.path)
            print(f"  {name}: {res.stored_rows:,} rows")
        shutil.rmtree(scratch, ignore_errors=True)

        print("A simulated home and second pit fill the hub…", flush=True)
        _seed_peer_and_home(url, pit)
        from app.db.sync.client import HubClient
        HubClient(url, devhub.HOME_TOKEN, "home", "home", "validate").push([
            {"tbl": "sync_verdict", "uid": prefs["machine_id"], "op": "upsert", "base": 0,
             "data": {"checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "manifest_at": "", "in_sync": True, "missing": [], "different": [],
                      "extra": [], "playlists_differ": []}}], force=True)

        env = {k: v for k, v in os.environ.items() if not k.startswith("PIT_SECRET_")}
        env.update(PIT_DISPLAY_DATA=str(pit), PIT_BATTERIES_FAKE="1",
                   PIT_AI_MODELS=str(ROOT / "models"))
        if not opts.leds:
            env["PIT_LEDS_FAKE"] = "1"
        if not opts.live_nexus:
            env["PIT_NEXUS_FAKE"] = "1"
        if opts.setup_only:
            return _first_sync(db, url, prefs)
        print(f"\nOpening the app as \"Validation Mac\" ({prefs['machine_id']}), "
              f"syncing with {url}. Close it to stop the hub.\n", flush=True)
        return subprocess.run([sys.executable, "main.py"], cwd=ROOT, env=env).returncode
    finally:
        devhub.stop(hub)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--app", action="store_true", help="open the full app on a fresh test install")
    ap.add_argument("--only", help="comma-separated check names")
    ap.add_argument("--model", action="store_true", help="also a real analysis (built-in engine)")
    ap.add_argument("--hardware", action="store_true", help="also the real LED controller")
    ap.add_argument("--leds", action="store_true", help="--app: drive the real LED controller")
    ap.add_argument("--setup-only", action="store_true",
                    help="--app: build the test install and hub contents, don't open the app")
    ap.add_argument("--live-nexus", action="store_true",
                    help="--app: the real Nexus relay (read-only) instead of the examples")
    opts = ap.parse_args()
    return run_app(opts) if opts.app else run_checks(opts)


if __name__ == "__main__":
    sys.exit(main())
