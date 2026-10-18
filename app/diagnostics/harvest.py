"""
Turn what `crash.log` and the other log files recorded into events.

`app/crash_log.py` stays the single writer of crashes (its hook, faulthandler
and the freeze watchdog), because it's installed before anything else and
must stay standard-library-only. This reads what it wrote since last time
(`state.json` `crash_offset`) and spools one event per incident:

    UNHANDLED <Type> + traceback        → crash   (error)
    Fatal Python error … (faulthandler) → fatal   (a native crash; the app died)
    Qt FATAL: …                         → fatal
    FROZEN: … + every thread's stack    → freeze  (error)
    responsive again after Ns           → info, the freeze's end

Run at startup (the previous session's crash, which nothing else could
report) and every minute (a crash the app survived). The first run on a
machine sends only the last week: a crash just before the update that brought
diagnostics is the point, the log's older history isn't. The log is trimmed past
256 KB; a file shorter than the saved offset is read from the start, and
incidents already sent are known by their hash, so nothing is sent twice.

Also, once each: an `update_check.log` the updater wrote since last time (a
staged build that failed its self-check) and a `selfcheck.log` from a
`--self-check` run against this machine's data that failed.
"""

from __future__ import annotations

import hashlib
import re

from app.diagnostics import spool

_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})  (.*)$")
_TRACE_LINE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
_SEEN_KEPT = 300
_FIRST_LOOK_DAYS = 7    # a machine's first harvest: recent history only


def _crash_log_path():
    from app import crash_log
    return crash_log.path()


def _blocks(text: str) -> list[tuple[str, str, str]]:
    """[(stamp, first line, continuation)]. Lines before any stamp attach to ''."""
    out: list[list] = []
    for line in text.splitlines():
        m = _STAMP.match(line)
        if m:
            out.append([m.group(1), m.group(2), []])
        elif out:
            out[-1][2].append(line)
        else:
            out.append(["", "", [line]])
    return [(s, head, "\n".join(rest)) for s, head, rest in out]


def signature(trace: str, fallback: str) -> str:
    """The same fault on every machine: the exception line plus the deepest
    frame inside the app's own code (paths cut to `app/…`)."""
    frames = _TRACE_LINE.findall(trace or "")
    own = [f for f in frames if "/app/" in f[0].replace("\\", "/") or f[0].endswith("main.py")]
    where = own[-1] if own else (frames[-1] if frames else None)
    last = next((ln.strip() for ln in reversed((trace or "").splitlines())
                 if ln.strip() and not ln.startswith(" ")), fallback)
    if where:
        path = where[0].replace("\\", "/")
        path = path[path.rfind("/app/") + 1:] if "/app/" in path else path.rsplit("/", 1)[-1]
        return f"{last[:120]} @ {path}:{where[1]} {where[2]}"
    return last[:200]


def _iso(stamp: str) -> str | None:
    if not stamp:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").astimezone().isoformat(
            timespec="seconds")
    except ValueError:
        return None


def _older(stamp: str, oldest) -> bool:
    try:
        from datetime import datetime
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S") < oldest
    except ValueError:
        return False


def _context(limit: int = 24 * 1024) -> dict[str, str]:
    """The log tails that go with a crash."""
    from app import paths
    return {"qt_warnings.log": spool.tail_file(paths.data("qt_warnings.log"), limit)}


def crash_log(*, startup: bool = False) -> list[str]:
    """Spool every new incident in crash.log. Returns the event ids."""
    try:
        path = _crash_log_path()
        size = path.stat().st_size
    except OSError:
        return []
    st = spool.state()
    first_look = "crash_offset" not in st
    offset = int(st.get("crash_offset", 0) or 0)
    if size < offset:
        offset = 0          # trimmed: read again, the seen-hashes stop repeats
    if size == offset:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    # Only whole lines: the writer may be mid-line (faulthandler mid-dump).
    cut = text.rfind("\n")
    if cut < 0:
        return []
    consumed = len(text[:cut + 1].encode("utf-8"))
    text = text[:cut + 1]

    seen = list(st.get("seen", []))
    ids: list[str] = []

    from datetime import datetime, timedelta
    oldest = datetime.now() - timedelta(days=_FIRST_LOOK_DAYS)

    def emit(kind, severity, summary, detail, stamp, sig):
        digest = hashlib.sha1(f"{stamp}|{kind}|{detail[:4000]}".encode()).hexdigest()[:16]
        if digest in seen:
            return
        seen.append(digest)
        if first_look and _older(stamp, oldest):
            return          # weeks-old history the first time diagnostics runs
        eid = spool.record(kind, summary, severity=severity, detail=detail,
                           at=_iso(stamp), signature=sig,
                           data={"from": "crash.log", "previous_session": startup},
                           attachments=_context())
        if eid:
            ids.append(eid)

    for stamp, head, rest in _blocks(text):
        if head.startswith("UNHANDLED"):
            exc = head.partition(" ")[2].strip() or "exception"
            last = next((ln for ln in reversed(rest.splitlines()) if ln.strip()), exc)
            last = last.strip()
            name = exc.split()[0]
            summary = (f"Unhandled {last[:220]}" if last.startswith(name)
                       else f"Unhandled {exc}: {last[:200]}")
            if " in thread " in exc and " in thread " not in summary:
                summary += f" (thread {exc.partition(' in thread ')[2]})"
            emit("crash", "error", summary,
                 rest, stamp, signature(rest, exc))
        elif head.startswith("Qt FATAL"):
            emit("fatal", "fatal", head[:300], rest, stamp, head[:200])
        elif head.startswith("FROZEN"):
            emit("freeze", "error", "The app stopped responding (GUI thread frozen)",
                 head + "\n" + rest, stamp, "freeze @ " + _frozen_where(rest))
        elif head.startswith("responsive again"):
            emit("freeze_end", "info", f"Responsive again: {head}", "", stamp, "freeze_end")
        if "Fatal Python error" in rest:
            start = rest.index("Fatal Python error")
            dump = rest[start:]
            first = dump.splitlines()[0]
            emit("fatal", "fatal", f"Native crash: {first[:200]}", dump, stamp,
                 f"{first[:100]} @ {_frozen_where(dump)}")

    spool.save_state(crash_offset=offset + consumed, seen=seen[-_SEEN_KEPT:])
    return ids


def _frozen_where(dump: str) -> str:
    """The innermost app frame of the main thread in a faulthandler dump."""
    main = dump
    for block in re.split(r"\n(?=(?:Current thread|Thread) 0x)", dump):
        if "<module>" in block and ("main.py" in block):
            main = block
            break
    for line in main.splitlines():
        m = re.search(r'File "([^"]+)", line (\d+) in (\S+)', line)
        if m:
            path = m.group(1).replace("\\", "/")
            if "/app/" in path:
                return f"{path[path.rfind('/app/') + 1:]}:{m.group(2)} {m.group(3)}"
    m = re.search(r'File "([^"]+)", line (\d+) in (\S+)', main)
    return f"{m.group(1).rsplit('/', 1)[-1]}:{m.group(2)} {m.group(3)}" if m else "unknown"


def _once_per_file(key: str, path, make) -> str | None:
    """Spool `make(text)` when `path` changed since the last time it was seen."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    st = spool.state()
    if float(st.get(key, 0) or 0) >= mtime:
        return None
    spool.save_state(**{key: mtime})
    if not st.get(key):
        return None         # first run on this machine: old news, not an event
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return make(text)


def update_check() -> str | None:
    from app import paths

    def make(text):
        failed = [ln.strip() for ln in text.splitlines() if ln.startswith("[FAIL]")]
        return spool.record(
            "update_check_failed",
            "A downloaded build failed its self-check: "
            + (", ".join(f.removeprefix("[FAIL] ") for f in failed) or text.splitlines()[0][:120]),
            severity="error", detail=text, signature="update_check " + ",".join(failed),
            data={"failed": failed})
    return _once_per_file("update_check_mtime", paths.data("update_check.log"), make)


def selfcheck() -> str | None:
    from app import paths

    def make(text):
        failed = [ln.strip() for ln in text.splitlines() if ln.startswith("[FAIL]")]
        if not failed:
            return None
        return spool.record(
            "selfcheck_failed",
            "--self-check failed: " + ", ".join(f.removeprefix("[FAIL] ") for f in failed),
            severity="error", detail=text, signature="selfcheck " + ",".join(failed),
            data={"failed": failed})
    return _once_per_file("selfcheck_mtime", paths.data("selfcheck.log"), make)


def all_files(*, startup: bool = False) -> list[str]:
    ids = crash_log(startup=startup)
    for fn in (update_check, selfcheck):
        try:
            eid = fn()
        except Exception:
            eid = None
        if eid:
            ids.append(eid)
    return ids
