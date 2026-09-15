"""
The event feed's preferences — `nexus.json` beside the database.

A JSON file rather than a table, for the same reason `update.json` is: an
operator at an event with a database that will not open still needs to be
able to change the event key, and a file they can open in Notepad is reachable
when the app is not. It sits in the data directory beside the secret folder,
so an upgrade never touches it and a copied database never carries it.

**The API key is not in here.** It is a credential and lives in
`secrets/nexus_api_key` (`app/credentials.py`); this file is safe to paste
into a bug report.
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

DEFAULTS: dict[str, Any] = {
    # Which event this pit is at. `2024casf`-style official code, or the
    # team's demo key from frc.nexus/api. Empty means the feed is off.
    "event_key": "",
    # Pull the live snapshot on a timer while an event key is set.
    "auto_poll": True,
    "poll_interval_s": 30,
    "slow_poll_interval_s": 300,
    # Accept Nexus's push webhooks on a local HTTP port. Off by default: the
    # pit laptop is behind event wifi and Nexus can only reach it through a
    # tunnel somebody has set up, and a listening port nobody asked for is a
    # surprise on a machine that also serves the CAD viewer.
    "webhook_enabled": False,
    "webhook_port": 8766,
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
        port = int(values["webhook_port"])
        values["webhook_port"] = port if 1024 <= port <= 65535 else DEFAULTS["webhook_port"]
    except (TypeError, ValueError):
        values["webhook_port"] = DEFAULTS["webhook_port"]
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
