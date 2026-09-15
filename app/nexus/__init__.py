"""
The Nexus event feed — live match queuing, the pit map, inspection and the
alliance board from `https://frc.nexus`, on every screen that wants it.

    frc.nexus ──GET /event/{key} every 30s──▶ EventStatus ─┐
              ──POST (webhook, optional)────▶             ├─▶ `nexus` signals ─▶ boards
              ──pits · map · inspection · teams · alliances ┘

| File | Role |
|---|---|
| `api.py`      | Every endpoint and every schema, typed. `FakeClient` for offline. No Qt |
| `settings.py` | Event key, cadences and the webhook switch, in `nexus.json` |
| `webhook.py`  | The receiving HTTP server for the two push webhooks |
| `service.py`  | `_NexusService` singleton — polling, freshness, derived "our match" |

The key is in the secret folder (`app/credentials.py`), never here.
`NEXUS.md` is the reference for what every field means.
"""

from app.nexus.service import init_nexus, nexus

__all__ = ["nexus", "init_nexus"]
