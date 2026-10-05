"""
Pull the team's robot logs from the sync hub into a scratch install, read-only.
Exit 0/1.

    uv run tools/pull_logs.py                     # into .validation/team_pull
    uv run tools/anomaly_report.py --data .validation/team_pull

For studying the team's real logs on a development machine whose own database
stays isolated from team sync (its sync.json is off by decision). This runs the
app's own sync engine against the team hub, **pull only**: the catch-up, pull,
retry and bundle-fetch steps, never scan, push, manifest or blob requests, so
nothing is ever sent to the hub but reads. The scratch tree gets a fixed,
honest machine identity ("validation-readonly"). The token is the app's own
`secrets/sync_token`, read here and handed to the client, never printed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

MACHINE_ID = "validation-readonly"
MACHINE_NAME = "Validation Mac (read-only)"


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=Path, default=ROOT / ".validation" / "team_pull")
    ap.add_argument("--fresh", action="store_true", help="start the scratch tree over")
    opts = ap.parse_args()

    # The app's own token, read straight from this checkout's file. Nothing
    # from app.db may be imported before PIT_DISPLAY_DATA points at the scratch
    # tree: app.db.database fixes its path at import (2026-10-05, a first
    # version of this tool imported sync settings first and pulled into the
    # machine's real database).
    token_file = ROOT / "secrets" / "sync_token"
    token = os.environ.get("PIT_SECRET_SYNC_TOKEN") or (
        token_file.read_text(encoding="utf-8").strip() if token_file.is_file() else "")
    if not token:
        print("No secrets/sync_token on this machine: nothing to pull with.")
        return 1
    url = "https://sync.bh-stack.com"
    if "app.db.database" in sys.modules:
        print("Refusing: the database module loaded before the scratch tree was set.")
        return 1

    if opts.fresh:
        shutil.rmtree(opts.to, ignore_errors=True)
    (opts.to / "data").mkdir(parents=True, exist_ok=True)
    os.environ["PIT_DISPLAY_DATA"] = str(opts.to.resolve())
    os.environ["PIT_LOG_SCRATCH"] = str((opts.to / "scratch").resolve())

    import app.db.migrations  # noqa: F401
    from app.db import init_db
    from app.db.sync.client import HubClient
    from app.db.sync.engine import Engine
    db = init_db()
    scratch = opts.to.resolve()
    if scratch not in Path(db.path).resolve().parents:
        print(f"Refusing: the database is {db.path}, not inside {scratch}.")
        return 1
    with db.transaction() as c:          # nothing local ever goes up
        c.execute("DELETE FROM sync_outbox")
    client = HubClient(url, token, MACHINE_ID, MACHINE_NAME, "pull-only")
    engine = Engine(db.path, client, MACHINE_ID,
                    {"pull_logs": True, "sync_files": False, "sync_music": False,
                     "upload_raw": False})
    # Pull only: the steps that read, never the ones that write to the hub.
    def nothing(conn, rep):
        return None
    for name in ("_scan", "_push", "_manifest", "_request_missing", "_status"):
        setattr(engine, name, nothing)

    print(f"Pulling from {url} into {opts.to} as {MACHINE_NAME!r}, read-only…", flush=True)
    t0 = time.monotonic()
    for cycle in range(1, 200):
        rep = engine.cycle()
        sessions = db.fetchone("SELECT COUNT(*) n FROM log_session")["n"]
        print(f"  cycle {cycle}: {sessions} log sessions here, {rep.waiting} bundles still to "
              f"fetch" + (f", errors: {rep.errors}" if rep.errors else ""), flush=True)
        if rep.errors and not sessions:
            return 1
        if not rep.waiting:
            break
    with db.transaction() as c:
        c.execute("DELETE FROM sync_outbox")
    n = db.fetchone("SELECT COUNT(*) n FROM log_session")["n"]
    print(f"Done in {time.monotonic() - t0:.0f}s: {n} log sessions in {opts.to}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
