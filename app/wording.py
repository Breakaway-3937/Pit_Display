"""
Screen wording an admin can edit in the app, without a code change.

Brayden, 2026-10-03: admins should be able to change the wording of the
pit-front panel and the words-only overhead slides themselves. Every such
piece of text is a **field**: a stable key, the text the code ships (the
default, and the fallback forever), a label, the group it's listed under, a
length limit that keeps it inside its layout, and whether it's a paragraph.

* **Registered where the text lives**: `interactive_board.py` and the
  presentation screens call `field()` at import, so the editor lists exactly
  what the screens use and a field can't drift from its screen.
* **Edits are a team setting** (setting doc `wording`, `wording.json` beside
  the database): one admin's edit reaches every pit, and a pit that never
  synced shows the shipped text.
* **Live**: a save emits `config.wording_changed`; the board updates its
  labels in place (the CAD viewer it borrows is never rebuilt) and the
  overhead slides rebuild. An emptied field falls back to the shipped text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app import paths

FILE_NAME = "wording.json"
DEFAULTS: dict[str, Any] = {"texts": {}}


@dataclass(frozen=True)
class Field:
    key: str
    default: str
    label: str
    group: str
    max_len: int
    multiline: bool = False


_FIELDS: dict[str, Field] = {}


def field(key: str, default: str, label: str, group: str,
          max_len: int | None = None, multiline: bool = False) -> str:
    """Register a field (once; re-registering keeps the first) and return its
    current text. `max_len` defaults to the shipped text's length plus half."""
    if key not in _FIELDS:
        _FIELDS[key] = Field(key, default, label, group,
                             max_len or max(24, int(len(default) * 1.5)), multiline)
    return text(key)


def fields() -> list[Field]:
    """Every registered field, in registration order."""
    _load_screens()
    return list(_FIELDS.values())


def _load_screens() -> None:
    """Import the modules that register fields, so the editor sees them all
    even when no screen has been built."""
    import app.widgets.interactive_board  # noqa: F401
    import app.windows.presentation_a  # noqa: F401
    import app.windows.presentation_b  # noqa: F401


def path():
    return paths.data(FILE_NAME)


_cache: tuple[float, dict] | None = None     # (file mtime, texts): the board reads ~70 keys


def load() -> dict[str, Any]:
    global _cache
    try:
        mtime = path().stat().st_mtime
    except OSError:
        mtime = -1.0
    if _cache is not None and _cache[0] == mtime:
        return {"texts": dict(_cache[1])}
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    texts = stored.get("texts") if isinstance(stored, dict) else None
    texts = {str(k): str(v) for k, v in (texts or {}).items() if v is not None}
    _cache = (mtime, texts)
    return {"texts": dict(texts)}


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    if isinstance(changes.get("texts"), dict):
        values["texts"] = {str(k): str(v) for k, v in changes["texts"].items()
                           if v is not None and str(v).strip()}
    global _cache
    _cache = None
    try:
        path().write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    except OSError:
        pass
    return values


def text(key: str) -> str:
    """The text on screen for `key`: the edit if there is one, else shipped."""
    edited = load()["texts"].get(key, "")
    f = _FIELDS.get(key)
    if edited.strip():
        return edited[:f.max_len] if f else edited
    return f.default if f else ""


def is_edited(key: str) -> bool:
    return bool(load()["texts"].get(key, "").strip())


def set_texts(texts: dict[str, str]) -> None:
    """Save edits (an empty string resets a field to its shipped text) and
    tell every screen."""
    current = load()["texts"]
    for k, v in texts.items():
        v = (v or "").strip()
        f = _FIELDS.get(k)
        if not v or (f and v == f.default):
            current.pop(k, None)
        else:
            current[k] = v[:f.max_len] if f else v
    save(texts=current)
    notify()


def notify() -> None:
    try:
        from app.config import config
        config.notify_wording_changed()
    except Exception:
        pass
