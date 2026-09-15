"""
The secret folder — `secrets/` in the data directory, one file per credential.

Every key this app is handed lives here and nowhere else: not in the database
(which gets copied to sticks and attached to bug reports), not in a settings
JSON somebody opens in Notepad to change a port, and never in the repository.
From a checkout the folder is `secrets/` at the repo root and `.gitignore`
keeps everything in it but its README out of git; on a pit machine it sits
beside the database, so an upgrade — which replaces the whole install folder —
never touches it.

    secrets/
      README.md               committed — what goes here and how to get it
      nexus_api_key           the `Nexus-Api-Key` header value
      nexus_webhook_token     the `Nexus-Token` Nexus sends to our webhook

**A file is a credential; an environment variable overrides it.** Every name
here can be supplied as `PIT_SECRET_<NAME>` in upper case instead, which is how
a test, a CI job or a one-off script gets a key without writing it to disk.

The update token predates this folder and stays where it was
(`update_token`, see `app/update/release.py`) — moving a credential that pit
machines already carry is a migration, and one that would silently turn
updates off on every machine that missed it.

Reading never raises. A missing file is `""`, which every caller treats as
"not configured" — the correct state for a machine nobody has set up yet.
"""

from __future__ import annotations

import os
from pathlib import Path

from app import paths

FOLDER = "secrets"


def folder() -> Path:
    """The secret folder, created if missing."""
    return paths.data_dir(FOLDER)


def path(name: str) -> Path:
    return folder() / name


def env_name(name: str) -> str:
    return "PIT_SECRET_" + name.upper()


def read(name: str) -> str:
    """The credential, or `""`. Environment first, so a test can override the file."""
    env = os.environ.get(env_name(name), "").strip()
    if env:
        return env
    try:
        return path(name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def present(name: str) -> bool:
    return bool(read(name))


def write(name: str, value: str) -> None:
    """Write the credential, or delete its file when `value` is blank."""
    p = path(name)
    value = value.strip()
    if not value:
        p.unlink(missing_ok=True)
        return
    p.write_text(value + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)        # no-op on Windows; correct everywhere else
    except OSError:
        pass


def source(name: str) -> str:
    """Where the value came from, for a status line: `env`, `file` or `none`."""
    if os.environ.get(env_name(name), "").strip():
        return "env"
    try:
        if path(name).read_text(encoding="utf-8").strip():
            return "file"
    except OSError:
        pass
    return "none"
