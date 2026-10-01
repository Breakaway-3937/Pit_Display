"""
The sync service: runs `Engine.cycle()` on a worker thread, on a timer, soon
after a local change, and on "Sync now".

**Off until a token is present.** A machine nobody has set up (no
`secrets/sync_token`) runs no timer and opens no connection, and neither does
`--self-check` (`PIT_SYNC_QUIET=1`). Nothing on an audience screen depends on
sync; it only changes what the next read finds.

**After a cycle, the screens re-read.** The engine writes from its own
connection, so the services that cache a view of those tables are told on the
GUI thread (`_refresh`): checklists, EQ presets, CAN names and logs, the admin
password, the Nexus event, judges slides, the CAD model.
"""

from __future__ import annotations

import os
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app import credentials
from app.db.sync import settings as sync_settings
from app.db.sync.client import HubClient
from app.db.sync.engine import Engine, Report
from app.lazy_proxy import LazyProxy

# How often the outbox is glanced at for a local change (one COUNT query).
_NOTICE_MS = 5_000
_FIRST_CYCLE_MS = 8_000


class _Cycle(QThread):
    done = pyqtSignal(object)      # Report
    progress = pyqtSignal(str)

    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self._engine = engine

    def run(self):
        self._engine._say = self.progress.emit
        try:
            rep = self._engine.cycle()
        except Exception as e:  # the engine catches its own; this is the last net
            rep = Report(errors=[f"{type(e).__name__}: {e}"])
        self.done.emit(rep)


class _SyncService(QObject):
    state_changed = pyqtSignal()           # anything the panel shows moved
    progress = pyqtSignal(str)             # "Uploading qm14.wpilog…"
    applied = pyqtSignal(object)           # set[str] of what landed
    boards_changed = pyqtSignal()          # analysis boards from home

    def __init__(self, db_path):
        super().__init__()
        self._db_path = db_path
        self._quiet = os.environ.get("PIT_SYNC_QUIET") == "1"
        self._engine: Engine | None = None
        self._engine_key: tuple = ()
        self._worker: _Cycle | None = None
        self._again = False
        self._last: Report | None = None
        self._last_at = 0.0
        self._activity = ""
        self._history: list[tuple[float, str]] = []

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sync_now)
        self._notice = QTimer(self)
        self._notice.setInterval(_NOTICE_MS)
        self._notice.timeout.connect(self._notice_local_change)
        self._reconfigure()

    # ── state ─────────────────────────────────────────────────────────────

    @property
    def prefs(self) -> dict:
        return sync_settings.load()

    @property
    def configured(self) -> bool:
        return credentials.present(sync_settings.TOKEN_NAME)

    @property
    def enabled(self) -> bool:
        return self.configured and bool(self.prefs["enabled"]) and not self._quiet

    @property
    def busy(self) -> bool:
        return self._worker is not None

    @property
    def last(self) -> Report | None:
        return self._last

    @property
    def last_at(self) -> float:
        return self._last_at

    @property
    def activity(self) -> str:
        return self._activity

    @property
    def history(self) -> list[tuple[float, str]]:
        """Recent conflicts and errors, newest first: (time, line)."""
        return list(self._history)

    def state(self) -> str:
        if self._quiet:
            return "quiet"
        if not self.configured:
            return "unconfigured"
        if not self.prefs["enabled"]:
            return "off"
        if self.busy:
            return "syncing"
        if self._last is None:
            return "waiting"
        return "ok" if self._last.ok else "error"

    # ── control ───────────────────────────────────────────────────────────

    def set_enabled(self, on: bool) -> None:
        sync_settings.save(enabled=bool(on))
        self._reconfigure()

    def set_token(self, value: str) -> None:
        credentials.write(sync_settings.TOKEN_NAME, value)
        self._reconfigure()

    def set_prefs(self, **changes) -> None:
        sync_settings.save(**changes)
        self._reconfigure()

    def set_machine_id(self, new: str) -> None:
        """Raises ValueError for an id the hub would refuse. The engine is
        rebuilt on the next cycle (its key includes the id)."""
        sync_settings.set_machine_id(new)
        self._reconfigure()

    def _reconfigure(self) -> None:
        self._timer.stop()
        self._notice.stop()
        if self.enabled:
            self._timer.start(self.prefs["interval_s"] * 1000)
            self._notice.start()
            QTimer.singleShot(_FIRST_CYCLE_MS, self.sync_now)
        self.state_changed.emit()

    def _engine_for(self, prefs: dict) -> Engine:
        """One engine across cycles (it caches file hashes), rebuilt if the hub moved."""
        from app import version
        token = credentials.read(sync_settings.TOKEN_NAME)
        key = (prefs["url"], token, prefs["machine_id"], prefs["machine_name"])
        if self._engine is None or key != self._engine_key:
            client = HubClient(prefs["url"], token, prefs["machine_id"],
                               prefs["machine_name"], version.VERSION)
            self._engine = Engine(self._db_path, client, prefs["machine_id"], prefs)
            self._engine_key = key
        self._engine.prefs = prefs
        return self._engine

    def sync_now(self) -> None:
        if not self.enabled:
            return
        if self.busy:
            self._again = True
            return
        engine = self._engine_for(self.prefs)
        self._worker = _Cycle(engine, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._activity = "Syncing…"
        self.state_changed.emit()
        self._worker.start()

    def _notice_local_change(self) -> None:
        """A local edit waiting to go: don't make it wait for the minute timer."""
        if self.busy:
            return
        from app.db import db
        try:
            waiting = db.fetchone("SELECT COUNT(*) FROM sync_outbox")[0]
        except Exception:
            return
        # Only a *new* change: an entry that keeps failing stays in the outbox
        # and must not turn this five-second glance into a five-second loop.
        if waiting > (self._last.outbox if self._last is not None else 0):
            self.sync_now()

    def _on_progress(self, text: str) -> None:
        self._activity = text
        self.progress.emit(text)

    def _on_done(self, rep: Report) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait()
            worker.deleteLater()
        self._last, self._last_at = rep, time.time()
        self._activity = ""
        now = time.time()
        for line in rep.conflicts + rep.errors:
            self._history.insert(0, (now, line))
        del self._history[20:]
        sync_settings.save(
            last_sync=time.strftime("%Y-%m-%d %H:%M:%S"),
            last_result="ok" if rep.ok else rep.errors[0][:200])
        if rep.applied:
            self._refresh(rep.applied)
            self.applied.emit(set(rep.applied))
        self.state_changed.emit()
        progressed = rep.ok and (rep.pulled or rep.downloaded_bytes)
        if self._again or (rep.waiting and progressed):
            # A change arrived mid-cycle, or downloads are landing: go again
            # soon. A download that failed waits for the timer instead.
            self._again = False
            QTimer.singleShot(1000, self.sync_now)

    # ── after a pull: tell whoever caches a view ──────────────────────────

    def _refresh(self, applied: set[str]) -> None:
        def attempt(fn):
            try:
                fn()
            except Exception:
                pass    # a service that isn't up (a test) just doesn't hear

        if applied & {"checklist", "checklist_item"}:
            from app.checklist import checklist
            attempt(checklist.lists_changed.emit)
            for lst in checklist.lists():
                attempt(lambda i=lst.id: checklist.items_changed.emit(i))
        if applied & {"eq_presets"}:
            from app.music import music
            attempt(music.eq_changed.emit)
        if applied & {"playlists", "playlist_items"}:
            from app.music import music
            attempt(music.library_changed.emit)
        if applied & {"device", "log_session"}:
            from app.config import config
            attempt(config.notify_logs_changed)
        if "admin_credential" in applied:
            from app.admin import admin
            attempt(admin.password_changed.emit)
        if "setting:nexus" in applied:
            attempt(self._reapply_nexus)
        if "file:judges_slides" in applied:
            from app.judges_slides import judges_slides
            attempt(judges_slides.reload)
        if "file:cad" in applied:
            from app.cad_assets import cad_assets
            attempt(cad_assets.model_changed.emit)
            attempt(cad_assets.config_changed.emit)
        if "analysis_board" in applied:
            self.boards_changed.emit()
        if applied & {"analysis_run", "analysis_feedback", "analysis_board"}:
            from app.ai.service import analysis
            attempt(analysis.runs_changed.emit)

    @staticmethod
    def _reapply_nexus() -> None:
        """nexus.json changed under the running feed: bring the feed in line."""
        from app.nexus import nexus
        from app.nexus import settings as nexus_settings
        prefs = nexus_settings.load()
        nexus.set_relay(enabled=prefs["relay_enabled"], url=prefs["relay_url"])
        nexus.set_poll_interval(prefs["poll_interval_s"])
        nexus.set_auto_poll(prefs["auto_poll"])
        nexus.set_event_key(prefs["event_key"])


sync: _SyncService = LazyProxy("sync", "init_sync")  # type: ignore[assignment]


def init_sync() -> _SyncService:
    """After init_db() and the services it refreshes (see main._boot)."""
    from app.db import db
    real = _SyncService(db.path)
    sync._install(real)
    return real
