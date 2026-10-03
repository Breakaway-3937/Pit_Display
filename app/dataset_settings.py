"""
Which of home's datasets may appear on the overhead screens: a team setting.

Home sends standard datasets (`home_dataset`, home/REQUESTS.md R10); the
team's adults review each before it goes on a screen, so **every dataset is
off until someone turns it on** (Control, admin). A team setting (setting doc
`datasets`), so one review covers every pit. `datasets.json` beside the
database.
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "datasets.json"
DEFAULTS: dict[str, Any] = {"enabled": []}


def path():
    return paths.data(FILE_NAME)


def load() -> dict[str, Any]:
    values = {"enabled": []}
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict) and isinstance(stored.get("enabled"), list):
        values["enabled"] = sorted({str(k) for k in stored["enabled"] if k})
    return values


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    if isinstance(changes.get("enabled"), list):
        values["enabled"] = sorted({str(k) for k in changes["enabled"] if k})
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return values


def is_on(key: str) -> bool:
    return key in load()["enabled"]


def set_on(key: str, on: bool) -> None:
    keys = set(load()["enabled"])
    (keys.add if on else keys.discard)(key)
    save(enabled=sorted(keys))
