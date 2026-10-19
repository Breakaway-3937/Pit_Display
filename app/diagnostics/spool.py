"""
The diagnostics spool: every report this machine owes home, as JSON files.

    <data>/diagnostics/outbox/<id>.json    waiting for the sync engine
    <data>/diagnostics/sent/<id>.json      taken by the hub (the newest kept)
    <data>/diagnostics/status.json         the latest status snapshot
    <data>/diagnostics/session.json        exists while the app is running
    <data>/diagnostics/state.json          the harvester's place in crash.log

**Files, not the database**, for the reason `crash.log` is a file: the
reports that matter most come from the states where the database, sync or
Qt is the broken thing. Anything can write here (the crash harvest at
startup, a worker thread, the check suite) and it waits, on disk, for the
next sync cycle that works. The engine reads it (`app/db/sync/engine.py`
`_diagnostics`); nothing here touches the network.

Standard library and `app.paths` only, and **nothing in here raises**: a
reporter that can crash the app is worse than none. Every text is scrubbed
of this machine's credentials before it's written (`scrub()`).
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA = 1
# Severity, worst first. `fatal` is the app dying; `error` something broken
# that kept running; `warn` worth a look; `info` the timeline (launches,
# clean exits, a check recovering).
SEVERITIES = ("fatal", "error", "warn", "info")

# What one event may carry. The hub refuses a row over 256 KB; detail and
# attachments travel zstd'd, and text is cut to these before packing.
MAX_DETAIL = 64 * 1024
MAX_ATTACHMENT = 192 * 1024
PACK_AT = 4 * 1024
MAX_ROW = 200 * 1024
# The outbox never grows past these: a machine that can't sync for a season
# (or a loop that reports every second) keeps the newest, drops the oldest,
# and says how many it dropped in its next status.
MAX_PENDING = 400
MAX_SENT_KEPT = 200
# A runaway reporter can't spend the hub's daily write budget (sync-hub
# README "Budget"): per kind, per hour. The status snapshot is not an event.
MAX_PER_KIND_PER_HOUR = 12

_lock = threading.Lock()
_recent: dict[str, list[float]] = {}
_secrets: list[str] | None = None


def root() -> Path:
    from app import paths
    p = paths.data("diagnostics")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dir(name: str) -> Path:
    p = root() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ── scrubbing ────────────────────────────────────────────────────────────

_BEARER = re.compile(r"(?i)(bearer\s+|token[=:]\s*|authorization:\s*)[A-Za-z0-9._~+/=-]{12,}")


def _known_secrets() -> list[str]:
    """Every credential on this machine (secrets/ and PIT_SECRET_*), longest first."""
    global _secrets
    if _secrets is not None:
        return _secrets
    found: set[str] = set()
    try:
        from app import credentials
        folder = credentials.folder()
        if folder.is_dir():
            for f in folder.iterdir():
                if f.is_file():
                    v = credentials.read(f.name)
                    if len(v) >= 8:
                        found.add(v)
    except Exception:
        pass
    for k, v in os.environ.items():
        if k.startswith("PIT_SECRET_") and len(v) >= 8:
            found.add(v)
    try:
        from app.update.release import token
        t = token()
        if t and len(t) >= 8:
            found.add(t)
    except Exception:
        pass
    _secrets = sorted(found, key=len, reverse=True)
    return _secrets


def forget_secrets() -> None:
    """A token was just set or changed: re-read before the next scrub."""
    global _secrets
    _secrets = None


def scrub(text: str) -> str:
    if not text:
        return text
    try:
        for s in _known_secrets():
            text = text.replace(s, "‹secret›")
        return _BEARER.sub(lambda m: m.group(1) + "‹secret›", text)
    except Exception:
        return text


def clip(text: str, limit: int) -> str:
    """The *end* of `text` when it's too long: the end of a log is the news."""
    if not text or len(text) <= limit:
        return text or ""
    return f"[…{len(text) - limit:,} characters cut]\n" + text[-limit:]


def tail_file(path: Path, limit: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return (f"[…first {size - limit:,} bytes not included]\n" if size > limit else "") + text
    except OSError:
        return ""


# ── packing ──────────────────────────────────────────────────────────────

def pack(text: str):
    """Text as the wire carries it: plain when short, `{"zstd": b64}` when not
    (the same shape as an analysis transcript, so home decodes both alike)."""
    if not text or len(text) < PACK_AT:
        return text
    try:
        from compression import zstd
        return {"zstd": base64.b64encode(zstd.compress(text.encode("utf-8"), 19)).decode("ascii")}
    except Exception:
        return text


def unpack(value) -> str:
    if isinstance(value, dict) and "zstd" in value:
        from compression import zstd
        return zstd.decompress(base64.b64decode(value["zstd"])).decode("utf-8")
    return value or ""


# ── writing ──────────────────────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> bool:
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _throttled(kind: str) -> bool:
    t = time.time()
    hits = [h for h in _recent.get(kind, []) if t - h < 3600]
    if len(hits) >= MAX_PER_KIND_PER_HOUR:
        _recent[kind] = hits
        return True
    hits.append(t)
    _recent[kind] = hits
    return False


def record(kind: str, summary: str, *, severity: str = "error", detail: str = "",
           data: dict | None = None, attachments: dict[str, str] | None = None,
           at: str | None = None, signature: str | None = None) -> str | None:
    """
    Queue one event for home. Returns its id, or None when it wasn't kept
    (throttled, or the disk refused). Never raises.

    `kind` is a short slug (`crash`, `freeze`, `check_failed`, `report`…);
    `summary` one line a person reads in a list; `detail` the traceback or
    report; `attachments` log tails by file name; `data` small structured
    facts. `signature` groups the same fault across launches and machines
    (home counts by it); by default the summary.
    """
    try:
        if severity not in SEVERITIES:
            severity = "error"
        with _lock:
            if _throttled(kind):
                _bump_state("throttled")
                return None
        stamp = datetime.now()
        # Milliseconds lead the suffix, so ids sort in time order within a
        # second (the panel's newest-first list, the push order).
        eid = f"{stamp:%Y%m%d-%H%M%S}-{stamp.microsecond // 1000:03d}{uuid.uuid4().hex[:5]}"
        event = {
            "schema": SCHEMA, "id": eid, "kind": kind, "severity": severity,
            "summary": scrub(str(summary))[:300],
            "at": at or now_iso(),
            "signature": scrub(signature or str(summary))[:200],
            "session": current_session_id(),
            "detail": scrub(clip(detail, MAX_DETAIL)),
            "data": data or {},
            "attachments": {name: scrub(clip(text, MAX_ATTACHMENT))
                            for name, text in (attachments or {}).items() if text},
        }
        _fill_build(event)
        with _lock:
            ok = _write_json(_dir("outbox") / f"{eid}.json", event)
            _cap_outbox()
        return eid if ok else None
    except Exception:
        return None


def _fill_build(event: dict) -> None:
    try:
        from app import version
        event["build"] = {"version": version.VERSION, "channel": version.CHANNEL,
                          "commit": version.COMMIT[:12]}
    except Exception:
        event["build"] = {}


def _cap_outbox() -> None:
    files = sorted(_dir("outbox").glob("*.json"))
    extra = len(files) - MAX_PENDING
    for f in files[:max(0, extra)]:
        try:
            f.unlink()
            _bump_state("dropped")
        except OSError:
            pass


def wire(event: dict) -> dict:
    """The event as pushed: big texts packed, the whole row under the hub's cap."""
    out = dict(event)
    out["detail"] = pack(event.get("detail") or "")
    out["attachments"] = {k: pack(v) for k, v in (event.get("attachments") or {}).items()}
    # Still too big (a log of distinct lines barely compresses): drop the
    # largest attachments until it fits, and say which were dropped.
    while len(json.dumps(out)) > MAX_ROW and out["attachments"]:
        biggest = max(out["attachments"], key=lambda k: len(json.dumps(out["attachments"][k])))
        out["attachments"].pop(biggest)
        out.setdefault("dropped_attachments", []).append(biggest)
    if len(json.dumps(out)) > MAX_ROW:
        out["detail"] = pack(clip(event.get("detail") or "", 16 * 1024))
    return out


def pending() -> list[str]:
    """Ids waiting for the hub, oldest first."""
    try:
        return [f.stem for f in sorted(_dir("outbox").glob("*.json"))]
    except OSError:
        return []


def load(eid: str) -> dict | None:
    for where in ("outbox", "sent"):
        data = read_json(root() / where / f"{eid}.json")
        if data is not None:
            return data
    return None


def mark_sent(eid: str) -> None:
    try:
        src = _dir("outbox") / f"{eid}.json"
        if src.exists():
            os.replace(src, _dir("sent") / src.name)
        kept = sorted(_dir("sent").glob("*.json"))
        for f in kept[:max(0, len(kept) - MAX_SENT_KEPT)]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def recent(limit: int = 30) -> list[dict]:
    """Newest first, sent and waiting, each with `sent` set — for the panel."""
    out = []
    try:
        for where in ("outbox", "sent"):
            for f in _dir(where).glob("*.json"):
                out.append((f.stem, where == "sent", f))
    except OSError:
        return []
    out.sort(key=lambda t: t[0], reverse=True)
    events = []
    for eid, sent, f in out[:limit]:
        data = read_json(f)
        if data is not None:
            data["sent"] = sent
            events.append(data)
    return events


# ── status snapshot ──────────────────────────────────────────────────────

def write_status(snapshot: dict) -> None:
    snapshot = json.loads(scrub(json.dumps(snapshot, default=str)))
    with _lock:
        _write_json(root() / "status.json", snapshot)


def read_status() -> dict | None:
    return read_json(root() / "status.json")


# ── harvester / counters state ───────────────────────────────────────────

def state() -> dict:
    return read_json(root() / "state.json") or {}


def save_state(**changes) -> None:
    with _lock:
        s = state()
        s.update(changes)
        _write_json(root() / "state.json", s)


def _bump_state(key: str) -> None:
    try:
        s = read_json(root() / "state.json") or {}
        s[key] = int(s.get(key, 0)) + 1
        _write_json(root() / "state.json", s)
    except Exception:
        pass


# ── the running session ──────────────────────────────────────────────────

_session: dict | None = None
_previous: dict | None = None


def current_session_id() -> str:
    return (_session or {}).get("id", "")


def current_session() -> dict:
    return dict(_session or {})


def begin_session() -> dict | None:
    """
    Mark this launch as running. Returns the *previous* session's record if
    it never ended (killed, a native crash, the power cord), else None.
    """
    global _session, _previous
    if _session is not None:
        return _previous
    try:
        path = root() / "session.json"
        previous = _previous = read_json(path)
        from app import version
        _session = {"id": uuid.uuid4().hex[:12], "started": now_iso(),
                    "started_ts": time.time(), "pid": os.getpid(),
                    "version": version.VERSION, "beat": now_iso()}
        _write_json(path, _session)
        if previous is not None:
            # Recorded here, first thing in main(), not when the service
            # starts: a launch that dies during boot would otherwise lose
            # the previous one's report and overwrite its mark.
            _report_unclean(previous)
        return previous
    except Exception:
        return None


def _report_unclean(previous: dict) -> None:
    from app import crash_log
    started, beat_at = previous.get("started", "?"), previous.get("beat", "?")
    record(
        "unclean_exit",
        f"The previous session ({previous.get('version', '?')}, started {started}) "
        f"ended without closing: last alive {beat_at}",
        severity="fatal", signature=f"unclean_exit {previous.get('version', '')}",
        detail=("The app was killed, crashed natively, froze and was closed, failed "
                "during startup, or the machine lost power. crash.log's end follows.\n\n"
                + crash_log.tail(80)),
        data={"previous": previous},
        attachments={"crash.log": tail_file(crash_log.path(), 64 * 1024)})


def previous_unclean() -> dict | None:
    """What `begin_session()` found: the last launch's record if it never ended."""
    return _previous


def beat(**extra) -> None:
    """The session is still alive (written every minute): an unclean exit
    then says roughly when the app died, not just that it did."""
    if _session is None:
        return
    _session.update(beat=now_iso(), beat_ts=time.time(), **extra)
    _write_json(root() / "session.json", _session)


def end_session() -> None:
    try:
        (root() / "session.json").unlink(missing_ok=True)
    except OSError:
        pass
