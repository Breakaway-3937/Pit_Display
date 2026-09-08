"""
Make stdout able to carry the text this app actually prints.

**Windows Python does not default to UTF-8.** When output is redirected — a CI
log, a pipe, a file — `sys.stdout` is opened with the locale encoding, which on
an English Windows machine is **cp1252**. Every arrow, em-dash, ellipsis and
`✗` in this codebase's own output is then unencodable, and printing one raises
`UnicodeEncodeError` and kills the process:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192'

It killed the very first Windows CI build, in `build_app.py`'s "here is the
command I am running" line — a *progress message*, which is a maddening thing
to have a build die on. The text is not the bug; the encoding assumption is, and
there are dozens of those characters across the build scripts and the
self-check report, so fixing the one line that happened to fire first would
just move the failure a few lines down.

Call `use_utf8()` first thing in anything that prints. `errors="replace"` means
even a console that cannot *render* a character gets a `?` rather than an
exception — output is diagnostic, and diagnostics must never be the thing that
fails.

Frozen windowed builds have no stdout at all (`console=False`), so every step
here is guarded rather than assumed.
"""

from __future__ import annotations

import sys


def use_utf8() -> None:
    """UTF-8, line-buffered, never raising. Safe to call more than once."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue                  # windowed build: nothing to reconfigure
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue                  # already wrapped by something of ours
        try:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (OSError, ValueError, AttributeError):
            pass
