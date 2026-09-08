"""
`_UpdateService` — the singleton the control screen talks to.

**Checking is automatic; downloading never is.** A release is ~400 MB, and a
download that starts itself is a download that starts during a match cycle on
an event's wifi. So the timer only ever asks GitHub whether something newer
exists and says so quietly on the control screen; a person presses the button.

**Installing does not interrupt anything.** The download, the unpack and the
staged build's self-check all happen on a worker thread while the pit display
carries on, and the pointer swap at the end touches only a link — the running
process keeps its own folder. Nothing on any audience screen so much as
flickers. The new version is what the *next* launch gets, and "Restart now" is
an offer, not a consequence.

The state machine is deliberately flat, because the panel draws it directly:

    idle ─check─▶ checking ─▶ up_to_date
                          └─▶ available ─install─▶ downloading ─▶ staging
                                                   ─▶ verifying ─▶ ready
    (any) ─────────────────────────────────────────────────────▶ error
"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app import paths, version
from app.lazy_proxy import LazyProxy
from app.update import install, settings
from app.update.release import Release, UpdateError, configured, download, latest

# How long after startup the first automatic check runs. Long enough that it is
# never competing with four windows and a Chromium process for the machine.
_FIRST_CHECK_MS = 120_000


class _Worker(QThread):
    """One check, or one full install. Never both, never two at once."""

    progressed = pyqtSignal(str, float)
    found = pyqtSignal(object)          # Release | None
    staged = pyqtSignal(str, str)       # version, self-check summary
    failed = pyqtSignal(str)

    def __init__(self, job: str, channel: str, release: Release | None = None,
                 parent=None):
        super().__init__(parent)
        self._job = job
        self._channel = channel
        self._release = release

    def run(self):
        try:
            if self._job == "check":
                self.found.emit(latest(self._channel))
                return
            self._install(self._release)
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"Update failed: {type(exc).__name__}: {exc}")

    def _install(self, release: Release) -> None:
        zip_path = paths.data_dir("updates") / f"{release.version}.zip"
        download(release, zip_path, progress=self.progressed.emit)

        self.progressed.emit("Unpacking", 0.0)
        folder = install.stage(zip_path, release.version,
                               progress=self.progressed.emit)
        # The zip is a gigabyte of nothing once it is unpacked, and the pit
        # laptop's disk is the one that has to hold two versions of the app.
        zip_path.unlink(missing_ok=True)

        ok, detail = install.verify(folder, progress=self.progressed.emit)
        if not ok:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
            raise UpdateError(
                f"The update was downloaded but not installed, because "
                f"{detail}. This machine is still running {version.VERSION} and "
                "nothing has changed.")

        install.activate(release.version)
        self.staged.emit(release.version, detail)


class _UpdateService(QObject):

    # "idle" | "checking" | "up_to_date" | "available" | "working" | "ready" | "error"
    state_changed = pyqtSignal(str)
    progressed = pyqtSignal(str, float)
    message_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._state = "idle"
        self._message = ""
        self._release: Release | None = None
        self._worker: _Worker | None = None
        self._ready_version = ""

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_check)

        prefs = settings.load()
        if prefs["auto_check"] and self.supported:
            hours = max(1, int(prefs["check_interval_hours"]))
            self._timer.start(hours * 3_600_000)
            QTimer.singleShot(_FIRST_CHECK_MS, self._auto_check)

        # An update installed in a previous run left its predecessor on disk;
        # that folder is only deletable once the process holding it is gone,
        # which is now.
        if install.is_managed():
            install.prune()

    # ── What this machine can do ─────────────────────────────────────────

    @property
    def supported(self) -> bool:
        """
        Can this install update itself at all?

        Three ways it cannot, and they are different problems with different
        answers, so `blocked_reason` names which one it is.
        """
        return (version.is_release() and install.is_managed() and configured())

    @property
    def blocked_reason(self) -> str:
        if not paths.is_frozen():
            return ("Running from a checkout — updates apply to installed "
                    "builds only. Use git.")
        if not version.is_release():
            return ("This build was not stamped with a version, so it cannot "
                    "tell what is newer than it. Build it through CI.")
        if not install.is_managed():
            return ("This copy was unzipped by hand rather than installed, so "
                    "there is no versioned layout to update into. Run the "
                    "Setup.exe from the latest release to enable updates — "
                    "your database, checklists and CAN names are untouched.")
        if not configured():
            return ("No GitHub token on this machine. The repository is "
                    "private, so updates need one — paste it below.")
        return ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    @property
    def release(self) -> Release | None:
        return self._release

    @property
    def ready_version(self) -> str:
        return self._ready_version

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def channel(self) -> str:
        return str(settings.get("channel"))

    def set_channel(self, channel: str) -> None:
        settings.save(channel=channel)
        self._release = None
        self._set_state("idle", f"Following the {channel} channel.")

    def set_auto_check(self, on: bool) -> None:
        settings.save(auto_check=bool(on))
        if on and self.supported:
            hours = max(1, int(settings.get("check_interval_hours")))
            self._timer.start(hours * 3_600_000)
        else:
            self._timer.stop()

    # ── Actions ──────────────────────────────────────────────────────────

    def check(self) -> None:
        """Ask GitHub what the newest release on this channel is."""
        if self.busy:
            return
        if not self.supported:
            self._set_state("error", self.blocked_reason)
            return
        self._set_state("checking", "Checking for updates…")
        self._start(_Worker("check", self.channel(), parent=self))

    def install_available(self) -> None:
        """Download, unpack, self-check and activate the release we found."""
        if self.busy or self._release is None:
            return
        self._set_state("working", f"Downloading {self._release.version}…")
        self._start(_Worker("install", self.channel(), self._release, parent=self))

    def rollback(self) -> str:
        target = install.rollback()
        self._ready_version = target
        self._set_state("ready", f"Rolled back to {target}. "
                                 "It starts the next time the app opens.")
        return target

    def restart(self) -> bool:
        """Relaunch through the link, so the new version is what comes up."""
        return install.relaunch()

    # ── Plumbing ─────────────────────────────────────────────────────────

    def _start(self, worker: _Worker) -> None:
        self._worker = worker
        worker.progressed.connect(self.progressed.emit)
        worker.found.connect(self._on_found)
        worker.staged.connect(self._on_staged)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _auto_check(self) -> None:
        # Never interrupt an install that is already running, and never start
        # one automatically — this only ever looks.
        if not self.busy and self.supported and self._state != "ready":
            self.check()

    def _on_found(self, release) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        if release is None or not version.is_newer(release.version):
            settings.save(last_check=stamp, last_result="up to date")
            self._release = None
            self._set_state("up_to_date",
                            f"Up to date — {version.VERSION} is the newest "
                            f"{self.channel()} release.")
            return
        self._release = release
        settings.save(last_check=stamp, last_result=f"found {release.version}")
        self._set_state("available",
                        f"{release.version} is available "
                        f"({release.size_mb:.0f} MB). You are on {version.VERSION}.")

    def _on_staged(self, new_version: str, _detail: str) -> None:
        self._ready_version = new_version
        self._release = None
        self._set_state("ready",
                        f"{new_version} is installed and passed its self-check "
                        "on this machine. It starts the next time the app opens.")

    def _on_failed(self, message: str) -> None:
        self._set_state("error", message)

    def _on_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self._message = message
        self.message_changed.emit(message)
        self.state_changed.emit(state)


update: _UpdateService = LazyProxy("update", "init_update")  # type: ignore[assignment]


def init_update() -> _UpdateService:
    """Call once in main(), after QApplication is created."""
    real = _UpdateService()
    update._install(real)
    return real
