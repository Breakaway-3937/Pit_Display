#!/usr/bin/env python3
"""
Hit the Nexus API from a terminal, with the key the app would use.

The first thing to run once a key is in `secrets/nexus_api_key`: it says
whether the key works, whether an event key exists, and prints exactly what
each endpoint returned — the raw JSON, or the parsed models, or both — so a
field that looks wrong on a screen can be traced back to what frc.nexus
actually sent.

    uv run python tools/nexus_probe.py events
    uv run python tools/nexus_probe.py status  2026casf
    uv run python tools/nexus_probe.py pits    2026casf
    uv run python tools/nexus_probe.py map     2026casf
    uv run python tools/nexus_probe.py inspection 2026casf
    uv run python tools/nexus_probe.py teams   2026casf
    uv run python tools/nexus_probe.py alliances 2026casf
    uv run python tools/nexus_probe.py all     2026casf          every endpoint
    uv run python tools/nexus_probe.py team    2026casf 3937     what the pit sees

    --raw     print the JSON as received instead of the parsed models
    --fake    use the bundled example payloads (no key, no network)

Every request goes through `app.nexus.api.Client`, so this exercises the
same code the display runs — a probe that works here works in the app.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8          # noqa: E402
from app.nexus import api                  # noqa: E402
from app.nexus.api import NexusError, when # noqa: E402

COMMANDS = ("events", "status", "pits", "map", "inspection", "teams",
            "alliances", "all", "team")


def _dump(obj) -> str:
    def default(o):
        if is_dataclass(o):
            return asdict(o)
        return str(o)
    return json.dumps(obj, indent=2, default=default, ensure_ascii=False)


def _raw(client: api.Client, path: str):
    """The unparsed body, for `--raw`."""
    if isinstance(client, api.FakeClient):
        fx = client._fx
        return {"/events": fx["events"], "/pits": fx["pits"],
                "/map": fx["map_simple"], "/inspection": fx["inspection"],
                "/teams": fx["teams"], "/alliances": fx["alliances"],
                "/status": fx["event_status"][2]}[path]
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    route = {"/events": "/events", "/status": f"/event/{key}",
             "/pits": f"/event/{key}/pits", "/map": f"/event/{key}/map",
             "/inspection": f"/event/{key}/inspection",
             "/teams": f"/event/{key}/teams",
             "/alliances": f"/event/{key}/alliances"}[path]
    return client._get(route)


def _ts(ms) -> str:
    dt = when(ms)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"


def show_status(s: api.EventStatus) -> None:
    print(f"event {s.event_key}   data as of {_ts(s.data_as_of)}")
    print(f"now queuing: {s.now_queuing or '—'}")
    print(f"{len(s.matches)} matches, {len(s.announcements)} announcements, "
          f"{len(s.parts_requests)} parts requests\n")
    for m in s.matches:
        t = m.times
        flag = f"  ← replay of {m.replay_of}" if m.replay_of else ""
        brk = f"  ⏸ {m.break_after}" if m.break_after else ""
        print(f"  {m.label:<26} {m.status:<13} "
              f"R {' '.join(x or '—' for x in (m.red_teams or ['?']))}"
              f"   B {' '.join(x or '—' for x in (m.blue_teams or ['?']))}"
              f"   est start {_ts(t.estimated_start)}{flag}{brk}")
    for a in s.announcements:
        print(f"\n  📣 {_ts(a.posted)}  {a.text}")
    for p in s.parts_requests:
        print(f"\n  🔧 {_ts(p.posted)}  team {p.requested_by} needs {p.parts}")


def show_team(s: api.EventStatus, client: api.Client, key: str, team: str) -> None:
    print(f"team {team} at {key}   (data as of {_ts(s.data_as_of)})\n")
    ours = s.matches_for(team)
    print(f"{len(ours)} matches in the schedule:")
    for m in ours:
        side = m.alliance_of(team)
        print(f"  {m.label:<26} {m.status:<13} {side:<4} "
              f"est start {_ts(m.times.estimated_start)}")
    nxt = s.next_for(team)
    cur = s.current_for(team)
    print(f"\nnext:    {nxt.label + ' · ' + nxt.status if nxt else '—'}")
    print(f"current: {cur.label if cur else '—'}")
    try:
        print(f"pit:     {client.pit_addresses(key).get(team, '—')}")
        insp = client.inspection(key).get(team)
        print(f"inspect: {insp.status or ('passed' if insp.inspected else 'not yet') if insp else '—'}"
              + (f"  #{insp.queue_position}" if insp and insp.queue_position else ""))
        al = client.alliances(key).alliance_of(team)
        print(f"alliance:{' #' + str(al.number) + ' ' + str(al.teams) if al else ' —'}")
    except NexusError as e:
        print(f"(slow endpoints: {e})")


def main(argv: list[str]) -> int:
    use_utf8()
    raw = "--raw" in argv
    fake = "--fake" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        return 2
    cmd = args[0]
    key = args[1] if len(args) > 1 else ""
    if cmd != "events" and not key:
        print("an event key is needed, e.g. 2026casf or your demo key")
        return 2

    if fake:
        os.environ["PIT_NEXUS_FAKE"] = "1"
    client = api.make_client()
    if not fake:
        print(f"key: {'present' if api.configured() else 'MISSING — put it in '
              + str(api.credentials.path(api.API_KEY_SECRET))}")
        if not api.configured():
            return 1

    try:
        if cmd == "events":
            data = _raw(client, "/events") if raw else client.events()
            if raw:
                print(_dump(data))
            else:
                for k, e in sorted(data.items(), key=lambda kv: kv[1].start):
                    print(f"  {k:<12} {e.name:<48} {_ts(e.start)[:10]} → "
                          f"{_ts(e.end)[:10]}{'   LIVE' if e.is_live else ''}")
        elif cmd == "status":
            if raw:
                print(_dump(_raw(client, "/status")))
            else:
                show_status(client.event_status(key))
        elif cmd == "team":
            team = args[2] if len(args) > 2 else "3937"
            show_team(client.event_status(key), client, key, team)
        elif cmd == "all":
            for name in ("status", "pits", "map", "inspection", "teams", "alliances"):
                print(f"\n═══ {name} ═══")
                fn = {"status": client.event_status, "pits": client.pit_addresses,
                      "map": client.pit_map, "inspection": client.inspection,
                      "teams": client.teams, "alliances": client.alliances}[name]
                try:
                    result = fn(key)
                    if name == "status":
                        show_status(result)
                    else:
                        print(_dump(result))
                except NexusError as e:
                    print(f"  ✗ {e}")
        else:
            fn = {"pits": client.pit_addresses, "map": client.pit_map,
                  "inspection": client.inspection, "teams": client.teams,
                  "alliances": client.alliances}[cmd]
            print(_dump(_raw(client, f"/{cmd}") if raw else fn(key)))
    except NexusError as e:
        print(f"✗ {e}" + (f"  (HTTP {e.code})" if e.code else ""))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
