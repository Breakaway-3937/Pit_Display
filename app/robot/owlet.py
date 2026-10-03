"""
Shelling out to CTRE's `owlet` to turn a `.hoot` into a `.wpilog`.

`.hoot` is a closed CTR Electronics format. Nothing in Python can read it, and
`owlet` — CTRE's own extractor, bundled in `tools/owlet/` — is the only thing
that can. So the first stage of the log pipeline is a subprocess:

    robot.hoot ──owlet -f wpilog──▶ robot.wpilog ──wpilog.py──▶ database

**Why wpilog and not mcap** (owlet's default): `.wpilog` is the format the rest
of the FRC world already writes, so the same reader handles both a hoot export
*and* the robot's own DataLogManager file, where the team's application-level
signals live. One reader, two sources.

## The conversion is throwaway

`convert()` writes into a scratch directory it owns and hands back a context
manager that deletes it. A 3.85 GB hoot produces a wpilog of roughly the same
size, and nothing reads it twice — the importer streams it straight into SQLite.
Keeping it would double the disk cost of every import for no benefit.

The scratch directory is created **beside the source file**, falling back to the
system temp dir only if that is not writable. The pit machine has room for the
hoot where the hoot already is; it may well not have gigabytes free on `C:`.
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from app import paths

# Shipped executables — read-only, inside the bundle when frozen.
BUNDLED_DIR = paths.resource("tools", "owlet")

# CTRE names its builds `owlet-<version>-<os><arch>`. Globs, not fixed names:
# upgrading owlet is dropping the new binaries in tools/owlet/ and deleting the
# old ones, with no code change here.
#
# The lists are **in preference order** — the first pattern that matches a file
# wins, so a machine with several builds present takes the one native to it and
# falls back to one it can emulate.
_PATTERNS: dict[str, dict[str, list[str]]] = {
    "Darwin": {
        # One universal binary covers Intel and Apple Silicon, so arch does not
        # branch here; the per-arch names are listed in case CTRE ever splits it.
        "arm64": ["owlet*macosuniversal", "owlet*macosarm64", "owlet*macos*"],
        "x86_64": ["owlet*macosuniversal", "owlet*macosx86-64", "owlet*macos*"],
    },
    "Windows": {
        # Windows on ARM runs the x86-64 build under emulation, so it is a
        # genuine fallback rather than a wrong answer.
        "arm64": ["owlet*windowsarm64.exe", "owlet*windowsx86-64.exe"],
        "x86_64": ["owlet*windowsx86-64.exe"],
    },
    "Linux": {
        "arm64": ["owlet*linuxarm64", "owlet*linuxarm*"],
        "x86_64": ["owlet*linuxx86-64", "owlet*linuxx86*"],
    },
}

# What `platform.machine()` calls the two architectures, per OS. Windows says
# AMD64, macOS and Linux say x86_64, and 64-bit ARM answers to three names.
_ARCH_ALIASES = {
    "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64", "i686": "x86_64",
    "arm64": "arm64", "aarch64": "arm64", "armv8": "arm64",
}


def _patterns_for_this_machine() -> list[str]:
    """Filename globs to try on this OS and CPU, best first."""
    by_arch = _PATTERNS.get(platform.system())
    if not by_arch:
        return []
    arch = _ARCH_ALIASES.get(platform.machine().lower(), "x86_64")
    return by_arch.get(arch, by_arch["x86_64"])


class OwletError(Exception):
    """Conversion failed in a way the operator can act on."""


def find_owlet() -> Path | None:
    """
    The owlet binary for this machine, or None.

    Order: `$PIT_OWLET` (an explicit override always wins), then the bundled
    copy in `tools/owlet/`, then `owlet` on `PATH` for a system install.
    """
    override = os.environ.get("PIT_OWLET")
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p

    if BUNDLED_DIR.is_dir():
        for pattern in _patterns_for_this_machine():
            # Sorted so a directory holding two versions picks the higher one
            # rather than whatever the filesystem happened to return first.
            found = sorted(BUNDLED_DIR.glob(pattern))
            if found:
                return found[-1]

    on_path = shutil.which("owlet")
    return Path(on_path) if on_path else None


def available() -> bool:
    return find_owlet() is not None


def describe() -> str:
    """
    One line naming this machine and the binary picked for it.

    Worth surfacing in the UI: "no owlet for your platform" is the one import
    failure the operator can neither diagnose nor fix from the error alone, and
    the pit machine is a different OS from every machine this is developed on.
    """
    where = f"{platform.system()} {platform.machine()}"
    exe = find_owlet()
    if exe is None:
        return (f"{where}: no owlet binary found — .hoot import unavailable "
                f"(.wpilog still works)")
    return f"{where}: {exe.name}"


def _prepare(exe: Path) -> None:
    """
    Make `exe` runnable, or explain why it is not.

    Two things stop a freshly-downloaded owlet from starting, and neither
    produces a useful error on its own — the POSIX one is a bare `PermissionError`
    and the macOS one is SIGKILL with no output at all.
    """
    if os.name == "posix" and not os.access(exe, os.X_OK):
        try:
            exe.chmod(exe.stat().st_mode | 0o755)
        except OSError as err:
            raise OwletError(f"{exe.name} is not executable: {err}") from err

    if sys.platform == "darwin" and shutil.which("xattr"):
        # Gatekeeper kills a quarantined binary before main() — no exception to
        # catch, no output, just a dead subprocess. Clearing the attribute needs
        # no privileges, so it is done unconditionally rather than checked
        # first: `os.listxattr` is Linux-only, and shelling out to read the
        # attribute costs the same as shelling out to remove it. A binary that
        # was never quarantined makes `xattr -d` exit non-zero, which is fine.
        subprocess.run(["xattr", "-d", "com.apple.quarantine", str(exe)],
                       capture_output=True, check=False)


# A wpilog runs a little larger than the hoot it came from. Ask for half again,
# so a conversion cannot be the thing that fills the pit machine's disk.
_SCRATCH_HEADROOM = 1.5


def _has_room(where: Path, need: int) -> bool:
    try:
        return shutil.disk_usage(where).free > need
    except OSError:
        return False


def scratch_dir(near: Path) -> Path:
    """
    Create a scratch directory for the conversion of `near` and return it.

    Tried in order, first one with room winning:

    1. **`$PIT_LOG_SCRATCH`** — an explicit choice always wins, and is how the
       pit machine points this at the drive with space on it.
    2. **The system temp dir**, the ordinary answer.
    3. **Beside the source file**, only if temp cannot hold the conversion.

    The source's own folder is *last*, not first. Logs arrive on a USB stick, a
    shared drive, or a read-only export folder, and none of those are ours to
    write to — a crash mid-import would strand a multi-gigabyte `.pit_owlet_*`
    directory in somebody's log archive. It stays on the list because the disk
    that held a 3.85 GB hoot demonstrably has room for its conversion, and being
    unable to import at all is worse than an unexpected temp file.

    The caller owns the result — pair every call with `clear_scratch()`.
    """
    need = int(near.stat().st_size * _SCRATCH_HEADROOM) if near.is_file() else 0

    candidates: list[Path] = []
    override = os.environ.get("PIT_LOG_SCRATCH")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path(tempfile.gettempdir()))
    candidates.append(near.parent)

    for parent in candidates:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(parent, os.W_OK) and _has_room(parent, need):
            return Path(tempfile.mkdtemp(prefix=".pit_owlet_", dir=str(parent)))

    raise OwletError(
        f"Nowhere to extract {near.name}: about "
        f"{need / 1e9:.1f} GB is needed and none of the temp directory, "
        f"{near.parent}, or $PIT_LOG_SCRATCH has room.")


def clear_scratch(path: Path) -> None:
    """Remove a `scratch_dir()`. Never raises — cleanup must not fail an import."""
    shutil.rmtree(path, ignore_errors=True)


def convert(hoot: Path, out: Path,
            progress: Callable[[str], None] | None = None,
            fmt: str = "mcap") -> Path:
    """
    Extract `hoot` to `out` as a wpilog. Returns `out`.

    Retries once with `--unlicensed` if the first attempt trips the Phoenix Pro
    licence check. Pro only gates the *export*, not the signals this app stores,
    and a pit crew that cannot read last match's log because a licence server is
    unreachable is exactly the failure this app exists to avoid.
    """
    exe = find_owlet()
    if exe is None:
        raise OwletError(
            f"No owlet binary for {platform.system()} {platform.machine()}. "
            f"Put one in {BUNDLED_DIR} (CTRE names them "
            f"owlet-<version>-<os><arch>), or set PIT_OWLET to its path. "
            f"Alternatively, convert the .hoot yourself and import the .wpilog.")
    _prepare(exe)

    # mcap, not wpilog: owlet 26.3.0's wpilog writer cuts the end off most
    # conversions and still exits 0 (app/robot/mcap.py has the evidence);
    # its mcap is byte-identical every run and complete.
    args = [str(exe), "-f", fmt, str(hoot), str(out)]
    result = _run(args, progress)

    if result.returncode != 0 and _is_licence_failure(result):
        if progress:
            progress("Retrying without the Phoenix Pro licence check…")
        out.unlink(missing_ok=True)
        result = _run(args + ["--unlicensed"], progress)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise OwletError(
            f"owlet could not read {hoot.name} "
            f"(exit {result.returncode}){': ' + detail[-400:] if detail else ''}")

    if not out.is_file() or out.stat().st_size == 0:
        raise OwletError(
            f"owlet reported success but wrote no data for {hoot.name}.")
    return out


def _is_licence_failure(result: subprocess.CompletedProcess) -> bool:
    blob = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return "licens" in blob or "licenc" in blob or "phoenix pro" in blob


def _run(args: list[str],
         progress: Callable[[str], None] | None) -> subprocess.CompletedProcess:
    """
    Run owlet, forwarding whatever it prints to `progress` as it prints it.

    Streamed rather than captured in one go because extracting a multi-gigabyte
    hoot takes minutes, and a progress bar that says nothing for minutes is
    indistinguishable from a hung app.
    """
    creationflags = 0
    if sys.platform == "win32":                       # no console window
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, creationflags=creationflags)
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        lines.append(line)
        if progress:
            progress(line)
    proc.wait()
    return subprocess.CompletedProcess(args, proc.returncode,
                                       "\n".join(lines), "")
