"""
Qt's own warnings, captured to a file instead of a terminal nobody is watching.

The pit machine runs `console=False` — there is no console behind the screens —
so everything Qt writes to stderr goes nowhere at all. That is fine for the
noise and not fine for the handful of lines that matter, which are the object
lifetime complaints:

    QObject::disconnect: wildcard call disconnects from destroyed signal of
    QWebSocketDataProcessor::unnamed

Those name a *class* and nothing else: not which screen, not what the operator
had just done, not whether it happened once or four hundred times. An operator
who sees one has no way to tell anybody anything useful about it, and by the
time it is mentioned the moment is gone.

So they are written to `qt_warnings.log` in the data directory with a
timestamp and a running count, and repeats of the same message are collapsed
rather than filling the disk. **Asking for that file is the whole point** —
it turns "I saw some errors" into a time and a sequence.

Everything Qt prints still reaches stderr as well, so running from a checkout
is unchanged.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

_FILE = "qt_warnings.log"
_MAX_BYTES = 512 * 1024

# Messages worth keeping. Everything else Qt says during a normal run is noise
# — missing font families, platform plugin chatter — and burying the two lines
# that matter under it is the same as not logging at all.
_INTERESTING = (
    "QObject::",
    "QWebSocket",
    "QTcpSocket",
    "QNativeSocketEngine",
    "wrapped C/C++ object",
    "QThread",
    "QBackingStore",
    "requestActivate",
)

_seen: dict[str, int] = {}
_previous = None


def _path():
    from app import paths
    return paths.data(_FILE)


def _write(line: str) -> None:
    try:
        path = _path()
        try:
            if path.stat().st_size > _MAX_BYTES:
                path.write_bytes(path.read_bytes()[-_MAX_BYTES // 2:])
        except OSError:
            pass
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except OSError:
        pass          # a full or read-only disk must never take the pit down


def _handler(mode, context, message: str) -> None:
    if _previous is not None:
        try:
            _previous(mode, context, message)
        except Exception:
            pass
    else:
        try:
            sys.stderr.write(message + "\n")
        except (OSError, ValueError):
            pass

    if mode not in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg,
                    QtMsgType.QtFatalMsg):
        return
    if not any(token in message for token in _INTERESTING):
        return

    count = _seen.get(message, 0) + 1
    _seen[message] = count
    # The first, then powers of ten. A socket teardown that fires once is a
    # curiosity; the same line four hundred times is the actual report, and
    # neither should cost an operator a gigabyte of log.
    if count == 1 or count in (10, 100, 1000) or count % 5000 == 0:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        suffix = "" if count == 1 else f"   (seen {count}x)"
        _write(f"{stamp}  {message}{suffix}")


def install() -> None:
    """Start capturing. Call once, straight after QApplication exists."""
    global _previous
    _previous = qInstallMessageHandler(_handler)
    _write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — pit display started ===")


def summary() -> list[str]:
    """What has been seen this run — for `--self-check`."""
    return [f"{count}x  {message}"
            for message, count in sorted(_seen.items(),
                                         key=lambda kv: -kv[1])]
