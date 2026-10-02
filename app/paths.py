"""
Where things live — the one place that knows, and the only file a frozen build
needs to be right about.

A packaged app has two directories and they behave completely differently:

* **Resources.** What shipped inside the bundle: the fonts, the Three.js viewer,
  the owlet binaries, the seed database and the seed CAD model. On Windows this
  lands under `Program Files`, which a normal user account **cannot write to**,
  and under PyInstaller it is a temporary extraction directory that is deleted
  when the app exits. Read from it; never write to it.
* **Data.** What this machine has accumulated: the live database, the telemetry
  samples, the CAD model somebody uploaded, the judges slides somebody dropped
  in. Per-user, persistent, writable, and **never inside the bundle**.

Running from a checkout, both are the repo — so the dev loop is exactly what it
was, the existing `data/` keeps being used, and nothing has to be seeded.

`PIT_DISPLAY_DATA` overrides the data directory anywhere, which is how the pit
machine can put its database on a stick, and how a test can get a scratch one.

**Nothing outside this module may build a path from `__file__`.** That is what
broke when the app was frozen: `Path(__file__).parent.parent / "assets"` is a
path inside a zip that does not exist on disk.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "Breakaway Pit Display"

# Seeded into the data directory on first run: what a fresh install should
# already have. Each entry is a path relative to both trees. Directories are
# copied whole; files individually. Nothing is ever overwritten — a seed is
# what you get when you have nothing, not a factory reset.
# **The CAD model and its subsystem map are deliberately not here.** Both are
# read through `find()`, which prefers the data tree and falls back to the
# bundle — so the shipped ones are used until somebody uploads a replacement,
# and a fresh install does not spend a third of a gigabyte and a minute of disk
# copying a file it already has.
_SEEDS: tuple[str, ...] = (
    "data/pit_display.db",
    "assets/judges_slides",
)


def is_frozen() -> bool:
    """True inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """
    The read-only tree that shipped with the app.

    PyInstaller extracts a one-file build to `sys._MEIPASS`; a one-folder build
    sets it to the folder next to the executable. From a checkout it is the
    repo.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """
    The writable tree this machine owns.

    From a checkout this is the repo, so a developer keeps using `data/` and
    `assets/` in place and nothing is copied anywhere.
    """
    override = os.environ.get("PIT_DISPLAY_DATA")
    if override:
        return Path(override).expanduser().resolve()
    if not is_frozen():
        return Path(__file__).resolve().parent.parent
    return _platform_data_dir()


def _platform_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "breakaway-pit-display"


def resource(*parts: str) -> Path:
    """A path inside the shipped, read-only tree."""
    return resource_root().joinpath(*parts)


def data(*parts: str) -> Path:
    """A path inside the writable tree. Parent directories are created."""
    path = data_root().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def data_dir(*parts: str) -> Path:
    """A *directory* inside the writable tree, created if missing."""
    path = data_root().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find(*parts: str) -> Path:
    """
    Data first, then resources — for anything an operator may have replaced.

    The CAD model is the case this exists for: one ships with the app and the
    crew can upload a new one mid-season without touching the install.
    """
    candidate = data_root().joinpath(*parts)
    if candidate.exists():
        return candidate
    return resource_root().joinpath(*parts)


def seed_user_data() -> list[str]:
    """
    Copy anything in `_SEEDS` that the data directory does not already have.

    Returns what it copied, for the log. **Never overwrites**: a database that
    already exists on this machine is the real one, and a seed that clobbered
    it would throw away a season of imported logs on every upgrade.
    """
    if data_root() == resource_root():
        return []          # running from a checkout; the two are the same tree

    copied: list[str] = []
    for rel in _SEEDS:
        if rel.endswith(".db") and not is_frozen():
            # In a checkout, data/ is the developer's own live database, not a
            # seed. A scratch data dir (a check, the validation app) must start
            # as a fresh install does: an empty file the migrations build,
            # which is exactly what CI's seed is.
            continue
        src = resource_root() / rel
        dst = data_root() / rel
        if not src.exists() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        copied.append(rel)
    return copied
