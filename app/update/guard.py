"""
Roll back, by itself, a build that can't stay up.

An update self-checks before it's activated (`install.verify`), but a build
can pass that and still die on this machine: on its data, its screens, its
hardware. Nobody may be at the pit to roll it back by hand, so:

    a launch of a new build ends uncleanly (killed, a crash, a boot step that
    throws, a freeze closed by hand) within SHORT_LIFE_S of starting
        → one strike against that build (`pointer.json` `trial`)
    FAILS_TO_ROLL_BACK strikes in a row
        → `install.rollback()` to the previous version, the report pushed
          home now (`Engine.farewell`; the older build may not report), the
          previous version started, this process gone

A build that stays up PROVEN_AFTER_S is proven and never rolled back
automatically: a crash days later is a bug to report, not a bad update. A
clean exit clears the strikes. A version rolled back is `bad` and never a
rollback target, and the updater doesn't offer it again (a newer release
clears it), so two broken builds can't swap back and forth.

Runs first thing in `main()`, right after `spool.begin_session()` (which is
what knows the last launch never ended), before Qt starts. Only a managed
install running its `current` version is touched; a checkout, an unzipped
copy or a version started by hand from its folder is left alone. Never raises.
"""

from __future__ import annotations

from datetime import datetime, timezone

FAILS_TO_ROLL_BACK = 2
SHORT_LIFE_S = 10 * 60
PROVEN_AFTER_S = 10 * 60


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lifetime(session: dict) -> float:
    """How long a session record says the launch stayed up."""
    try:
        started = float(session.get("started_ts") or 0)
        last = float(session.get("beat_ts") or started)
        return max(0.0, last - started)
    except (TypeError, ValueError):
        return 0.0


def _where():
    """(install root, pointer, running version) when this is the managed
    install's current build, else None."""
    from app.update import install
    root = install.install_root()
    if root is None:
        return None
    running = install.running_version()
    pointer = install.read_pointer(root)
    if not running or str(pointer.get("current", "")) != running:
        return None
    return root, pointer, running


def decide(pointer: dict, running: str, previous: dict | None,
           versions_on_disk: set[str]) -> tuple[str, dict]:
    """The rule, without touching anything: ("stay" | "rollback" | "stuck",
    pointer changes). `previous` is the last launch's record if it never ended."""
    if running in set(pointer.get("proven_versions", [])):
        return "stay", {}
    trial = dict(pointer.get("trial") or {})
    if trial.get("version") != running:
        trial = {"version": running, "failures": 0, "since": _now(), "strikes": []}
    if previous and str(previous.get("version", "")) == running \
            and lifetime(previous) < SHORT_LIFE_S:
        trial["failures"] = int(trial.get("failures", 0)) + 1
        trial["strikes"] = (list(trial.get("strikes", [])) + [{
            "started": previous.get("started"), "lived_s": round(lifetime(previous))}])[-5:]
    if trial["failures"] < FAILS_TO_ROLL_BACK:
        return "stay", {"trial": trial}
    target = str(pointer.get("previous", ""))
    bad = set(pointer.get("bad", []))
    if not target or target not in versions_on_disk or target in bad or target == running:
        # Nowhere safe to go: keep running this one and say so, once.
        if trial.get("stuck"):
            return "stay", {"trial": trial}
        trial["stuck"] = True
        return "stuck", {"trial": trial}
    return "rollback", {"trial": None, "bad": sorted(bad | {running}),
                        "rolled_back": {"from": running, "to": target, "at": _now(),
                                        "strikes": trial.get("strikes", [])}}


def after_launch(previous: dict | None) -> bool:
    """Call once, right after `spool.begin_session()`. True means this build
    was rolled back and the previous one started: the caller exits now."""
    try:
        where = _where()
        if where is None:
            return False
        root, pointer, running = where
        from app.update import install
        verdict, changes = decide(pointer, running, previous, set(install.installed_versions()))
        if verdict == "rollback":
            target = install.rollback()
            install.write_pointer(root, **changes)
            _report_rollback(running, target, changes["rolled_back"]["strikes"])
            _send_home()
            from app.diagnostics import spool
            spool.end_session()         # this launch ends here, on purpose
            return install.relaunch()
        install.write_pointer(root, **changes)
        if verdict == "stuck":
            _report_stuck(running, pointer, changes["trial"])
        return False
    except Exception:
        return False


def note_uptime(uptime_s: float) -> None:
    """The diagnostics service's minute tick: up long enough is proven."""
    if uptime_s < PROVEN_AFTER_S:
        return
    try:
        where = _where()
        if where is None:
            return
        root, pointer, running = where
        proven = list(pointer.get("proven_versions", []))
        if running in proven:
            return
        from app.update import install
        install.write_pointer(root, proven_versions=(proven + [running])[-5:], trial=None)
    except Exception:
        pass


def clean_exit() -> None:
    """A clean close: whatever happened before, this build can start."""
    try:
        where = _where()
        if where is None:
            return
        root, pointer, _running = where
        if pointer.get("trial"):
            from app.update import install
            install.write_pointer(root, trial=None)
    except Exception:
        pass


def bad_versions() -> set[str]:
    """Versions this machine rolled back: the updater doesn't offer them."""
    try:
        from app.update import install
        return set(install.read_pointer().get("bad", []))
    except Exception:
        return set()


def last_rollback() -> dict:
    """The last automatic rollback, if this launch is running its target."""
    try:
        from app.update import install
        pointer = install.read_pointer()
        rb = pointer.get("rolled_back") or {}
        return rb if rb and rb.get("to") == install.running_version() else {}
    except Exception:
        return {}


def _report_rollback(bad: str, target: str, strikes: list) -> None:
    from app.diagnostics import spool
    lives = ", ".join(f"{s.get('lived_s', '?')} s" for s in strikes)
    spool.record(
        "rollback",
        f"{bad} failed to start {len(strikes)} times in a row and was rolled back to "
        f"{target} automatically",
        severity="fatal", signature=f"rollback {bad}",
        detail=(f"Each launch of {bad} ended uncleanly within "
                f"{SHORT_LIFE_S // 60} minutes (stayed up {lives}). The unclean_exit "
                f"events before this one carry crash.log. {target} starts now; the "
                f"updater won't offer {bad} again on this machine."),
        data={"from": bad, "to": target, "strikes": strikes})


def _report_stuck(running: str, pointer: dict, trial: dict) -> None:
    from app.diagnostics import spool
    spool.record(
        "rollback_stuck",
        f"{running} keeps failing to start, and there's no safe version to roll back to",
        severity="fatal", signature=f"rollback_stuck {running}",
        detail=(f"Previous version: {pointer.get('previous') or 'none'}; already rolled "
                f"back from: {', '.join(pointer.get('bad', [])) or 'none'}. Both failing "
                "points at this machine rather than a build: its data, display or disk."),
        data={"trial": trial, "pointer": {k: pointer.get(k) for k in
                                          ("current", "previous", "bad")}})


def _send_home(timeout: float = 4.0) -> None:
    """Push the reports now: the version starting next may predate diagnostics."""
    try:
        if not _reporting():
            return
        from app import credentials, version
        from app.db.database import default_path
        from app.db.sync import settings as sync_settings
        from app.db.sync.client import HubClient
        from app.db.sync.engine import Engine
        prefs = sync_settings.load()
        token = credentials.read(sync_settings.TOKEN_NAME)
        if not token or not prefs.get("enabled"):
            return
        client = HubClient(prefs["url"], token, prefs["machine_id"],
                           prefs["machine_name"], version.VERSION)
        Engine(default_path(), client, prefs["machine_id"], prefs).farewell(timeout)
    except Exception:
        pass


def _reporting() -> bool:
    import os
    from app.diagnostics import settings as diag_settings
    return diag_settings.enabled() and os.environ.get("PIT_DIAG_QUIET") != "1"

