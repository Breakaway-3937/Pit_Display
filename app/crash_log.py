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
- **A freeze is recorded too** (`start_watchdog`, 2026-10-03). A batch import
  "crashed" on the pit machine and the log ended with no stack at all: the
  app had stopped responding and was closed, which no hook sees. A timer on
  the GUI thread checks in every second; when it misses 20 s, a background
  thread writes every thread's stack here (once per freeze) and a line when
  it recovers, so a hang names the exact line it's stuck on.

**Installed first thing in `main.py`, before any other `app` import**, so an
import-time failure is caught too. It uses only the standard library and
`app.paths`, and never raises: a crash log that can crash is worse than none.
The file is trimmed when it passes 256 KB.
"""

from __future__ import annotations

import faulthandler
import sys
import threading
import time
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
    # Idempotent. `main.py` runs as `__main__`, so anything that later does
    # `from main import …` (the self-check does) executes its top again.
    if _file is not None:
        return
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


def note(text: str) -> None:
    """One timestamped line in the log. Never raises."""
    try:
        if _file is not None:
            _file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
            _file.flush()
    except Exception:
        pass


_last_beat = time.monotonic()
_beat_timer = None


def _beat() -> None:
    global _last_beat
    _last_beat = time.monotonic()


def start_watchdog(stall_s: float = 20.0) -> None:
    """Record GUI-thread freezes (see the module note). Call once, after the
    QApplication exists, on the GUI thread."""
    global _beat_timer
    if _beat_timer is not None or _file is None:
        return
    from PyQt6.QtCore import QTimer
    _beat_timer = QTimer()
    _beat_timer.setInterval(1000)
    _beat_timer.timeout.connect(_beat)
    _beat_timer.start()
    _beat()

    def watch() -> None:
        stalled_since = None
        while True:
            time.sleep(2.0)
            idle = time.monotonic() - _last_beat
            if idle > stall_s and stalled_since is None:
                stalled_since = _last_beat
                note(f"FROZEN: the GUI thread has not responded for {idle:.0f}s. "
                     f"Every thread's stack follows (the main thread is the frozen one):")
                try:
                    faulthandler.dump_traceback(file=_file, all_threads=True)
                    _file.flush()
                except Exception:
                    pass
            elif idle < 3 and stalled_since is not None:
                note(f"responsive again after {time.monotonic() - stalled_since:.0f}s")
                stalled_since = None

    threading.Thread(target=watch, name="gui-watchdog", daemon=True).start()


def tail(lines: int = 40) -> str:
    """The end of the log, for `--self-check` to print. Never raises."""
    try:
        text = path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
