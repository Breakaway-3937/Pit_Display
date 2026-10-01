"""
This machine's analysis preferences: `ai.json` beside the database.

A JSON file, like `sync.json` and `update.json`: which model a machine can run
is a fact about that machine (its memory, what's pulled), not team data, and
none of it syncs.
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "ai.json"

# Measured 2026-09-30 (CLAUDE.md, "Robot-log analysis"): 6.7 GB loaded at a
# 16k context, which fits a 16 GB pit machine beside Windows and the app. The
# 30B took ~20 GB and pushed a 24 GB Mac 11 GB into swap; the 4B never made a
# board. Qwen3 because its tool calling is reliable at this size.
DEFAULT_MODEL = "qwen3:8b"

DEFAULTS: dict[str, Any] = {
    # Analyse each newly imported log by itself.
    "auto_run": True,
    # "auto": the built-in engine (runtime.py) when its model is downloaded,
    # else Ollama if it's running. "llama" / "ollama" force one.
    "engine": "auto",
    "model": DEFAULT_MODEL,
    # A different model for the designer; empty = the same one (no swap).
    "designer": "",
    "num_ctx": 16384,
    # Unload the model this long after a run (Ollama's keep_alive; the
    # built-in engine's server is stopped after it).
    "keep_alive": "1m",
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
        values.update({k: stored[k] for k in DEFAULTS if k in stored})
    values["auto_run"] = bool(values["auto_run"])
    if values["engine"] not in ("auto", "llama", "ollama"):
        values["engine"] = "auto"
    values["model"] = str(values["model"] or DEFAULT_MODEL)
    try:
        values["num_ctx"] = max(4096, int(values["num_ctx"]))
    except (TypeError, ValueError):
        values["num_ctx"] = DEFAULTS["num_ctx"]
    return values


def save(**changes: Any) -> dict[str, Any]:
    values = load()
    values.update({k: v for k, v in changes.items() if k in DEFAULTS})
    try:
        path().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass          # a read-only data dir is already reported by --self-check
    return load()
