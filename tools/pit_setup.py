#!/usr/bin/env python3
"""
Make, inspect and apply a pit setup file — the one file that carries the keys
and the event to a pit machine. See `app/provision.py`.

On the Mac, with the keys already in `secrets/` and the event set:

    uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json
    uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json --event 2026casf
    uv run python tools/pit_setup.py make ~/Desktop/pit-setup.json --no-update-token

Look at one without printing a key:

    uv run python tools/pit_setup.py show ~/Desktop/pit-setup.json

Apply one to *this* machine (the installed app does the same with
`"Breakaway Pit Display" --provision <file>`, or on its own if the file is
`pit-setup.json` in the data directory when it starts):

    uv run python tools/pit_setup.py apply ~/Desktop/pit-setup.json

Then, on the pit machine, any one of:

  - copy it to  %LOCALAPPDATA%\\Breakaway Pit Display\\pit-setup.json  and start the app
  - Control → Event Feed → (admin) Import setup file…
  - "Breakaway Pit Display" --provision D:\\pit-setup.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import provision                  # noqa: E402
from app.console import use_utf8           # noqa: E402


def main(argv: list[str]) -> int:
    use_utf8()
    flags = {a for a in argv if a.startswith("--") and "=" not in a}
    opts = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    args = [a for a in argv if not a.startswith("--")]
    # `--event 2026casf` as two words, for the muscle memory that types it so
    i = argv.index("--event") if "--event" in argv else -1
    if i >= 0 and i + 1 < len(argv):
        opts["event"] = argv[i + 1]
        args = [a for a in args if a != argv[i + 1]]
        flags.discard("--event")

    if len(args) < 2 or args[0] not in ("make", "show", "apply"):
        print(__doc__)
        return 2
    cmd, path = args[0], Path(args[1]).expanduser()

    try:
        if cmd == "make":
            setup = provision.current(
                include_update_token="--no-update-token" not in flags,
                include_nexus="--no-nexus" not in flags)
            if "event" in opts:
                setup.nexus["event_key"] = opts["event"].strip()
            if setup.empty:
                print("Nothing to put in the file: no keys in secrets/, no update "
                      "token, no event. Put them there first (see secrets/README.md).")
                return 1
            provision.write(setup, path)
            print(f"Wrote {path}")
            for line in provision.describe(setup):
                print(f"  {line}")
            print("\nCopy it to the pit machine's data directory as pit-setup.json,\n"
                  "or import it from Control → Event Feed. Keep it off the repo.")
            return 0
        setup = provision.load(path)
        if cmd == "show":
            for line in provision.describe(setup):
                print(f"  {line}")
            return 0
        for line in provision.apply(setup):
            print(f"  {line}")
        return 0
    except provision.ProvisionError as e:
        print(f"✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
