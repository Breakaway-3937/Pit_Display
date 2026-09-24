r"""
The install layout — how a new version becomes the running one without ever
touching the folder that is currently executing.

Windows will not let you overwrite a running `.exe`, and "replace the folder"
(what DEPLOYMENT.md used to say) therefore means closing the display, doing the
copy, and opening it again — by hand, on a machine that is usually unattended.
So the install is **versioned folders behind a link**:

    %LOCALAPPDATA%\Programs\Breakaway Pit Display\
        pointer.json              which version is current, and what preceded it
        current  ──────────────▶  versions\1.4.2          (a directory junction)
        versions\
            1.4.1\                the previous one, kept for rollback
            1.4.2\Breakaway Pit Display.exe

The shortcut — Start Menu, desktop, `shell:startup` — points at
`current\Breakaway Pit Display.exe`. Windows resolves the junction when the
process launches, so the running app holds handles on `versions\1.4.2\` and the
junction itself is not locked by anything. **Repointing it while the app runs is
therefore safe**, and the new version is simply what the next launch gets.

Three things about this are load bearing:

- **A directory junction, not a symlink.** Junctions need no privilege on
  Windows; symlinks need Developer Mode or an elevated shell, which a pit
  laptop has neither of. On macOS and Linux there is no such distinction and a
  symlink is used, swapped through `os.replace` so there is no instant with no
  link at all.
- **Never `shutil.rmtree` the link.** On a junction that walks *into* the target
  and deletes the version you are running. `os.rmdir` removes the reparse point
  and nothing else, and it is the only thing here allowed near `current`.
- **The staged build is self-checked before the pointer moves.** `--self-check`
  already exists to prove a bundle works on this machine; running it on the new
  folder means a broken build can be released, downloaded and extracted and
  still never become the app that opens tomorrow morning.

Installs that are not this shape — somebody unzipped the folder onto the
desktop — are detected and left completely alone. `is_managed()` is False, the
panel says so, and nothing here writes anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app import paths
from app.update.release import UpdateError

POINTER = "pointer.json"
VERSIONS = "versions"
CURRENT = "current"

APP_NAME = paths.APP_DIR_NAME              # "Breakaway Pit Display"

# How long the staged build gets to prove itself. A cold first run has to
# unpack Chromium and build four windows; a minute is normal, four is not.
VERIFY_TIMEOUT = 300

# Versions kept on disk: the one running and the one to fall back to. Each is
# about a gigabyte, and a third buys nothing an operator would ever use.
KEEP_VERSIONS = 2


# ── Where we are ─────────────────────────────────────────────────────────────

def executable() -> Path:
    """
    This build's launcher, with junctions resolved.

    `.resolve()` matters: depending on how it was started, Windows may report
    `sys.executable` through the junction or through the real path, and the
    layout check below only works on the real one.
    """
    return Path(sys.executable).resolve()


def install_root() -> Path | None:
    """
    The managed install this app is running out of, or None.

    Recognised by shape — `<root>/versions/<version>/<exe>` with a pointer file
    at the root — so an unzipped-anywhere copy is never mistaken for one.
    """
    if not paths.is_frozen():
        return None
    exe = executable()
    version_dir = exe.parent
    # macOS .app bundles put the executable three levels deeper.
    if version_dir.name == "MacOS" and version_dir.parent.name == "Contents":
        version_dir = version_dir.parent.parent.parent
    versions_dir = version_dir.parent
    root = versions_dir.parent
    if versions_dir.name != VERSIONS or not (root / POINTER).exists():
        return None
    return root


def is_managed() -> bool:
    return install_root() is not None


def versions_dir() -> Path | None:
    root = install_root()
    return None if root is None else root / VERSIONS


def link_path() -> Path | None:
    root = install_root()
    return None if root is None else root / CURRENT


def default_root() -> Path:
    """
    Where a fresh install goes when nobody says otherwise.

    Per-user, so no administrator is needed to install *or* to update — the
    pit laptop's operator account owns it outright. `Program Files` would need
    elevation on every update, which is the thing this design exists to avoid.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Programs" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Applications" / APP_NAME
    return Path.home() / ".local" / "opt" / "breakaway-pit-display"


# ── The pointer ──────────────────────────────────────────────────────────────

def read_pointer(root: Path | None = None) -> dict:
    root = root or install_root()
    if root is None:
        return {}
    try:
        data = json.loads((root / POINTER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_pointer(root: Path, **changes) -> dict:
    data = read_pointer(root)
    data.update(changes)
    (root / POINTER).write_text(json.dumps(data, indent=2) + "\n",
                                encoding="utf-8")
    return data


def installed_versions() -> list[str]:
    vdir = versions_dir()
    if vdir is None or not vdir.is_dir():
        return []
    return sorted(p.name for p in vdir.iterdir()
                  if p.is_dir() and not p.name.endswith(".staging"))


def previous_version() -> str:
    return str(read_pointer().get("previous", ""))


# ── Linking ──────────────────────────────────────────────────────────────────

def _remove_link(link: Path) -> None:
    """
    Remove the link and *only* the link.

    `shutil.rmtree` on a junction descends into the target and deletes the
    version currently running. `os.rmdir` removes the reparse point itself and
    fails loudly on a real directory, which is the guard we want.
    """
    if not link.exists() and not link.is_symlink():
        return
    if link.is_symlink():
        link.unlink()
        return
    try:
        os.rmdir(link)
    except OSError as e:
        raise UpdateError(
            f"{link} is a real folder, not a link to a version. This install "
            "was not set up by the installer and cannot update itself safely.") from e


def point_at(root: Path, version: str) -> None:
    """Repoint `current` at `versions/<version>`."""
    target = root / VERSIONS / version
    if not target.is_dir():
        raise UpdateError(f"Version {version} is not installed.")
    link = root / CURRENT

    if sys.platform == "win32":
        _remove_link(link)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or not link.exists():
            raise UpdateError(
                "Could not point the launcher at the new version "
                f"({result.stderr.strip() or 'mklink failed'}). The old version "
                "is still installed; run --rollback if the shortcut is broken.")
        return

    # POSIX: build the new link beside the old and rename over it, so there is
    # never a moment with no `current` at all.
    staging = link.parent / (CURRENT + ".new")
    if staging.is_symlink() or staging.exists():
        staging.unlink()
    os.symlink(target, staging, target_is_directory=True)
    os.replace(staging, link)


# ── Staging a downloaded build ───────────────────────────────────────────────

def _strip_root(names: list[str]) -> str:
    """
    The single top-level folder inside the zip, if there is exactly one.

    `build_app.py` writes the app folder as the zip's root, so extracting
    naively would give `versions/1.4.2/Breakaway Pit Display/…exe` and a
    launcher path with the name in it twice.
    """
    tops = {n.split("/", 1)[0] for n in names if n and not n.startswith("/")}
    return tops.pop() + "/" if len(tops) == 1 else ""


def stage(zip_path: Path, version: str,
          progress: Callable[[str, float], None] | None = None) -> Path:
    """
    Extract into `versions/<version>`, via a `.staging` name.

    A half-extracted folder that shares its final name is indistinguishable
    from a finished one, and the next launch would find a version with no
    executable in it. The rename at the end is what makes it exist.
    """
    root = install_root()
    if root is None:
        raise UpdateError("This is not a managed install — see DEPLOYMENT.md.")

    final = root / VERSIONS / version
    staging = root / VERSIONS / f"{version}.staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    staging.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.namelist() if not m.endswith("/")]
        prefix = _strip_root(z.namelist())
        total = len(members) or 1
        for i, name in enumerate(members):
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not rel or ".." in Path(rel).parts or Path(rel).is_absolute():
                continue          # a zip is untrusted input even when we made it
            out = staging / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            # Zip files carry no executable bit on Windows-built archives, so
            # the launcher and Chromium's helper come out unrunnable on POSIX.
            if not out.suffix or out.suffix in (".sh", ".dylib", ".so"):
                out.chmod(out.stat().st_mode | 0o755)
            if progress and i % 200 == 0:
                progress(f"Unpacking {i:,} of {total:,} files", i / total)

    staging.rename(final)
    return final


def launcher_in(folder: Path) -> Path | None:
    """The executable inside a staged or installed version folder."""
    candidates = [
        folder / f"{APP_NAME}.exe",
        folder / APP_NAME,
        folder / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def verify(folder: Path,
           progress: Callable[[str, float], None] | None = None) -> tuple[bool, str]:
    """
    Run `--self-check` inside the staged build. True means it may go live.

    Three environment overrides make this safe to run *while the real app is
    running*, which is the whole point of verifying before the swap:

    * `PIT_DISPLAY_DATA` — a scratch data tree, so the check does not apply the
      new build's migrations to the live database before the new build is the
      one in charge.
    * `PIT_CAD_PORT` — a different port, so it does not take :8765 out from
      under the CAD viewer that is on screen in the pit right now.
    * `PIT_LEDS_FAKE` — so it does not open the LED controller's serial port
      while the running app has it.
    """
    exe = launcher_in(folder)
    if exe is None:
        return False, "no executable in the downloaded build"

    if progress:
        progress("Checking the new version on this machine", 0.0)

    with tempfile.TemporaryDirectory(prefix=".pit_verify_") as scratch:
        env = dict(os.environ)
        env["PIT_DISPLAY_DATA"] = scratch
        env["PIT_CAD_PORT"] = "0"          # 0 = pick any free port
        env["PIT_LEDS_FAKE"] = "1"
        # The launcher splash is drawn before Python runs, so the app can't
        # skip it itself; this is the bootloader's own switch. Without it the
        # check would flash a splash over whatever the pit is showing.
        env["PYINSTALLER_SUPPRESS_SPLASH_SCREEN"] = "1"
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            result = subprocess.run(
                [str(exe), "--self-check"], env=env, capture_output=True,
                text=True, timeout=VERIFY_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return False, (f"the new version did not finish its self-check in "
                           f"{VERIFY_TIMEOUT}s")
        except OSError as e:
            return False, f"the new version would not start: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        failed = [ln.strip() for ln in output.splitlines() if ln.startswith("[FAIL]")]
        detail = "; ".join(failed) or f"exit status {result.returncode}"
        return False, f"the new version failed its self-check — {detail}"
    return True, output.strip().splitlines()[-1] if output.strip() else "self-check passed"


# ── Going live ───────────────────────────────────────────────────────────────

def activate(version: str) -> str:
    """
    Make `version` what the next launch runs. Returns the version replaced.

    The running process is untouched — it keeps its handles on the folder it
    started from, which is also why the old version cannot be deleted yet.
    `prune()` on the next launch clears it.
    """
    root = install_root()
    if root is None:
        raise UpdateError("This is not a managed install — see DEPLOYMENT.md.")
    was = str(read_pointer(root).get("current", ""))
    point_at(root, version)
    write_pointer(root, current=version, previous=was,
                  updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return was


def rollback() -> str:
    """
    Go back to the version that was running before the last update.

    The recovery path for a build that passed its self-check and is still wrong
    — a screen that comes up blank, a panel nobody can read. Also reachable
    without the GUI: `"Breakaway Pit Display" --rollback`.
    """
    root = install_root()
    if root is None:
        raise UpdateError("This is not a managed install — nothing to roll back.")
    pointer = read_pointer(root)
    target = str(pointer.get("previous", ""))
    if not target:
        raise UpdateError("There is no previous version on this machine.")
    if not (root / VERSIONS / target).is_dir():
        raise UpdateError(f"Version {target} is no longer on disk.")
    current = str(pointer.get("current", ""))
    point_at(root, target)
    write_pointer(root, current=target, previous=current,
                  updated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return target


def prune(keep: int = KEEP_VERSIONS) -> list[str]:
    """
    Delete old version folders, newest kept.

    Called at startup, not at update time: the folder being replaced is still
    open by the running process when the pointer moves, and Windows will not
    delete it until that process is gone.
    """
    from app.version import parse

    root = install_root()
    if root is None:
        return []
    pointer = read_pointer(root)
    protected = {str(pointer.get("current", "")), str(pointer.get("previous", "")),
                 running_version()}
    ordered = sorted(installed_versions(), key=parse, reverse=True)
    removed: list[str] = []
    for name in ordered[keep:]:
        if name in protected:
            continue
        shutil.rmtree(root / VERSIONS / name, ignore_errors=True)
        if not (root / VERSIONS / name).exists():
            removed.append(name)
    # A staging folder left by an interrupted update is always junk.
    for junk in (root / VERSIONS).glob("*.staging"):
        shutil.rmtree(junk, ignore_errors=True)
    return removed


def running_version() -> str:
    """Which version folder this process is actually executing from."""
    root = install_root()
    if root is None:
        return ""
    exe = executable()
    for parent in exe.parents:
        if parent.parent == root / VERSIONS:
            return parent.name
        if parent == root / VERSIONS:
            break
    return ""


def relaunch() -> bool:
    """Start the current launcher and let the caller quit. Best effort."""
    link = link_path()
    if link is None:
        return False
    exe = launcher_in(link)
    if exe is None:
        return False
    try:
        subprocess.Popen([str(exe)], cwd=str(link),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                         start_new_session=(sys.platform != "win32"))
        return True
    except OSError:
        return False
