"""
`diagnostics.json` beside the database: whether this machine reports home.

A JSON file like `update.json`, readable when the database isn't. Nothing
in here syncs.

**Who reports by default** (Brayden, 2026-10-18): the dev Mac (a checkout,
channel `dev`) and every machine running beta (a beta build, or a stable
build whose updater follows `beta`). A stable pit stays quiet unless an
admin turns it on; `enabled` set either way overrides the default.
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "diagnostics.json"

DEFAULTS: dict[str, Any] = {
    # None = the default for this build (see the module note); True/False = chosen.
    "enabled": None,
    # A status snapshot goes home this often (one hub row per machine,
    # overwritten; home keeps every version). 144 writes a day at 10.
    "status_minutes": 10,
    # The quick checks run this often; a check that changes state is an event.
    "check_minutes": 5,
}


def path():
    return paths.data(FILE_NAME)


def load() -> dict[str, Any]:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                values[key] = stored[key]
    if values["enabled"] is not None:
        values["enabled"] = bool(values["enabled"])
    for key, low in (("status_minutes", 2), ("check_minutes", 1)):
        try:
            values[key] = max(low, int(values[key]))
        except (TypeError, ValueError):
            values[key] = DEFAULTS[key]
    return values


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return load()


def default_on() -> bool:
    """On for a checkout and for anything on the beta line."""
    try:
        from app import version
        if version.CHANNEL in ("dev", "beta") or not version.is_release():
            return True
        from app.update import settings as update_settings
        return update_settings.load().get("channel") == "beta"
    except Exception:
        return False


def enabled() -> bool:
    chosen = load()["enabled"]
    return default_on() if chosen is None else chosen


def why() -> str:
    """One line for the panel: why reporting is on or off here."""
    chosen = load()["enabled"]
    if chosen is True:
        return "turned on for this machine"
    if chosen is False:
        return "turned off for this machine"
    return ("on by default for development and beta builds" if default_on()
            else "off by default on the stable line")
