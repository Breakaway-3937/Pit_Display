"""
Read the pits' diagnostics straight off the hub: for a development session
that wants the reports now, without waiting on home.

    uv run tools/diag_pull.py                       machines, then the last day's events
    uv run tools/diag_pull.py --since 72 --kind crash,fatal,unclean_exit
    uv run tools/diag_pull.py --machine Central_Home
    uv run tools/diag_pull.py --event <uid>          one event in full, attachments unpacked
    uv run tools/diag_pull.py --event <uid> --save out/   …and its attachments as files
    uv run tools/diag_pull.py --json                 everything matched, as JSON

Read-only. Uses this Mac's own sync token (`secrets/sync_token`, or
`PIT_SECRET_SYNC_TOKEN`) as any pit's pull does, and presents itself as
`diag-reader` so the hub's machine list doesn't mistake it for a running
pit. The hub keeps every event (each is its own row) and only the newest
status per machine; home keeps every version (`home/REQUESTS.md` R16).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

use_utf8()

from app import credentials  # noqa: E402
from app.db.sync import settings as sync_settings  # noqa: E402
from app.db.sync.client import HubClient, SyncError  # noqa: E402
from app.diagnostics.spool import unpack  # noqa: E402


def fetch(client: HubClient) -> tuple[list[dict], dict[str, dict]]:
    events: list[dict] = []
    status: dict[str, dict] = {}
    since = 0
    while True:
        page = client.changes(since, 1000)
        for ch in page.get("changes", []):
            if ch["tbl"] == "pit_event" and ch["op"] == "upsert":
                events.append({**(ch.get("data") or {}), "uid": ch["uid"], "hub_at": ch.get("at")})
            elif ch["tbl"] == "pit_status" and ch["op"] == "upsert":
                status[ch["uid"]] = {**(ch.get("data") or {}), "hub_at": ch.get("at")}
        changes = page.get("changes", [])
        if not changes or not page.get("more"):
            break
        since = int(changes[-1]["seq"])
    events.sort(key=lambda e: e.get("at", ""))
    return events, status


def _when(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _hub_age(ms) -> str:
    try:
        mins = (datetime.now().timestamp() * 1000 - float(ms)) / 60000
    except (TypeError, ValueError):
        return "?"
    return f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h"


def show_machines(status: dict[str, dict]) -> None:
    print("Machines (latest status)")
    if not status:
        print("  none have reported yet")
    for mid, s in sorted(status.items()):
        b = s.get("build", {})
        ch = s.get("checks", {})
        counts = s.get("counts", {})
        bad = [f"{r['name']}:{r['status']}" for r in ch.get("results", [])
               if r.get("status") in ("fail", "warn")]
        online = "closed" if s.get("final") else f"seen {_hub_age(s.get('hub_at'))} ago"
        print(f"  {s.get('machine_name', mid)} ({mid})  {b.get('version')} [{b.get('channel')}]  "
              f"{online}, up {s.get('session', {}).get('uptime_s', 0) / 3600:.1f} h, "
              f"{s.get('process', {}).get('rss_mb')} MB, crashes {counts.get('crashes')}, "
              f"freezes {counts.get('freezes')}, worst stall {counts.get('max_lag_s')} s")
        if bad:
            print(f"      checks: {', '.join(bad)}")
    print()


def show_events(events: list[dict]) -> None:
    print(f"Events ({len(events)})")
    for e in events:
        print(f"  {e.get('at', '')[:19]}  {e.get('severity', ''):5}  {e.get('kind', ''):14} "
              f"{e.get('machine_name', '')[:16]:16}  {e.get('summary', '')[:110]}")
        print(f"      {e['uid']}")
    sigs = Counter(e.get("signature") for e in events if e.get("severity") in ("fatal", "error"))
    if sigs:
        print("\nFault signatures")
        for sig, n in sigs.most_common(15):
            print(f"  {n:3}×  {sig}")


def show_event(e: dict, save: Path | None) -> None:
    print(f"{e['uid']}\n{e.get('severity')} {e.get('kind')}  at {e.get('at')}  "
          f"on {e.get('machine_name')} ({e.get('machine_id')})  build {e.get('build')}")
    print(f"signature: {e.get('signature')}\nsummary:   {e.get('summary')}\n")
    if e.get("data"):
        print("data: " + json.dumps(e["data"], indent=1)[:4000] + "\n")
    detail = unpack(e.get("detail"))
    if detail:
        print("── detail ──\n" + detail + "\n")
    for name, value in (e.get("attachments") or {}).items():
        text = unpack(value)
        if save is not None:
            save.mkdir(parents=True, exist_ok=True)
            (save / name).write_text(text, encoding="utf-8")
            print(f"── {name}: {len(text):,} characters → {save / name}")
        else:
            print(f"── {name} ({len(text):,} characters, last 60 lines) ──")
            print("\n".join(text.splitlines()[-60:]) + "\n")
    if e.get("dropped_attachments"):
        print(f"(left out to fit the hub's row: {', '.join(e['dropped_attachments'])})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", type=float, default=24, help="hours back (default 24)")
    ap.add_argument("--kind", help="comma-separated kinds")
    ap.add_argument("--machine", help="machine id or name (substring)")
    ap.add_argument("--event", help="one event's uid, in full")
    ap.add_argument("--save", type=Path, help="with --event: write attachments here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--url", default=None)
    args = ap.parse_args()

    token = credentials.read(sync_settings.TOKEN_NAME)
    if not token:
        print("No sync token on this machine (secrets/sync_token or PIT_SECRET_SYNC_TOKEN).")
        return 1
    url = args.url or sync_settings.load()["url"]
    client = HubClient(url, token, "diag-reader", "Diagnostics reader (read-only)", "diag_pull")
    try:
        events, status = fetch(client)
    except SyncError as e:
        print(f"The hub didn't answer: {e}")
        return 1

    if args.event:
        match = [e for e in events if e["uid"] == args.event or e["uid"].endswith(args.event)]
        if not match:
            print("No such event at the hub.")
            return 1
        show_event(match[-1], args.save)
        return 0

    cutoff = datetime.now().astimezone() - timedelta(hours=args.since)
    kinds = set(args.kind.split(",")) if args.kind else None
    who = (args.machine or "").lower()
    picked = [e for e in events
              if (_when(e.get("at")) or cutoff) >= cutoff
              and (kinds is None or e.get("kind") in kinds)
              and (not who or who in e.get("machine_id", "").lower()
                   or who in e.get("machine_name", "").lower())]
    machines = {k: v for k, v in status.items()
                if not who or who in k.lower() or who in v.get("machine_name", "").lower()}
    if args.json:
        print(json.dumps({"machines": machines, "events": picked}, indent=1, default=str))
        return 0
    show_machines(machines)
    show_events(picked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
