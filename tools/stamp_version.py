#!/usr/bin/env python3
"""
Write the version into the build, from the tag that is producing it.

    uv run tools/stamp_version.py 1.4.2 --channel stable --commit $GITHUB_SHA

Rewrites the stamped block in `app/version.py` and the `version =` line in
`pyproject.toml`. CI runs it between checkout and build, so the string the pit
machine reports is the tag, with nobody having had to remember to bump a file.

Run with no version to print what is currently stamped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.console import use_utf8  # noqa: E402

use_utf8()   # Windows redirects stdout as cp1252; see app/console.py

VERSION_PY = ROOT / "app" / "version.py"
PYPROJECT = ROOT / "pyproject.toml"


def _sub(text: str, field: str, value: str) -> str:
    # `[^"]*`, not `.*` — greedy would swallow a trailing comment on the
    # line and put the value where the comment was.
    pattern = rf'^{field} = "[^"]*"$'
    replacement = f'{field} = "{value}"'
    new, n = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise SystemExit(f"  x {field} not found in {VERSION_PY.name}")
    return new


def stamp(version: str, channel: str, commit: str) -> None:
    version = version.lstrip("vV")
    built = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    text = VERSION_PY.read_text(encoding="utf-8")
    text = _sub(text, "VERSION", version)
    text = _sub(text, "CHANNEL", channel)
    text = _sub(text, "COMMIT", commit)
    text = _sub(text, "BUILT", built)
    VERSION_PY.write_text(text, encoding="utf-8")

    # pyproject's version is metadata, not what the app reports — but a
    # packaging file that says 0.1.0 forever is a lie somebody will trip on.
    proj = PYPROJECT.read_text(encoding="utf-8")
    proj, n = re.subn(r'^version = "[^"]*"$', f'version = "{version}"',
                      proj, count=1, flags=re.MULTILINE)
    if n == 1:
        PYPROJECT.write_text(proj, encoding="utf-8")

    print(f"stamped {version} ({channel}) {commit[:7]} {built}")


def channel_for(tag: str) -> str:
    """A tag with a prerelease part is the beta channel. `v1.4.2-rc.1` → beta."""
    return "beta" if "-" in tag.lstrip("vV") else "stable"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="e.g. 1.4.2 or v1.4.2-beta.1")
    ap.add_argument("--channel", help="stable | beta (default: from the version)")
    ap.add_argument("--commit", default="", help="full sha; default: git HEAD")
    args = ap.parse_args()

    if not args.version:
        sys.path.insert(0, str(ROOT))
        from app import version as v
        print(f"{v.VERSION}  channel={v.CHANNEL}  commit={v.COMMIT[:7]}  built={v.BUILT}")
        return 0

    commit = args.commit
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                    capture_output=True, text=True,
                                    check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = ""

    stamp(args.version, args.channel or channel_for(args.version), commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
