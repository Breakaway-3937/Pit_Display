#!/usr/bin/env python3
"""
Build the Pit Display into a shippable app folder.

    uv run tools/build_app.py                 # everything, including the CAD model
    uv run tools/build_app.py --no-model      # ~340 MB smaller; upload one later
    uv run tools/build_app.py --zip           # also produce a zip (the update payload)
    uv run tools/build_app.py --installer     # Windows: the one file people are given
    uv run tools/build_app.py --clean         # throw away build/ and dist/ first

The result is `dist/Breakaway Pit Display/` — one folder, copy it anywhere on a
machine of the same OS and run the executable inside it. On macOS you also get
`dist/Breakaway Pit Display.app`.

**What is deliberately *not* in it: the music library.** The library is an index
of paths to the team's own audio, kept in the database; the audio files stay
wherever they live on the pit machine. Shipping a few gigabytes of music inside
an app nobody can update without a rebuild is the wrong shape, and the folder is
re-pointed in one click from Control → Music → Add folder.

**Two outputs, and they are for different readers.** `--installer` produces the
single `…-Setup.exe` a person downloads and double-clicks; `--zip` produces the
archive the *app* downloads when it updates itself. A release carries both, and
CI makes both from one build.

**PyInstaller does not cross-compile.** Run this on the OS you are shipping to.
For Windows without a Windows machine, push a tag and let
`.github/workflows/build.yml` do it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

use_utf8()   # Windows redirects stdout as cp1252; see app/console.py

APP_NAME = "Breakaway Pit Display"
SPEC = ROOT / "packaging" / "pit_display.spec"
ISS = ROOT / "packaging" / "installer.iss"

# Where Inno Setup puts its compiler. The GitHub Windows runner has it at the
# first of these; a developer machine that installed it normally, the second.
_ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def preflight(include_model: bool, want_installer: bool = False) -> None:
    """Fail before a ten-minute build rather than after it."""
    problems: list[str] = []

    if not (ROOT / "data" / "pit_display.db").exists():
        problems.append(
            "data/pit_display.db is missing — run the app once to create it, "
            "since it is the seed a fresh install starts from.")

    owlet = ROOT / "tools" / "owlet"
    binaries = [p for p in owlet.glob("owlet*")] if owlet.exists() else []
    if not binaries:
        problems.append(
            "tools/owlet/ has no owlet binaries — .hoot import will be dead on "
            "every machine this ships to. See tools/owlet/README.md.")
    else:
        have_windows = any("windows" in p.name for p in binaries)
        if not have_windows:
            print("  ! no windows owlet binary present — a Windows build will "
                  "not be able to import .hoot files", file=sys.stderr)

    # Windows has no libVLC of its own. A build without this one is not
    # broken — it boots, and everything but music works — which is exactly why
    # it needs saying out loud here rather than being discovered at an event.
    if sys.platform == "win32" and not (ROOT / "vlc" / "libvlc.dll").exists():
        print("  ! vlc/ is empty — music and the equaliser will be dead on "
              "every machine this ships to. Run `uv run tools/fetch_vlc.py`.",
              file=sys.stderr)

    if want_installer:
        # Checked in preflight, not at the point of use: ISCC runs *after* a
        # ten-minute package, and a typo in the .iss should not cost that.
        from tools.check_installer import main as check_installer
        if check_installer() != 0:
            problems.append("packaging/installer.iss has problems (above)")
        elif sys.platform == "win32" and find_iscc() is None:
            problems.append(
                "Inno Setup is not installed, so --installer cannot run. "
                "`choco install innosetup -y`, or get it from "
                "https://jrsoftware.org/isdl.php")

    if include_model and not (ROOT / "assets" / "cad" / "robot.glb").exists():
        print("  ! assets/cad/robot.glb is missing — building without a CAD "
              "model; upload one from the control screen after install",
              file=sys.stderr)

    try:
        import PyInstaller
        _ = PyInstaller.__version__
    except ImportError:
        problems.append(
            "PyInstaller is not installed. `uv sync --group build`, or "
            "`uv pip install pyinstaller`.")

    if problems:
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        raise SystemExit(1)


def build(include_model: bool, clean: bool) -> Path:
    env = dict(os.environ)
    env["PIT_BUILD_ROOT"] = str(ROOT)
    env["PIT_BUILD_MODEL"] = "1" if include_model else "0"

    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC),
           "--distpath", str(ROOT / "dist"),
           "--workpath", str(ROOT / "build"),
           "--noconfirm"]
    if clean:
        cmd.append("--clean")

    print(f"→ {' '.join(cmd[2:])}")
    started = time.time()
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    print(f"  built in {time.time() - started:.0f}s")
    return ROOT / "dist" / APP_NAME


def make_zip(folder: Path) -> Path:
    """
    Zip the folder for handover.

    Stored, not deflated, for the CAD model: a .glb is already compressed, and
    spending four minutes of CPU to save two percent on a 400 MB archive is not
    a trade worth making.
    """
    target = folder.parent / f"{folder.name}.zip"
    if target.exists():
        target.unlink()
    print(f"→ zipping to {target.name}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            arc = Path(folder.name) / path.relative_to(folder)
            method = (zipfile.ZIP_STORED if path.suffix.lower() == ".glb"
                      else zipfile.ZIP_DEFLATED)
            z.write(path, arc, compress_type=method)
    return target


def find_iscc() -> str | None:
    """The Inno Setup compiler, from PATH or where it installs itself."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    for candidate in _ISCC_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def numeric_version(version: str) -> str:
    """
    `1.4.2-beta.1` → `1.4.2.0`.

    Windows' version resource is four integers and cannot hold a prerelease
    suffix. The real string still goes in AppVersion, which is the one a person
    ever reads; this is only what Explorer's Details tab shows.
    """
    core = version.lstrip("vV").split("+", 1)[0].split("-", 1)[0]
    parts = [p for p in core.split(".") if p.isdigit()][:4]
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts)


def make_installer(folder: Path) -> Path:
    """
    Compile the one-file Windows installer around an already-built folder.

    Not a second build: Inno wraps exactly the `dist/` tree PyInstaller just
    produced and self-checked, so what a person installs is byte-for-byte what
    CI proved boots.
    """
    if sys.platform != "win32":
        raise SystemExit("  x --installer only works on Windows — Inno Setup is "
                         "a Windows compiler. Push a tag and let CI do it.")
    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "  x Inno Setup is not installed. `choco install innosetup -y`, or "
            "get it from https://jrsoftware.org/isdl.php")

    from app import version as appver
    v = appver.VERSION

    cmd = [iscc,
           f"/DAppVersion={v}",
           f"/DNumericVersion={numeric_version(v)}",
           f"/DSourceDir={folder}",
           str(ISS)]
    print(f"→ {Path(iscc).name} {' '.join(cmd[1:-1])}")
    started = time.time()
    subprocess.run(cmd, cwd=ROOT, check=True)
    print(f"  compiled in {time.time() - started:.0f}s")

    out = ROOT / "dist" / f"Breakaway-Pit-Display-{v}-Setup.exe"
    if not out.exists():
        raise SystemExit(f"  x expected {out.name} — Inno produced nothing")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-model", action="store_true",
                    help="leave the season CAD model out (~340 MB smaller)")
    ap.add_argument("--zip", action="store_true",
                    help="also produce a zip beside the folder (the update payload)")
    ap.add_argument("--installer", action="store_true",
                    help="Windows: also compile the one-file Setup.exe")
    ap.add_argument("--clean", action="store_true",
                    help="delete build/ and dist/ first")
    args = ap.parse_args()

    include_model = not args.no_model
    print(f"Building {APP_NAME} for {sys.platform} "
          f"({'with' if include_model else 'without'} the CAD model)")

    preflight(include_model, args.installer)

    if args.clean:
        for d in (ROOT / "build", ROOT / "dist"):
            if d.exists():
                print(f"  removing {d.relative_to(ROOT)}/")
                shutil.rmtree(d)

    folder = build(include_model, args.clean)
    if not folder.exists():
        print(f"  ✗ expected {folder} — build produced nothing", file=sys.stderr)
        return 1

    print(f"\n{folder}")
    print(f"  {human(tree_size(folder))} across "
          f"{sum(1 for _ in folder.rglob('*') if _.is_file())} files")

    mac_app = ROOT / "dist" / f"{APP_NAME}.app"
    if mac_app.exists():
        print(f"{mac_app}\n  {human(tree_size(mac_app))}")

    if args.zip:
        z = make_zip(folder)
        print(f"{z}\n  {human(z.stat().st_size)}")

    if args.installer:
        setup = make_installer(folder)
        print(f"{setup}\n  {human(setup.stat().st_size)}")

    print("\nThe music library is NOT bundled — point the app at the audio "
          "folder from Control → Music → Add folder on the target machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
