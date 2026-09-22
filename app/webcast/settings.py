"""
Which overhead screens are published to the pit LAN — `webcast.json`.

A JSON file beside the database rather than a row in it, for the same reason
`nexus.json` and `update.json` are: **this is the setting that has to survive a
restart and be readable without the app.** The two overhead screens are the
ones hanging from the ceiling on the far side of the pit, and if the machine
reboots on the morning of an event nobody wants to rediscover that the panels
went dark because a toggle defaulted to off.

That is not hypothetical here. `config`'s per-screen settings are held **in
memory only** — theme, content, slide index and the rest all reset on every
launch — so a webcast switch stored there would need re-arming every morning
by somebody standing at the control panel. The URLs the Pis point at have to
be stable across a power cut, so they live in a file.

**Nothing in here is a credential**, so this file is safe to paste into a bug
report. The whole feature is pit-local: a switch, a cable, and no route off
the bench. See `app/webcast/server.py` for why it binds what it binds.
"""

from __future__ import annotations

import json
from typing import Any

from app import paths

FILE_NAME = "webcast.json"

# The two overhead panels, and only those. The control screen is the
# operator's own and has no business on the network; the project screen is a
# touch panel, and publishing it would send pixels one way while every finger
# on the glass went nowhere — a screen that looks live and answers nothing is
# worse than one that is plainly not there.
PUBLISHABLE = ("presentation_a", "presentation_b")

# **3938 — the team number plus one.** An operator types this into a Pi across
# the pit from a number written on a whiteboard, and 3937 is the number every
# one of them already knows. 3937 itself is left for a second service; this is
# the first.
#
# Checked, rather than picked because it was memorable:
#
# * **IANA** has both registered — 3937 `dvbservdsc` (DVB Service Discovery)
#   and 3938 `dbcontrol-agent` (Oracle dbControl Agent). Neither is anything a
#   pit laptop runs: one is digital-TV broadcast discovery, the other is
#   Oracle Enterprise Manager's DB Control, deprecated after Oracle 12c.
#   Registration is not reservation — nothing enforces it, and binding needs
#   no privilege because both are above 1024.
# * **Ephemeral ranges cannot collide with it.** Windows hands out 49152-65535,
#   macOS the same, Linux 32768-60999. 3938 is below every one of them, so the
#   OS will never hand this port to some other program's outbound socket while
#   the app is closed — which is the failure that actually bites, because it is
#   intermittent and looks like the app is broken.
# * **Verified bindable** on this machine, on both 0.0.0.0 and loopback, with
#   nothing already listening.
#
# The one real risk is Windows-specific and worth knowing: Hyper-V, WSL2 and
# Docker Desktop reserve blocks of ports at boot, and a reserved block *does*
# refuse a bind. `netsh int ipv4 show excludedportrange protocol=tcp` lists
# them. `--self-check` reports the bind failure with the port in it, and the
# port is a setting, so the fix is one field.
DEFAULT_PORT = 3938
# Kept as the documented fallback: if something on a pit machine ever does
# take 3938, this is the other number the team already knows.
ALT_PORT = 3937


DEFAULTS: dict[str, Any] = {
    # Off until somebody asks for it. A listening socket nobody requested is a
    # surprise on a machine that is already serving the CAD viewer, and most
    # pits will hang these screens on a cable like they always have.
    "enabled": False,
    # Which screens are published, by id. Both by default *once enabled*,
    # because the reason to turn this on at all is that the cable run is the
    # problem, and that is rarely true of only one of them.
    "screens": list(PUBLISHABLE),
    "port": DEFAULT_PORT,
    # Bind address. `0.0.0.0` is correct here and is the opposite of the
    # Nexus webhook's default, which is the point worth pausing on: that port
    # is reached by a tunnel running on this same machine, so loopback is
    # enough. This one is reached by a *different* machine — the Pi across the
    # pit — so it has to be on the wire. `127.0.0.1` is offered only as a way
    # to test the pages on the pit machine itself without publishing anything.
    "bind": "0.0.0.0",
}

BINDS = ("0.0.0.0", "127.0.0.1")


def socket_port(port: int | None = None) -> int:
    """
    The websocket port: the page's port plus one.

    Derived rather than configured, because two numbers an operator has to
    keep consistent is one number too many — and the page is served by us, so
    it always knows where to dial. **A second port costs nothing
    operationally**: Windows Firewall prompts per *application*, not per port,
    so the rule raised for the page covers this too.
    """
    return (DEFAULT_PORT if port is None else int(port)) + 1


def path():
    return paths.data(FILE_NAME)


def _clamp(values: dict[str, Any]) -> dict[str, Any]:
    try:
        port = int(values["port"])
        values["port"] = port if 1024 <= port <= 65535 else DEFAULT_PORT
    except (TypeError, ValueError):
        values["port"] = DEFAULT_PORT
    bind = str(values.get("bind") or "").strip()
    values["bind"] = bind if bind in BINDS else DEFAULTS["bind"]
    # An unknown screen id in the file must not reach the renderer, which
    # would ask the control screen for a window that can never exist.
    wanted = values.get("screens")
    if not isinstance(wanted, list):
        wanted = list(DEFAULTS["screens"])
    values["screens"] = [s for s in PUBLISHABLE if s in wanted]
    values["enabled"] = bool(values.get("enabled"))
    return values


def load() -> dict[str, Any]:
    """Defaults, overlaid with whatever the file has. Never raises."""
    values = dict(DEFAULTS)
    try:
        stored = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _clamp(values)
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


def is_published(screen_id: str) -> bool:
    values = load()
    return bool(values["enabled"]) and screen_id in values["screens"]
