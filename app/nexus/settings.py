"""
The event feed's preferences — `nexus.json` beside the database.

A JSON file rather than a table, for the same reason `update.json` is: an
operator at an event with a database that will not open still needs to be
able to change the event key, and a file they can open in Notepad is reachable
when the app is not. It sits in the data directory beside the secret folder,
so an upgrade never touches it and a copied database never carries it.

**No key is in here.** The relay token and the API key are credentials and
live in `secrets/` (`app/credentials.py`); this file is safe to paste into a
bug report.
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "nexus.json"

# The live snapshot moves on a minute scale — a match takes ~6–7 minutes of
# field time and the estimates shift by seconds between polls — so 30s keeps
# "On deck" reaching the pit within half a minute without hammering a service
# that publishes no rate limit. Ten seconds is the floor.
MIN_POLL_S = 10
# Pits, teams, the map and alliances change on an hour scale (or once, at
# alliance selection); inspection is cached upstream for "a couple minutes".
MIN_SLOW_POLL_S = 60

# The relay's one static address. Not a secret — the token that goes with it
# is, and lives in `secrets/nexus_relay_token`.
DEFAULT_RELAY_URL = "https://nexus.bh-stack.com"

# Keys an older build wrote here and this one no longer reads. They are the
# local push webhook and its tunnel, replaced by the relay; `provision` skips
# them in an old setup file rather than refusing the whole file.
RETIRED = ("webhook_enabled", "webhook_port", "webhook_bind")

DEFAULTS: dict[str, Any] = {
    # Which event this pit is at. `2024casf`-style official code, or the
    # team's demo key from frc.nexus/api. Empty means the feed is off.
    "event_key": "",
    # Pull the live snapshot on a timer while an event key is set.
    "auto_poll": True,
    "poll_interval_s": 30,
    "slow_poll_interval_s": 300,
    # Take the live feed from the team's relay over a WebSocket. The relay is
    # the pit's one static address for everything Nexus: webhooks land on it,
    # it pulls Nexus itself when they go quiet, and it pushes each snapshot
    # here the moment it has one. Direct polling is the fallback.
    "relay_enabled": True,
    "relay_url": DEFAULT_RELAY_URL,
    # How long the relay socket may be down before the live snapshot is
    # polled instead. Long enough to ride out a reconnect; short enough that
    # a venue that blocks WebSockets costs a minute, not a match.
    "fallback_after_s": 60,
    # Written by the service, read by the panel.
    "last_poll": "",
    "last_result": "",
}


def path():
    return paths.data(FILE_NAME)


def _clamp(values: dict[str, Any]) -> dict[str, Any]:
    try:
        values["poll_interval_s"] = max(MIN_POLL_S, int(values["poll_interval_s"]))
    except (TypeError, ValueError):
        values["poll_interval_s"] = DEFAULTS["poll_interval_s"]
    try:
        values["slow_poll_interval_s"] = max(MIN_SLOW_POLL_S,
                                             int(values["slow_poll_interval_s"]))
    except (TypeError, ValueError):
        values["slow_poll_interval_s"] = DEFAULTS["slow_poll_interval_s"]
    try:
        values["fallback_after_s"] = max(15, int(values["fallback_after_s"]))
    except (TypeError, ValueError):
        values["fallback_after_s"] = DEFAULTS["fallback_after_s"]
    url = str(values.get("relay_url") or "").strip().rstrip("/")
    values["relay_url"] = (url if url.startswith(("https://", "http://"))
                           else DEFAULT_RELAY_URL)
    values["relay_enabled"] = bool(values.get("relay_enabled"))
    values["event_key"] = str(values.get("event_key") or "").strip()
    return values


def load() -> dict[str, Any]:
    """Defaults, overlaid with whatever the file has. Never raises."""
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                values[key] = stored[key]
    return _clamp(values)


def save(**changes: Any) -> dict[str, Any]:
    """Merge `changes` into the file and return the new full set."""
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    values = _clamp(values)
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass          # a read-only data dir is already reported by --self-check
    return values


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))
