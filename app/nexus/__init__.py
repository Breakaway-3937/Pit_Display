"""
The Nexus event feed — live match queuing, the pit map, inspection and the
alliance board from `https://frc.nexus`, on every screen that wants it.

    frc.nexus ──webhooks──▶ nexus-relay (nexus.bh-stack.com) ──wss push──▶ EventStatus ─┐
              ◀──pulls───                        ──/api/v1 mirror──▶ pits · map · …  ├─▶ `nexus` signals ─▶ boards
    frc.nexus ──direct GET, only when the relay cannot be reached──────────────────┘

| File | Role |
|---|---|
| `api.py`      | Every endpoint and every schema, typed. `FakeClient` for offline. No Qt |
| `settings.py` | Event key, cadences and the relay address, in `nexus.json` |
| `relay.py`    | `RelayLink` — the WebSocket to the relay: heartbeat, watchdog, backoff |
| `service.py`  | `_NexusService` singleton — polling, freshness, derived "our match" |

The relay token and the API key are in the secret folder (`app/credentials.py`), never here.
`NEXUS.md` is the reference for what every field means.
"""

from app.nexus.service import init_nexus, nexus

__all__ = ["nexus", "init_nexus"]
