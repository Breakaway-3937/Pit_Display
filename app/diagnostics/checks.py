"""
The in-app check suite: is this running machine healthy, right now?

`--self-check` answers "does this *install* work" by booting everything
offscreen. This answers "is the *running* app working" from inside it,
without rebuilding anything, and it runs on its own:

* **quick** (every few minutes, `service.py`): the live state of every
  service and of the machine. Fast: no network, no processes.
* **full** (Control → Pit Systems → Diagnostics → Run all checks): quick,
  plus the self-check's packaging checks that are safe in a running app
  (not `paths`/`database`/`windows`/`cad`/`webengine`, which would
  re-initialise or rebuild what's live).

A check returns `(status, detail)`; status is `pass`, `warn`, `fail` or
`skip` (not applicable here: no controller plugged in, no token). A check
that raises is a `fail` carrying its traceback, so a broken check is itself
reported. Results go into the status snapshot; a quick check that changes
state is an event (`service.py`).
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
RANK = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    ms: int = 0
    group: str = "live"

    def as_dict(self) -> dict:
        return asdict(self)


# Filled by the service: facts only a long-lived observer knows.
context = {"max_lag_s": 0.0, "lag_window_s": 0.0, "crashes": 0, "freezes": 0,
           "leds_seen": False, "started": time.time()}


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


# ── process and machine ──────────────────────────────────────────────────

def process_memory() -> tuple[int, int]:
    """(resident bytes now, peak resident bytes). (0, 0) when unknown."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            proc = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(proc, ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize), int(pmc.PeakWorkingSetSize)
            return 0, 0
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = peak if sys.platform == "darwin" else peak * 1024
        now = 0
        try:
            with open("/proc/self/statm") as f:
                now = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            now = peak if sys.platform != "darwin" else _mac_rss()
        return int(now), int(peak)
    except Exception:
        return 0, 0


def _mac_rss() -> int:
    try:
        import ctypes
        import ctypes.util

        class TaskBasicInfo(ctypes.Structure):
            _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                        ("resident_size_max", ctypes.c_uint64), ("user_time", ctypes.c_uint64),
                        ("system_time", ctypes.c_uint64), ("policy", ctypes.c_int),
                        ("suspend_count", ctypes.c_int)]
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        info = TaskBasicInfo()
        count = ctypes.c_uint(ctypes.sizeof(info) // 4)
        task = libc.mach_task_self()
        # MACH_TASK_BASIC_INFO = 20
        if libc.task_info(task, 20, ctypes.byref(info), ctypes.byref(count)) == 0:
            return int(info.resident_size)
    except Exception:
        pass
    return 0


def check_process():
    rss, peak = process_memory()
    threads = threading.active_count()
    up = time.time() - context["started"]
    detail = (f"memory {_gb(rss)} (peak {_gb(peak)}), {threads} Python threads, "
              f"up {up / 3600:.1f} h, cpu time {time.process_time():.0f} s")
    # 16 GB pit machines also hold the model (6.5 GB) while it runs.
    if rss > 3e9:
        return WARN, detail + " — unusually high; a leak would show as this climbing"
    if threads > 150:
        return WARN, detail + " — unusually many threads"
    return PASS, detail


def check_disk():
    from app import paths
    usage = shutil.disk_usage(paths.data_root())
    detail = f"{_gb(usage.free)} free of {_gb(usage.total)} at {paths.data_root()}"
    if usage.free < 1e9:
        return FAIL, detail + " — log imports and sync will fail"
    if usage.free < 5e9:
        return WARN, detail + " — a batch import needs several GB"
    return PASS, detail


def check_data_dir():
    from app import paths
    probe = paths.data("diagnostics", ".writetest")
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return PASS, f"writable: {paths.data_root()}"


def check_responsive():
    lag, window = context["max_lag_s"], context["lag_window_s"]
    freezes = context["freezes"]
    detail = (f"worst event-loop stall {lag:.2f} s over the last {window / 60:.0f} min; "
              f"{freezes} freeze{'s' if freezes != 1 else ''} (20 s+) this session")
    if freezes or lag > 10:
        return FAIL, detail
    if lag > 1.0:
        return WARN, detail + " — the screens stutter at this"
    return PASS, detail


def check_crashes():
    n = context["crashes"]
    if n:
        return WARN, f"{n} unhandled exception{'s' if n != 1 else ''} this session (sent as events)"
    return PASS, "none this session"


# ── storage ──────────────────────────────────────────────────────────────

def check_database():
    from app.db import db
    t = time.monotonic()
    ok = db.fetchone("PRAGMA main.quick_check(1)")[0]
    version = db.fetchone("PRAGMA user_version")[0]
    guard = db.fetchone("SELECT applying FROM sync_guard WHERE id = 1")
    db.fetchone("SELECT 1 FROM samples.sqlite_master LIMIT 1")
    size = os.path.getsize(db.path)
    detail = (f"schema v{version}, {size / 1e6:.0f} MB, quick_check {ok} "
              f"in {(time.monotonic() - t) * 1000:.0f} ms")
    if ok != "ok":
        return FAIL, detail
    if guard is None or guard[0] != 0:
        return FAIL, detail + "; sync guard stuck on (local edits aren't being recorded)"
    return PASS, detail


# ── services ─────────────────────────────────────────────────────────────

def check_sync():
    from app.db.sync.service import sync
    st = sync.state()
    if st in ("quiet", "unconfigured"):
        return SKIP, "no sync token on this machine" if st == "unconfigured" else "quiet"
    if st == "off":
        return WARN, "turned off on this machine: nothing reaches the team or home"
    last = sync.last
    age = time.time() - sync.last_at if sync.last_at else None
    detail = f"{st}"
    if last is not None:
        detail += (f", last cycle {age:.0f} s ago in {last.seconds:.1f} s, outbox {last.outbox}, "
                   f"waiting {last.waiting}, pending {last.pending}")
    if st == "error" and last is not None:
        return WARN, detail + f" — {last.errors[0][:200]}"
    if last is not None and last.outbox > 500:
        return WARN, detail + " — a large outbox isn't draining"
    return PASS, detail


def check_leds():
    from app.leds import leds
    if leds.connected:
        context["leds_seen"] = True
        t = leds.telemetry()
        p95 = t.get("rtt_p95_ms")
        extra = f", round trip p95 {p95:.0f} ms" if p95 is not None else ""
        given_up = t.get("unanswered") or 0
        detail = f"connected: {leds.status}{extra}"
        if given_up:
            return WARN, detail + f", {given_up} commands never acknowledged"
        return PASS, detail
    if context["leds_seen"]:
        return FAIL, f"the controller was connected this session and is gone: {leds.status}"
    return SKIP, f"no controller attached ({leds.status})"


def check_nexus():
    from app.nexus import nexus
    from app.nexus import settings as nexus_settings
    prefs = nexus_settings.load()
    if not prefs["event_key"]:
        return SKIP, "no event selected"
    relay = nexus.relay
    detail = f"{nexus.state}, event {prefs['event_key']}, relay {relay.state}"
    age = nexus.age_s()
    if age is not None:
        detail += f", data {age:.0f} s old"
    if prefs["relay_enabled"] and not relay.connected:
        return WARN, detail + " — the relay socket is down (falling back to polling)"
    if nexus.state == "error":
        return WARN, detail + f" — {nexus.message[:200]}"
    return PASS, detail


def check_webcast():
    from app.webcast import settings as wset
    from app.webcast import webcast
    prefs = wset.load()
    if not prefs["enabled"] or not prefs["screens"]:
        return SKIP, "no screens published to the pit network"
    detail = f"{len(prefs['screens'])} screen(s) published, {webcast.viewers} viewer(s)"
    if not webcast.listening:
        return FAIL, detail + " — the server isn't listening; the network screens are dark"
    return PASS, detail


def check_analysis():
    from app.ai.service import analysis
    return PASS, f"{analysis.describe()}, {analysis.queued} queued"


def check_updates():
    from app.update import update
    st = update.state
    if st == "error":
        return WARN, f"the updater's last attempt failed: {update.message}"[:300]
    return PASS, st


def check_qt():
    from app import qt_log
    seen = qt_log.summary()
    if not seen:
        return PASS, "no Qt warnings this run"
    return PASS, f"{len(seen)} distinct Qt warning(s) this run; most frequent: {seen[0][:200]}"


QUICK = [
    ("process", check_process), ("disk", check_disk), ("data_dir", check_data_dir),
    ("responsive", check_responsive), ("crashes", check_crashes),
    ("database", check_database), ("sync", check_sync), ("leds", check_leds),
    ("nexus", check_nexus), ("webcast", check_webcast), ("analysis", check_analysis),
    ("updates", check_updates), ("qt", check_qt),
]

# The self-check's checks that are safe inside the running app.
_SELFCHECK = ("fonts", "sponsors", "owlet", "workers", "audio", "updates", "network",
              "app_control", "nexus", "sync", "ai", "webcast", "diagnostics")


def _from_selfcheck(name: str):
    def run():
        from app import selfcheck
        r = getattr(selfcheck, f"_check_{name}")()
        detail = r.detail + ("".join(f"\n! {w}" for w in r.warnings))
        if not r.ok:
            return (FAIL if r.critical else WARN), detail
        if r.warnings or not r.critical:
            return WARN if r.warnings else PASS, detail
        return PASS, detail
    return run


FULL = QUICK + [(f"install.{n}", _from_selfcheck(n)) for n in _SELFCHECK]


def run_one(name: str, fn, group: str = "live") -> Check:
    t = time.monotonic()
    try:
        status, detail = fn()
    except Exception as e:
        # A service that isn't up (LazyProxy raises before its init) isn't broken.
        if type(e).__name__ == "RuntimeError" and "before" in str(e) and "init" in str(e):
            status, detail = SKIP, f"not started: {e}"
        else:
            status, detail = FAIL, f"the check itself failed: {type(e).__name__}: {e}\n" + \
                traceback.format_exc(limit=6)
    return Check(name, status, str(detail), int((time.monotonic() - t) * 1000), group)


def run_quick() -> list[Check]:
    return [run_one(n, fn) for n, fn in QUICK]


def summarize(results: list[Check]) -> dict:
    counts = {s: 0 for s in (PASS, WARN, FAIL, SKIP)}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    worst = min((r.status for r in results), key=lambda s: RANK[s], default=PASS)
    return {"worst": worst, "counts": counts}
