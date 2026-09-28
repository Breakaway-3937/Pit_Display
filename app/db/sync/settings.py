"""
This machine's sync preferences and identity — `sync.json` beside the database.

A JSON file, not a table, for two reasons. The usual one (`nexus.json`,
`update.json`): it has to be readable when the database isn't. And the one
that matters here: **the machine id must not travel with the database.** A
database copied from one pit laptop to another would otherwise carry the
first machine's identity, and the hub would take both machines' pushes as one
machine agreeing with itself. That's exactly the "settings switched around
between devices" failure this feature exists to end.

Nothing in here is team-owned and nothing in here syncs. The token is a
credential and lives in `secrets/sync_token` (`app/credentials.py`).
"""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any

from app import paths

FILE_NAME = "sync.json"

# The hub's one static address. Not a secret; the token that goes with it is.
DEFAULT_URL = "https://sync.bh-stack.com"
TOKEN_NAME = "sync_token"

MIN_INTERVAL_S = 15

DEFAULTS: dict[str, Any] = {
    # On once a token is present; this switch is for turning it off on
    # purpose (a demo laptop that must not touch the team's data).
    "enabled": True,
    "url": DEFAULT_URL,
    # Generated once, never edited. See the module docstring.
    "machine_id": "",
    # What the hub and the home server call this machine. Defaults to the
    # computer's name, which is what the crew will recognise.
    "machine_name": "",
    # How often a cycle runs with nothing to do. A local change or the
    # "Sync now" button runs one straight away.
    "interval_s": 60,
    # Download other pits' robot logs. Each session is a few MB to tens of MB.
    "pull_logs": True,
    # Upload the original log file too, zstd-compressed, for the home archive.
    # Off by default: the bundle already carries every record (audited
    # lossless), at ~3 MB where the original is ~110 MB even compressed.
    # Turn on for a machine that syncs over home Wi-Fi.
    "upload_raw": False,
    # Team files (judges slides, the CAD model). The model is ~300 MB.
    "sync_files": True,
    # Written by the service, read by the panel.
    "last_sync": "",
    "last_result": "",
}


def path():
    return paths.data(FILE_NAME)


def _clamp(values: dict[str, Any]) -> dict[str, Any]:
    url = str(values.get("url") or "").strip().rstrip("/")
    values["url"] = url if url.startswith(("https://", "http://")) else DEFAULT_URL
    try:
        values["interval_s"] = max(MIN_INTERVAL_S, int(values["interval_s"]))
    except (TypeError, ValueError):
        values["interval_s"] = DEFAULTS["interval_s"]
    for key in ("enabled", "pull_logs", "upload_raw", "sync_files"):
        values[key] = bool(values.get(key))
    return values


def _read() -> dict[str, Any]:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                values[key] = stored[key]
    return _clamp(values)


def _write(values: dict[str, Any]) -> None:
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass          # a read-only data dir is already reported by --self-check


def load() -> dict[str, Any]:
    """Defaults, overlaid with the file. Mints the machine id on first read."""
    values = _read()
    if not values["machine_id"]:
        values["machine_id"] = "pit-" + uuid.uuid4().hex[:16]
        _write(values)
    if not values["machine_name"]:
        values["machine_name"] = (socket.gethostname() or values["machine_id"])[:80]
    return values


def save(**changes: Any) -> dict[str, Any]:
    """Merge `changes` into the file and return the new full set."""
    values = load()
    changes.pop("machine_id", None)         # identity is never edited
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    values = _clamp(values)
    _write(values)
    return values


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))
