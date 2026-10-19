"""
The diagnostics service: what this machine tells home about itself.

    session    a launch is a session; one that never ended (killed, a native
               crash, the power cord) is reported on the next launch
    harvest    crash.log, update_check.log and selfcheck.log → events (minutely)
    stalls     the event loop's worst stall (a 500 ms timer that measures
               itself), for the `responsive` check
    checks     the quick suite every `check_minutes`; a check that changes
               state is an event, and every result is in the status
    status     a snapshot every `status_minutes` (`pit_status`, one hub row
               per machine; home keeps every version)
    report     "Report a problem": the operator's words with every log tail

Everything goes through the spool (`spool.py`), and the sync engine carries
it home (`pit_event`, `pit_status`; DATABASE.md "Sync"). **Off means nothing
leaves this machine**: events are still kept and shown here. On by default
for the dev Mac and the beta line (`settings.py`). `PIT_DIAG_QUIET=1` (set by
`--self-check`) starts no timers and records nothing.

Never holds a window: the control screen hands over `managed_window`, as it
does to the webcast.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from collections import deque

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.diagnostics import checks, harvest, settings, spool
from app.lazy_proxy import LazyProxy

_LAG_TICK_MS = 500
_LAG_WINDOW_S = 600
_HARVEST_MS = 60_000
_FIRST_CHECK_MS = 30_000
_FIRST_STATUS_MS = 45_000


class _DiagnosticsService(QObject):
    changed = pyqtSignal()                  # events, status or results moved
    suite_progress = pyqtSignal(str)        # "Checking install.audio…"
    suite_done = pyqtSignal(object)         # list[checks.Check]

    def __init__(self):
        super().__init__()
        self._quiet = os.environ.get("PIT_DIAG_QUIET") == "1"
        self._window_provider = None
        self._boot: list[tuple[str, float]] = []
        self._last_quick: list[checks.Check] = []
        self._last_quick_at = 0.0
        self._last_full: list[checks.Check] = []
        self._last_full_at = 0.0
        self._prev_status: dict[str, str] = {}
        self._suite: list | None = None
        self._lags: deque = deque()
        self._lag_mark = time.monotonic()

        self._lag_timer = QTimer(self)
        self._lag_timer.setInterval(_LAG_TICK_MS)
        self._lag_timer.timeout.connect(self._on_lag_tick)
        self._harvest_timer = QTimer(self)
        self._harvest_timer.setInterval(_HARVEST_MS)
        self._harvest_timer.timeout.connect(self._on_harvest)
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self.run_quick)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self.write_status)

    # ── state ─────────────────────────────────────────────────────────────

    @property
    def quiet(self) -> bool:
        return self._quiet

    @property
    def reporting(self) -> bool:
        """Will what's recorded here leave this machine?"""
        return settings.enabled() and not self._quiet

    def sync_ready(self) -> bool:
        try:
            from app.db.sync.service import sync
            return sync.configured and bool(sync.prefs["enabled"])
        except Exception:
            return False

    def describe(self) -> str:
        if self._quiet:
            return "Quiet (self-check)."
        if not settings.enabled():
            return "Off: reports are kept on this machine and never sent."
        if not self.sync_ready():
            return ("On, but this machine has no team sync: reports wait here "
                    "until sync is set up.")
        return "On: reports go home with team sync."

    @property
    def last_quick(self) -> list[checks.Check]:
        return list(self._last_quick)

    @property
    def last_full(self) -> list[checks.Check]:
        return list(self._last_full)

    @property
    def last_full_at(self) -> float:
        return self._last_full_at

    @property
    def running_suite(self) -> bool:
        return self._suite is not None

    def pending(self) -> int:
        return len(spool.pending())

    def recent(self, limit: int = 30) -> list[dict]:
        return spool.recent(limit)

    def set_window_provider(self, provider) -> None:
        self._window_provider = provider

    def set_enabled(self, on: bool | None) -> None:
        settings.save(enabled=on)
        self.changed.emit()

    # ── lifetime ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """After the services it reads exist (main._boot)."""
        if self._quiet:
            return
        checks.context["started"] = spool.current_session().get("started_ts", time.time())
        for eid in harvest.all_files(startup=True):
            self._count(eid)
        self._record_launch()
        prefs = settings.load()
        self._lag_mark = time.monotonic()
        self._lag_timer.start()
        self._harvest_timer.start()
        self._check_timer.start(prefs["check_minutes"] * 60_000)
        self._status_timer.start(prefs["status_minutes"] * 60_000)
        QTimer.singleShot(_FIRST_CHECK_MS, self.run_quick)
        QTimer.singleShot(_FIRST_STATUS_MS, self.write_status)
        self.changed.emit()

    def boot_finished(self, steps: list[tuple[str, float]]) -> None:
        """main._boot's step timings, for the status and the launch event."""
        self._boot = list(steps)

    def shutdown(self) -> None:
        if self._quiet:
            spool.end_session()     # main() began one; a quiet close is still clean
            return
        for t in (self._lag_timer, self._harvest_timer, self._check_timer, self._status_timer):
            t.stop()
        up = time.time() - checks.context["started"]
        spool.record("exit", f"Closed normally after {_duration(up)}", severity="info",
                     signature="exit", data={"uptime_s": round(up)})
        self.write_status(final=True)
        spool.end_session()
        from app.update import guard
        guard.clean_exit()
        if self.reporting:
            try:
                from app.db.sync.service import sync
                sync.farewell()
            except Exception:
                pass

    def _record_launch(self) -> None:
        from app import version
        st = spool.state()
        before = st.get("last_version")
        spool.save_state(last_version=version.VERSION)
        summary = f"Started {version.VERSION}"
        if before and before != version.VERSION:
            summary += f" (updated from {before})"
        spool.record("launch", summary, severity="info", signature="launch",
                     data={"previous_version": before, "platform": _platform(),
                           "frozen": _frozen()})

    # ── observers ─────────────────────────────────────────────────────────

    def _on_lag_tick(self) -> None:
        now = time.monotonic()
        lag = max(0.0, now - self._lag_mark - _LAG_TICK_MS / 1000)
        self._lag_mark = now
        self._lags.append((now, lag))
        while self._lags and now - self._lags[0][0] > _LAG_WINDOW_S:
            self._lags.popleft()
        checks.context["max_lag_s"] = max((v for _, v in self._lags), default=0.0)
        checks.context["lag_window_s"] = (now - self._lags[0][0]) if self._lags else 0.0

    def _on_harvest(self) -> None:
        spool.beat()
        from app.update import guard
        guard.note_uptime(time.time() - checks.context["started"])
        new = harvest.all_files()
        for eid in new:
            self._count(eid)
        if new:
            self.changed.emit()

    def _count(self, eid: str) -> None:
        ev = spool.load(eid) or {}
        if ev.get("data", {}).get("previous_session"):
            return
        if ev.get("kind") == "crash":
            checks.context["crashes"] += 1
        elif ev.get("kind") == "freeze":
            checks.context["freezes"] += 1

    # ── checks ────────────────────────────────────────────────────────────

    def run_quick(self) -> list[checks.Check]:
        results = checks.run_quick()
        self._last_quick, self._last_quick_at = results, time.time()
        if not self._quiet:
            self._transitions(results)
        self.changed.emit()
        return results

    def _transitions(self, results: list[checks.Check]) -> None:
        """A check that got worse, or got better, is an event; steady state isn't."""
        for r in results:
            before = self._prev_status.get(r.name)
            self._prev_status[r.name] = r.status
            bad = r.status in (checks.FAIL, checks.WARN)
            if bad and before != r.status:
                spool.record(
                    "check_failed", f"{r.name}: {r.status.upper()} — {r.detail.splitlines()[0][:220]}",
                    severity="error" if r.status == checks.FAIL else "warn",
                    signature=f"check {r.name} {r.status}", detail=r.detail,
                    data={"check": r.as_dict(), "was": before})
            elif before in (checks.FAIL, checks.WARN) and r.status == checks.PASS:
                spool.record("check_recovered", f"{r.name}: back to PASS (was {before})",
                             severity="info", signature=f"check {r.name} recovered",
                             data={"check": r.as_dict()})

    def run_full(self) -> None:
        """Every check, one per event-loop turn so the panel keeps painting."""
        if self._suite is not None:
            return
        self._suite = [list(checks.FULL), []]
        self.changed.emit()
        QTimer.singleShot(0, self._suite_step)

    def _suite_step(self) -> None:
        todo, done = self._suite
        if todo:
            name, fn = todo.pop(0)
            self.suite_progress.emit(f"Checking {name}…")
            done.append(checks.run_one(name, fn, "install" if name.startswith("install.") else "live"))
            QTimer.singleShot(0, self._suite_step)
            return
        self._suite = None
        self._last_full, self._last_full_at = done, time.time()
        if not self._quiet:
            failing = [r for r in done if r.status == checks.FAIL]
            summary = checks.summarize(done)
            spool.record(
                "check_suite",
                f"Full check suite: {summary['counts']['fail']} fail, "
                f"{summary['counts']['warn']} warn, {summary['counts']['pass']} pass"
                + (f" — failing: {', '.join(r.name for r in failing)}" if failing else ""),
                severity="error" if failing else "info", signature="check_suite",
                detail=_results_text(done), data={"results": [r.as_dict() for r in done]})
            self.write_status()
        self.suite_done.emit(done)
        self.changed.emit()

    # ── status ────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        from app import version
        from app.diagnostics.checks import process_memory
        rss, peak = process_memory()
        st = spool.state()
        snap = {
            "schema": spool.SCHEMA,
            "at": spool.now_iso(),
            "build": {"version": version.VERSION, "channel": version.CHANNEL,
                      "commit": version.COMMIT[:12], "built": version.BUILT,
                      "frozen": _frozen(), "update_channel": _update_channel()},
            "platform": _platform(),
            "session": {**{k: v for k, v in spool.current_session().items()
                           if k in ("id", "started", "pid")},
                        "uptime_s": round(time.time() - checks.context["started"]),
                        "boot_s": round(sum(t for _, t in self._boot), 2),
                        "boot_steps": [[n, round(t, 2)] for n, t in self._boot]},
            "process": {"rss_mb": round(rss / 1e6), "peak_mb": round(peak / 1e6),
                        "threads": _threads(), "cpu_s": round(time.process_time())},
            "app": self._app_state(),
            "services": self._services(),
            "counts": {"crashes": checks.context["crashes"],
                       "freezes": checks.context["freezes"],
                       "max_lag_s": round(checks.context["max_lag_s"], 2),
                       "events_pending": self.pending(),
                       "events_dropped": int(st.get("dropped", 0)),
                       "events_throttled": int(st.get("throttled", 0))},
            "reporting": self.reporting,
        }
        if self._last_quick:
            snap["checks"] = {"at": _iso(self._last_quick_at),
                              **checks.summarize(self._last_quick),
                              "results": [_short(r) for r in self._last_quick]}
        if self._last_full:
            snap["full_checks"] = {"at": _iso(self._last_full_at),
                                   **checks.summarize(self._last_full),
                                   "failing": [_short(r) for r in self._last_full
                                               if r.status in (checks.FAIL, checks.WARN)]}
        return snap

    def write_status(self, final: bool = False) -> None:
        if self._quiet:
            return
        try:
            snap = self.snapshot()
            snap["final"] = final
            spool.write_status(snap)
        except Exception as e:
            spool.record("diagnostics_error", f"Couldn't build the status: {type(e).__name__}: {e}",
                         severity="warn", signature=f"status {type(e).__name__}")
        self.changed.emit()

    def _app_state(self) -> dict:
        out: dict = {}
        try:
            from app.config import config
            out["mode"] = config.mode
            out["team"] = config.active_team.number
            # Config stores content only once someone picks it; unset is the
            # screen's default, which a reader took for a blank screen
            # (beta.9 sent `"content": null` for screens that were showing).
            live = {}
            published: set[str] = set()
            try:
                from app.webcast import webcast
                published = set(webcast.published())
                for row in webcast.telemetry().get("live", []):
                    live[row["screen"]] = live.get(row["screen"], 0) + 1
            except Exception:
                pass
            screens = {}
            for sid, default in (("presentation_a", "rotation"), ("presentation_b", "rotation"),
                                 ("project", "cad")):
                win = self._window_provider(sid) if self._window_provider else None
                row = {"on": win is not None,
                       "content": config.get(sid, "content", default) or default}
                if win is not None:
                    row["where"] = "network" if sid in published else "monitor"
                    if sid in published:
                        row["displays"] = live.get(sid, 0)
                screens[sid] = row
            out["screens"] = screens
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    def _services(self) -> dict:
        out: dict = {}

        def grab(name, fn):
            try:
                out[name] = fn()
            except Exception as e:
                out[name] = {"error": f"{type(e).__name__}: {e}"[:200]}

        def _sync():
            from app.db.sync.service import sync
            last = sync.last
            return {"state": sync.state(), "last_at": _iso(sync.last_at) if sync.last_at else None,
                    "outbox": last.outbox if last else None,
                    "waiting": last.waiting if last else None,
                    "errors": (last.errors[:3] if last else []),
                    "recent": [line for _, line in sync.history[:5]]}

        def _leds():
            from app.leds import leds
            t = leds.telemetry() if leds.connected else {}
            return {"connected": leds.connected, "status": leds.status,
                    "rtt_p95_ms": t.get("rtt_p95_ms"), "unanswered": t.get("unanswered"),
                    "reconnects": t.get("reconnects"), "reboots": t.get("reboots")}

        def _nexus():
            from app.nexus import nexus
            return {"state": nexus.state, "relay": nexus.relay.state,
                    "age_s": nexus.age_s(), "source": nexus.status_source}

        def _webcast():
            from app.webcast import webcast
            return {"listening": webcast.listening, "viewers": webcast.viewers,
                    "published": webcast.published()}

        def _analysis():
            from app.ai.service import analysis
            return {"state": analysis.describe(), "queued": analysis.queued}

        def _update():
            from app.update import update
            return {"state": update.state, "message": update.message[:200]}

        for name, fn in (("sync", _sync), ("leds", _leds), ("nexus", _nexus),
                         ("webcast", _webcast), ("analysis", _analysis), ("update", _update)):
            grab(name, fn)
        return out

    # ── sending ───────────────────────────────────────────────────────────

    def send_now(self) -> None:
        """Write the status and run a sync cycle, so it all goes now."""
        self.write_status()
        try:
            from app.db.sync.service import sync
            sync.sync_now()
        except Exception:
            pass

    def report_problem(self, note: str) -> str | None:
        """The operator saw something: their words, the checks, every log tail."""
        from app import crash_log, paths
        results = checks.run_quick()
        self._last_quick, self._last_quick_at = results, time.time()
        note = (note or "").strip() or "(no description)"
        files = {
            "crash.log": spool.tail_file(crash_log.path(), 96 * 1024),
            "qt_warnings.log": spool.tail_file(paths.data("qt_warnings.log"), 64 * 1024),
            "selfcheck.log": spool.tail_file(paths.data("selfcheck.log"), 48 * 1024),
            "update_check.log": spool.tail_file(paths.data("update_check.log"), 32 * 1024),
        }
        for name in ("sync.json", "update.json", "nexus.json", "webcast.json",
                     "diagnostics.json", "ai.json"):
            files[name] = spool.tail_file(paths.data(name), 16 * 1024)
        try:
            files["status.json"] = json.dumps(self.snapshot(), indent=1, default=str)
        except Exception as e:
            files["status.json"] = f"couldn't build: {type(e).__name__}: {e}"
        eid = spool.record(
            "report", note.splitlines()[0][:280], severity="warn",
            signature=f"report {spool.now_iso()}",
            detail=f"{note}\n\n── checks ──\n{_results_text(results)}",
            data={"results": [r.as_dict() for r in results]}, attachments=files)
        self.send_now()
        self.changed.emit()
        return eid


def _duration(s: float) -> str:
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


def _results_text(results: list[checks.Check]) -> str:
    return "\n".join(f"[{r.status.upper():4}] {r.name} ({r.ms} ms): {r.detail}" for r in results)


def _short(r: checks.Check) -> dict:
    return {"name": r.name, "status": r.status, "detail": r.detail[:300], "ms": r.ms}


def _iso(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _threads() -> int:
    import threading
    return threading.active_count()


def _frozen() -> bool:
    try:
        from app import paths
        return paths.is_frozen()
    except Exception:
        return False


def _update_channel() -> str:
    try:
        from app.update import settings as update_settings
        return update_settings.load().get("channel", "")
    except Exception:
        return ""


def _platform() -> dict:
    out = {"os": platform.system(), "release": platform.release(),
           "version": platform.version()[:120], "arch": platform.machine(),
           "python": sys.version.split()[0], "cpus": os.cpu_count(),
           "host": platform.node()[:80]}
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        out["qt"], out["pyqt"] = QT_VERSION_STR, PYQT_VERSION_STR
    except Exception:
        pass
    try:
        import shutil
        from app import paths
        du = shutil.disk_usage(paths.data_root())
        out["disk_free_gb"], out["disk_total_gb"] = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    except Exception:
        pass
    return out


diagnostics: _DiagnosticsService = LazyProxy("diagnostics", "init_diagnostics")  # type: ignore[assignment]


def init_diagnostics() -> _DiagnosticsService:
    """After sync (it triggers a cycle) and before the control screen (its
    Diagnostics panel subscribes). `start()` once the services exist."""
    real = _DiagnosticsService()
    diagnostics._install(real)
    return real
