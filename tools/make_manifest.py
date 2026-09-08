#!/usr/bin/env python3
"""
Write the `manifest.json` that goes on a GitHub release.

    uv run tools/make_manifest.py --version 1.4.2 --tag v1.4.2 \
        --asset windows=dist/Breakaway-Pit-Display-1.4.2-windows.zip \
        --asset macos=dist/Breakaway-Pit-Display-1.4.2-macos.zip \
        --out manifest.json

**The manifest is what makes a release installable, and its SHA-256 is the only
thing between a 400 MB download and the pit machine's install folder.** The
updater refuses a release that has no manifest asset rather than trusting an
asset that merely has the right name — see `app/update/release.py`.

Run by CI after both platform builds have been made *and* self-checked, so a
release can never exist for a build that did not boot on its own runner.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

_CHUNK = 1 << 20


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--channel", default="", help="default: from the version")
    ap.add_argument("--notes", default="")
    ap.add_argument("--asset", action="append", default=[],
                    metavar="PLATFORM=PATH",
                    help="windows=… / macos=… / linux=…; repeatable")
    ap.add_argument("--out", default="manifest.json")
    args = ap.parse_args()

    version = args.version.lstrip("vV")
    channel = args.channel or ("beta" if "-" in version else "stable")

    platforms: dict[str, dict] = {}
    for entry in args.asset:
        key, _, raw = entry.partition("=")
        path = Path(raw)
        if not path.is_file():
            raise SystemExit(f"  x {path} does not exist")
        platforms[key] = {
            # The *asset* name as GitHub will publish it — the updater looks
            # the asset up by this name on the release, not by a URL.
            "asset": path.name,
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        print(f"  {key:8} {path.name}  {path.stat().st_size / 1048576:.0f} MB  "
              f"{platforms[key]['sha256'][:12]}…")

    if not platforms:
        raise SystemExit("  x no assets given — a manifest with no build is useless")

    manifest = {
        "version": version,
        "tag": args.tag,
        "channel": channel,
        "notes": args.notes,
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n",
                              encoding="utf-8")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
