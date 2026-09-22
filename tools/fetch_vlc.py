#!/usr/bin/env python3
"""
Put the Windows libVLC runtime in `vlc/`, so the packaged app has one.

    uv run tools/fetch_vlc.py           # fetch if `vlc/` is not already there
    uv run tools/fetch_vlc.py --force   # fetch again over the top

**Why this exists.** Windows ships no libVLC. Before this, a fresh pit machine
had the music player and the *entire* equaliser dead until somebody separately
downloaded VLC and happened to pick the 64-bit build — and nothing says so
until an operator presses play and gets silence. That is one more install step
on a machine that is supposed to need none, and it is the step most likely to
be skipped, because the app boots perfectly without it.

macOS and Linux are not built here: both resolve `libvlc` through the system,
and the Mac this is developed on already has VLC. `packaging/pit_display.spec`
only reaches for `vlc/` when it is building on Windows, and treats it as
optional — a build on a machine that never ran this still succeeds and simply
has no audio, which is exactly the behaviour that existed before.

**What is taken out of the official zip**, and nothing else:

    libvlc.dll  libvlccore.dll  plugins/  + the licence files

`plugins/` is the part that is easy to leave behind and produces the most
confusing failure: `libvlc.dll` loads, `Instance()` returns an object, and
every `play()` is silent, because libVLC with no plugin directory has no audio
output module and no codecs. It is not an error — there is simply nothing to
decode with and nowhere to send the result. `app/music/engine.py` points
python-vlc at both halves with `PYTHON_VLC_LIB_PATH` and
`PYTHON_VLC_MODULE_PATH` before it imports `vlc`.

**Licensing.** These are unmodified upstream VideoLAN binaries. libvlc and
libvlccore are LGPLv2.1+; a number of the plugins are GPLv2+. Both require
that the licence text travel with the binaries and that the corresponding
source be obtainable, so `COPYING*` is copied out of the zip alongside them
and `vlc/SOURCE.txt` records the exact version and the URL it came from.
Do not strip those two out to save a few kilobytes.

The download is checked against VideoLAN's own published SHA-256 rather than a
hash pinned in here, so a version bump is one constant and no hand-copied
digest that somebody will eventually forget to update.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.console import use_utf8  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vlc"

# Bump this one constant to take a new VLC. 3.x is the long-lived branch and
# the one python-vlc is built against; 4.x is not released and its API differs.
VERSION = "3.0.23"
BASE = f"https://download.videolan.org/pub/videolan/vlc/{VERSION}/win64"
ARCHIVE = f"vlc-{VERSION}-win64.zip"

# Everything the app needs, as paths *inside* the zip's `vlc-<version>/` folder.
WANTED_FILES = ("libvlc.dll", "libvlccore.dll")
WANTED_TREES = ("plugins",)
# LGPL and GPL both require the licence to travel with the binary.
LICENCE_PREFIXES = ("COPYING", "AUTHORS", "THANKS", "NEWS")


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:      # noqa: S310
        return r.read()


def _expected_sha256() -> str:
    """
    VideoLAN's published digest for this archive.

    Read from their `.sha256` rather than pinned here: a hash typed into this
    file is one more thing to remember on a version bump, and a stale one fails
    in a way that looks exactly like a corrupted download.
    """
    text = _get(f"{BASE}/{ARCHIVE}.sha256").decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == ARCHIVE:
            return parts[0].lower()
    raise SystemExit(f"fetch_vlc: {ARCHIVE} is not named in its own .sha256 file")


def _download(into: Path) -> Path:
    url = f"{BASE}/{ARCHIVE}"
    print(f"Downloading {url}")
    expected = _expected_sha256()
    blob = _get(url)
    got = hashlib.sha256(blob).hexdigest()
    if got != expected:
        # The archive is about to become executable code inside the app that
        # runs the pit. "It downloaded" is not a check.
        raise SystemExit(f"fetch_vlc: SHA-256 mismatch\n  expected {expected}\n"
                         f"  got      {got}")
    print(f"  {len(blob):,} bytes, sha256 verified")
    path = into / ARCHIVE
    path.write_bytes(blob)
    return path


def _extract(archive: Path, dest: Path) -> tuple[int, int]:
    """Pull the wanted members out into `dest`. Returns (files, bytes)."""
    prefix = f"vlc-{VERSION}/"
    files = total = 0
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if name.endswith("/") or not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            head = rel.split("/", 1)[0]
            keep = (rel in WANTED_FILES
                    or head in WANTED_TREES
                    or any(head.startswith(p) for p in LICENCE_PREFIXES))
            if not keep:
                continue
            out = dest / rel
            # A zip is untrusted input even when VideoLAN made it.
            if not out.resolve().is_relative_to(dest.resolve()):
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            data = z.read(name)
            out.write_bytes(data)
            files += 1
            total += len(data)
    return files, total


def main() -> int:
    use_utf8()
    ap = argparse.ArgumentParser(description="Fetch the Windows libVLC runtime.")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even if vlc/ already looks complete")
    args = ap.parse_args()

    if (DEST / "libvlc.dll").exists() and (DEST / "plugins").is_dir() and not args.force:
        print(f"vlc/ already has a runtime — {DEST}\nUse --force to replace it.")
        return 0

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="pit_vlc_") as tmp:
        archive = _download(Path(tmp))
        files, total = _extract(archive, DEST)

    if not (DEST / "libvlc.dll").exists():
        raise SystemExit("fetch_vlc: libvlc.dll was not in the archive")
    plugins = len(list((DEST / "plugins").rglob("*.dll"))) if (DEST / "plugins").is_dir() else 0
    if plugins == 0:
        # Silent playback, not an error, is what this would cause. Fail here.
        raise SystemExit("fetch_vlc: no plugins were extracted — libVLC would "
                         "load and then play nothing at all")

    (DEST / "SOURCE.txt").write_text(
        f"VLC {VERSION} for Windows x86-64, unmodified upstream binaries.\n"
        f"Downloaded from {BASE}/{ARCHIVE}\n"
        f"Source for these binaries: https://www.videolan.org/vlc/download-sources.html\n"
        f"libvlc/libvlccore are LGPLv2.1+; several plugins are GPLv2+.\n"
        f"See the COPYING files beside this one.\n",
        encoding="utf-8")

    print(f"Extracted {files:,} files ({total / 1e6:.0f} MB), {plugins} plugins → {DEST}")
    print("packaging/pit_display.spec will now include it in a Windows build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
