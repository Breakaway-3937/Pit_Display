"""
The pit setup file — how keys and the event get onto a pit machine.

Keys are typed once, on the Mac, into the secret folder. Every pit machine then
needs the same keys and the same event, and typing a 40-character API key
into an admin field on a laptop under a table at an event is how a key ends
up with a typo in it. So the whole setup travels as **one JSON file**:

    {
      "pit_setup": 1,
      "made": "2026-09-15T20:11:03Z",
      "secrets": {
        "nexus_relay_token": "…",             the one key a pit machine needs
        "nexus_api_key": "…"                  optional — direct fallback
      },
      "update_token": "…",                     optional — the GitHub PAT
      "nexus": {"event_key": "2026casf"}       optional — any nexus.json key
    }

Made with `tools/pit_setup.py make` (or Export on the Event Feed panel) and
applied on the pit machine in whichever of three ways is at hand:

1. **Drop it in the data directory** as `pit-setup.json` — next to the
   database, `%LOCALAPPDATA%\\Breakaway Pit Display\\` on Windows — and start
   the app. `auto_import()` runs first thing in `main()`, applies it, and
   renames it `pit-setup.json.imported` so it is not re-applied over a later
   change made on the panel.
2. **Control → Event Feed → Import setup file…** (admin), from a stick.
3. **`"Breakaway Pit Display" --provision D:\\pit-setup.json`** from a prompt,
   for a script.

All three call `apply()`, and `apply()` only ever *writes* — it never deletes
a key that the file does not mention, so a file carrying just an event key
leaves the machine's keys alone. What it changed comes back as a list of
lines, for the console or the panel.

**This file is a credential.** It is a convenience for moving keys, not a
place to keep them: `.gitignore` excludes `*.pitsetup.json` and
`pit-setup.json`, and the panel's Export writes wherever the operator points
it — a stick, not the repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import credentials, paths

FORMAT = 1
AUTO_FILE = "pit-setup.json"
SUFFIX = ".pitsetup.json"

# What may travel in a setup file, so a typo in a hand-edited one is refused
# rather than silently written as a credential nobody reads.
KNOWN_SECRETS = ("nexus_relay_token", "nexus_api_key")
# Secrets an older build carried. Skipped with a note, never refused: a setup
# file made last month should still deliver its event key and API key.
RETIRED_SECRETS = ("nexus_webhook_token",)


class ProvisionError(Exception):
    """A setup file that cannot be applied, phrased for whoever ran it."""


@dataclass
class Setup:
    secrets: dict[str, str] = field(default_factory=dict)
    update_token: str = ""
    nexus: dict[str, Any] = field(default_factory=dict)
    made: str = ""
    # What was in the file and deliberately skipped — reported, not applied.
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.secrets or self.update_token or self.nexus)

    def to_json(self) -> str:
        body: dict[str, Any] = {"pit_setup": FORMAT,
                                "made": self.made or _now()}
        if self.secrets:
            body["secrets"] = dict(self.secrets)
        if self.update_token:
            body["update_token"] = self.update_token
        if self.nexus:
            body["nexus"] = dict(self.nexus)
        return json.dumps(body, indent=2) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Reading ──────────────────────────────────────────────────────────────────

def load(path: Path) -> Setup:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise ProvisionError(f"Could not read {path}: {e}") from e
    except ValueError as e:
        raise ProvisionError(f"{path} is not valid JSON: {e}") from e
    return parse(raw, str(path))


def parse(raw: Any, name: str = "setup file") -> Setup:
    if not isinstance(raw, dict) or "pit_setup" not in raw:
        raise ProvisionError(f"{name} is not a pit setup file (no \"pit_setup\" field).")
    try:
        version = int(raw["pit_setup"])
    except (TypeError, ValueError):
        version = -1
    if version != FORMAT:
        raise ProvisionError(f"{name} is pit_setup format {raw['pit_setup']}; "
                             f"this app reads format {FORMAT}.")

    setup = Setup(made=str(raw.get("made") or ""))

    secrets = raw.get("secrets") or {}
    if not isinstance(secrets, dict):
        raise ProvisionError(f"{name}: \"secrets\" must be an object.")
    for key, value in secrets.items():
        if key in RETIRED_SECRETS:
            setup.notes.append(f"skipped secret {key} — no longer used "
                               "(the relay holds the webhook token now)")
            continue
        if key not in KNOWN_SECRETS:
            raise ProvisionError(
                f"{name}: unknown secret \"{key}\". Known: {', '.join(KNOWN_SECRETS)}.")
        if value is None:
            continue
        if not isinstance(value, str):
            raise ProvisionError(f"{name}: secret \"{key}\" must be a string.")
        if value.strip():
            setup.secrets[key] = value.strip()

    token = raw.get("update_token")
    if token is not None:
        if not isinstance(token, str):
            raise ProvisionError(f"{name}: \"update_token\" must be a string.")
        setup.update_token = token.strip()

    nexus = raw.get("nexus") or {}
    if not isinstance(nexus, dict):
        raise ProvisionError(f"{name}: \"nexus\" must be an object.")
    from app.nexus import settings as nexus_settings
    for key, value in nexus.items():
        if key in nexus_settings.RETIRED:
            setup.notes.append(f"skipped nexus.{key} — no longer used")
            continue
        if key not in nexus_settings.DEFAULTS:
            raise ProvisionError(
                f"{name}: unknown nexus setting \"{key}\". Known: "
                f"{', '.join(nexus_settings.DEFAULTS)}.")
        setup.nexus[key] = value
    return setup


# ── Applying ─────────────────────────────────────────────────────────────────

def apply(setup: Setup) -> list[str]:
    """
    Write everything the setup carries. Returns one line per change.

    Only writes. A key absent from the file is left as it is on the machine,
    so a file carrying just an event key never clears a credential.
    """
    done: list[str] = list(setup.notes)
    for name, value in setup.secrets.items():
        before = credentials.read(name)
        credentials.write(name, value)
        done.append(f"{'replaced' if before else 'wrote'} secrets/{name}")

    if setup.update_token:
        from app.update.release import set_token, token
        before = token()
        set_token(setup.update_token)
        done.append(f"{'replaced' if before else 'wrote'} update_token")

    if setup.nexus:
        from app.nexus import settings as nexus_settings
        before = nexus_settings.load()
        after = nexus_settings.save(**setup.nexus)
        for key in setup.nexus:
            if before.get(key) != after.get(key):
                done.append(f"nexus.{key}: {before.get(key)!r} → {after.get(key)!r}")
            else:
                done.append(f"nexus.{key} already {after.get(key)!r}")
    if len(done) == len(setup.notes):
        done.append("nothing to apply — the file carries no keys or settings")
    return done


def apply_file(path: Path) -> list[str]:
    return apply(load(path))


# ── The drop-in ──────────────────────────────────────────────────────────────

def auto_path() -> Path:
    return paths.data_root() / AUTO_FILE


def auto_import() -> list[str]:
    """
    Apply `pit-setup.json` from the data directory, if one is waiting.

    Called first thing in `main()`, before any singleton reads a key. The
    file is renamed `.imported` afterwards so it is applied exactly once —
    re-applying on every launch would overwrite an event key the operator
    changed on the panel the day before. Never raises: a bad file is reported
    and left in place, named so the next person can see it.
    """
    path = auto_path()
    if not path.exists():
        return []
    try:
        lines = apply_file(path)
    except ProvisionError as e:
        return [f"pit-setup.json was NOT applied: {e}"]
    stamped = path.with_name(path.name + ".imported")
    try:
        stamped.unlink(missing_ok=True)
        path.rename(stamped)
        lines.append(f"renamed to {stamped.name}")
    except OSError as e:
        lines.append(f"could not rename {path.name} ({e}); it will be "
                     "applied again next launch")
    return lines


# ── Making one ───────────────────────────────────────────────────────────────

def current(include_update_token: bool = True,
            include_nexus: bool = True) -> Setup:
    """This machine's keys and event, as a setup file would carry them."""
    setup = Setup(made=_now())
    for name in KNOWN_SECRETS:
        value = credentials.read(name)
        if value:
            setup.secrets[name] = value
    if include_update_token:
        from app.update.release import token
        setup.update_token = token()
    if include_nexus:
        from app.nexus import settings as nexus_settings
        prefs = nexus_settings.load()
        setup.nexus = {
            "event_key": prefs["event_key"],
            "auto_poll": prefs["auto_poll"],
            "poll_interval_s": prefs["poll_interval_s"],
            "relay_enabled": prefs["relay_enabled"],
            "relay_url": prefs["relay_url"],
        }
    return setup


def write(setup: Setup, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(setup.to_json(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def describe(setup: Setup) -> list[str]:
    """What a setup file carries, without printing a key."""
    lines = []
    for name, value in setup.secrets.items():
        lines.append(f"secret  {name}   ({len(value)} chars, …{value[-4:]})")
    if setup.update_token:
        lines.append(f"secret  update_token   ({len(setup.update_token)} chars, "
                     f"…{setup.update_token[-4:]})")
    for key, value in setup.nexus.items():
        lines.append(f"nexus   {key} = {value!r}")
    if setup.made:
        lines.append(f"made    {setup.made}")
    return lines or ["(empty)"]
