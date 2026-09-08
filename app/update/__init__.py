"""
Self-updating: how a build made on the Mac reaches the Windows pit machine.

    tag on GitHub ─▶ Actions builds + self-checks ─▶ Release + manifest.json
                                                          │
                        pit machine, on a 6-hour timer ◀───┘
                                                          │
              download ─▶ unpack ─▶ --self-check ─▶ repoint `current`
                                                          │
                                          next launch is the new version

| File | Role |
|---|---|
| `release.py` | The GitHub feed. Token, manifest, SHA-256'd download. No Qt |
| `install.py`  | Versioned folders behind a link; stage, verify, activate, roll back |
| `settings.py` | Channel and auto-check, in a JSON file rather than the database |
| `service.py`  | `_UpdateService` singleton — the state machine the panel draws |

Two properties everything here is built to keep:

- **A build that fails on this machine never becomes the running app.** The
  staged version is put through `--self-check` before the pointer moves.
- **Nothing an audience can see is ever interrupted.** The download and the
  swap touch neither the running process nor any window it owns.
"""

from app.update.service import init_update, update

__all__ = ["update", "init_update"]
