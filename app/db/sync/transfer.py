"""
When team-file bytes may move: the night window (home/REQUESTS.md R8).

At an event the laptops stay on overnight and the signal is best then; by day
cell congestion breaks big transfers. So uploads and downloads of team files
(CAD, judges slides, music) start only between `start` and `end` by this
machine's clock, the event's time zone. Rows (every table, requests,
manifests) sync all day; only bytes wait. Robot-log bundles aren't covered.

A **team setting** (setting doc `transfer`), so home can switch `enforce` off
off-season. Enforced until anyone says otherwise. "Sync files now" on the
Telemetry panel opens it for one cycle (`Engine.force_files`).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

from app import paths

FILE_NAME = "transfer.json"
DEFAULTS: dict[str, Any] = {"start": "00:00", "end": "05:00", "enforce": True}


def path():
    return paths.data(FILE_NAME)


def _hhmm(value: Any, fallback: str) -> str:
    try:
        h, m = str(value).split(":")
        return f"{int(h) % 24:02d}:{int(m) % 60:02d}"
    except (ValueError, AttributeError):
        return fallback


def _clamp(values: dict[str, Any]) -> dict[str, Any]:
    values["start"] = _hhmm(values.get("start"), DEFAULTS["start"])
    values["end"] = _hhmm(values.get("end"), DEFAULTS["end"])
    values["enforce"] = bool(values.get("enforce"))
    return values


def load() -> dict[str, Any]:
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict):
        values.update({k: stored[k] for k in DEFAULTS if k in stored})
    return _clamp(values)


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    values = _clamp(values)
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return values


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day.date(), time(h, m))


def is_open(now: datetime | None = None, values: dict | None = None) -> bool:
    """May a team-file transfer start now? Handles a window past midnight."""
    v = values or load()
    if not v["enforce"]:
        return True
    now = now or datetime.now()
    start, end = _at(now, v["start"]), _at(now, v["end"])
    if start <= end:
        return start <= now < end
    return now >= start or now < end           # e.g. 22:00–05:00


def last_close(now: datetime | None = None, values: dict | None = None) -> datetime:
    """The most recent end of the window at or before `now` (when tonight's
    manifest is due)."""
    v = values or load()
    now = now or datetime.now()
    end = _at(now, v["end"])
    return end if end <= now else end - timedelta(days=1)


def describe(values: dict | None = None) -> str:
    v = values or load()
    if not v["enforce"]:
        return "Team files move any time (the night window is off)."
    state = "open now" if is_open(values=v) else "closed now"
    return f"Team files move {v['start']}–{v['end']} ({state})."
