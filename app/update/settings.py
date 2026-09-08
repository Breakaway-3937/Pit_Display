"""
The updater's own preferences — a small JSON file, deliberately not the database.

Everything else this app persists lives in `data/pit_display.db`, and this does
not, for one reason: **the updater has to be configurable when the app cannot
start.** A migration that fails, a database that will not open, a build that
boots into a traceback — those are exactly the states you need to change the
channel or turn auto-check off from, and a preference stored inside the thing
that is broken is not reachable then. A file an operator can open in Notepad is.

It sits beside the credential file in the data directory, so an upgrade — which
replaces the whole install folder — never touches it.

    %LOCALAPPDATA%\\Breakaway Pit Display\\update.json
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "update.json"

CHANNELS = ("stable", "beta")

DEFAULTS: dict[str, Any] = {
    # Which release feed this machine follows. `beta` also sees prereleases,
    # which is how one machine can take a build before the rest of the pit does.
    "channel": "stable",
    # Look for a release on a timer. Downloading is never automatic — see
    # `service.py`; a 400 MB download that starts itself during a match cycle
    # is the failure mode this whole feature has to avoid.
    "auto_check": True,
    "check_interval_hours": 6,
    # Written by the service, read by the panel.
    "last_check": "",
    "last_result": "",
}


def path():
    return paths.data(FILE_NAME)


def load() -> dict[str, Any]:
    """Defaults, overlaid with whatever the file has. Never raises."""
    values = dict(DEFAULTS)
    try:
        raw = path().read_text(encoding="utf-8")
        stored = json.loads(raw)
    except (OSError, ValueError):
        return values
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                values[key] = stored[key]
    if values["channel"] not in CHANNELS:
        values["channel"] = DEFAULTS["channel"]
    return values


def save(**changes: Any) -> dict[str, Any]:
    """Merge `changes` into the file and return the new full set."""
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    if values["channel"] not in CHANNELS:
        values["channel"] = DEFAULTS["channel"]
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass          # a read-only data dir is already reported by --self-check
    return values


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))
