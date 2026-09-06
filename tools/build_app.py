#!/usr/bin/env python3
"""
Build the Pit Display into a shippable app folder.

    uv run tools/build_app.py                 # everything, including the CAD model
    uv run tools/build_app.py --no-model      # ~340 MB smaller; upload one later
    uv run tools/build_app.py --zip           # also produce a zip to hand over
    uv run tools/build_app.py --clean         # throw away build/ and dist/ first

The result is `dist/Breakaway Pit Display/` — one folder, copy it anywhere on a
machine of the same OS and run the executable inside it. On macOS you also get
`dist/Breakaway Pit Display.app`.

**What is deliberately *not* in it: the music library.** The library is an index
of paths to the team's own audio, kept in the database; the audio files stay
wherever they live on the pit machine. Shipping a few gigabytes of music inside
an app nobody can update without a rebuild is the wrong shape, and the folder is
re-pointed in one click from Control → Music → Add folder.

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
APP_NAME = "Breakaway Pit Display"
SPEC = ROOT / "packaging" / "pit_display.spec"


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def tree_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def preflight(include_model: bool) -> None:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-model", action="store_true",
                    help="leave the season CAD model out (~340 MB smaller)")
    ap.add_argument("--zip", action="store_true",
                    help="also produce a zip beside the folder")
    ap.add_argument("--clean", action="store_true",
                    help="delete build/ and dist/ first")
    args = ap.parse_args()

    include_model = not args.no_model
    print(f"Building {APP_NAME} for {sys.platform} "
          f"({'with' if include_model else 'without'} the CAD model)")

    preflight(include_model)

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

    print("\nThe music library is NOT bundled — point the app at the audio "
          "folder from Control → Music → Add folder on the target machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
