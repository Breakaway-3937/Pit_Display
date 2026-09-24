"""
`crash.log` in the data directory: why the app died, when nothing else can say.

The shipped app is `console=False`, so a Python exception at startup — or in
a Qt slot, which PyQt turns into an abort — prints to a stderr nobody has, and
the only symptom is "it won't open". That happened with v0.1.7 on the first
Windows pit machine, and there was nothing to read. This module fixes that:

- `sys.excepthook` writes the full traceback, then hands on to the previous
  hook (PyInstaller's windowed build shows its own dialog from there).
- `faulthandler` writes the Python stack on a hard crash — a segfault in a
  Qt or VLC DLL — which no exception hook ever sees.
- Every launch writes one line naming the version, so the log also says which
  build was running when it died.

**Installed first thing in `main.py`, before any other `app` import**, so an
import-time failure is caught too. It uses only the standard library and
`app.paths`, and never raises: a crash log that can crash is worse than none.
The file is trimmed when it passes 256 KB.
"""

from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime

FILE_NAME = "crash.log"
_MAX_BYTES = 256 * 1024
_file = None     # held open for faulthandler, which writes to a raw fd


def path():
    from app import paths
    return paths.data(FILE_NAME)


def _version() -> str:
    try:
        from app import version
        return version.describe()
    except Exception:
        return "unknown version"


def install() -> None:
    global _file
    try:
        p = path()
        if p.exists() and p.stat().st_size > _MAX_BYTES:
            tail = p.read_bytes()[-_MAX_BYTES // 2:]
            p.write_bytes(b"[trimmed]\n" + tail)
        _file = open(p, "a", encoding="utf-8")
        _file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  launch  {_version()}  "
                    f"{' '.join(sys.argv[1:])}\n")
        _file.flush()
        faulthandler.enable(file=_file, all_threads=True)
    except Exception:
        _file = None
        return

    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            _file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  UNHANDLED "
                        f"{exc_type.__name__}\n")
            _file.write("".join(traceback.format_exception(exc_type, exc, tb)))
            _file.flush()
        except Exception:
            pass
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def tail(lines: int = 40) -> str:
    """The end of the log, for `--self-check` to print. Never raises."""
    try:
        text = path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
