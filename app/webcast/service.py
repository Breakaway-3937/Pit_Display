"""
`_WebcastService` — publishes the overhead screens to the pit LAN.

    ControlScreen ──builds headless──▶ PresentationScreenA/B   (state engine)
                                            │
    config / rotation / checklist  ──signals─┤
                                            ▼
    Pi on the switch ◀── ws:// JSON ── ScreenSocketServer
                     ◀── http ─────── WebcastServer (page, css, js, fonts)

**There is no renderer here any more, and that was the whole problem.** The
first build rasterised each screen to JPEG several times a second: ~15 ms of
full-chassis repaint plus encode per frame, two screens at a quarter of a core
forever, and a soft picture at the far end. It was redoing expensive work to
say that nothing had changed, because a slide holds for 45 seconds.

Now the browser draws and this sends state when state actually moves. Between
slides the socket is silent. The dwell rail is not sent at all — the payload
carries the dwell's deadline and the page animates against it, so a rail at 60
fps costs the pit machine nothing.

**The headless window stays, as the state engine.** It owns the rotation: it
advances its own `slide_index` and writes it back to `config`, and every other
face resolves from the same singletons. Keeping it means a monitor and a
browser cannot disagree, and it is the cheap half — a window nobody renders
costs ~1% of a core in its own timers. `ControlScreen` still owns its lifetime.

**What is pushed, and when.** Every subscription below is a signal this app
already emitted, and none fires faster than a person can act except
`rotation.advance`, which is once per 45 seconds:

| signal | what changed |
|---|---|
| `config.team_changed` | the brand on every surface |
| `config.mode_changed` | standard / judges / lunch |
| `config.screen_setting_changed` | theme, face, slide index, checklist id |
| `rotation.advance` | the slide moved, so the dwell clock restarted |
| `checklist.items_changed` / `item_toggled` | a row, or all of them |
| `judges_slides.slide_changed` / `slides_reloaded` | the deck |
| `config.logs_changed` | an import regenerated the boards and fun facts |

**Bound methods, never lambdas** — `CLAUDE.md` is emphatic and it applies
here: the singletons outlive this object, and a lambda left on
`config.team_changed` keeps firing into a deleted C++ object.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.config import config
from app.lazy_proxy import LazyProxy
from app.rotation import rotation
from app.webcast import settings
from app.webcast.server import WebcastServer
from app.webcast.sockets import ScreenSocketServer


class _WebcastService(QObject):

    # on/off, and the reason when it could not start
    state_changed = pyqtSignal(bool, str)
    # total viewers across every published screen
    viewers_changed = pyqtSignal(int)
    # screen id, published? — the control screen reconciles window lifetime
    published_changed = pyqtSignal(str, bool)
    log = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._prefs = settings.load()
        self._provider = None
        self._sockets = ScreenSocketServer(self)
        self._server = WebcastServer(socket_port=self._socket_port)
        self._sockets.viewers_changed.connect(self._on_viewers_changed)
        self._sockets.log.connect(self.log.emit)
        self._subscribed = False

    # ── Wiring ───────────────────────────────────────────────────────────

    def set_window_provider(self, provider) -> None:
        """
        Kept for `ControlScreen`, which still owns window lifetime.

        Nothing here renders a window any more, so this service never asks for
        one — but the *state engine* is a window, and the control screen is
        what decides whether it exists.
        """
        self._provider = provider

    def _socket_port(self) -> int:
        return settings.socket_port(self._prefs["port"])

    def _subscribe(self) -> None:
        """Connect once, on first start. Disconnecting is never needed."""
        if self._subscribed:
            return
        config.team_changed.connect(self._on_anything)
        config.mode_changed.connect(self._on_anything)
        config.screen_setting_changed.connect(self._on_screen_setting)
        config.logs_changed.connect(self._on_anything)
        rotation.advance.connect(self._on_rotation_advance)
        try:
            from app.checklist import checklist
            checklist.items_changed.connect(self._on_anything)
            checklist.item_toggled.connect(self._on_anything)
        except Exception:
            pass
        try:
            from app.judges_slides import judges_slides
            judges_slides.slide_changed.connect(self._on_anything)
            judges_slides.slides_reloaded.connect(self._on_anything)
        except Exception:
            pass
        self._subscribed = True

    # ── State ────────────────────────────────────────────────────────────

    @property
    def listening(self) -> bool:
        return self._server.listening

    @property
    def port(self) -> int:
        return self._server.port

    @property
    def socket_port(self) -> int:
        return self._sockets.port

    @property
    def viewers(self) -> int:
        return self._sockets.viewers()

    def published(self) -> list[str]:
        return list(self._prefs["screens"]) if self._prefs["enabled"] else []

    def is_published(self, screen_id: str) -> bool:
        return screen_id in self.published()

    def reload_settings(self) -> dict:
        self._prefs = settings.load()
        return self._prefs

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> str:
        prefs = self.reload_settings()
        if not prefs["enabled"]:
            return ""
        page_port, bind = int(prefs["port"]), str(prefs["bind"])
        problem = self._server.start(page_port, bind)
        if problem:
            self.log.emit(problem)
            self.state_changed.emit(False, problem)
            return problem
        problem = self._sockets.start(settings.socket_port(page_port), bind)
        if problem:
            # Half a feature is worse than none: a page that loads and then
            # never receives anything looks like a broken screen rather than a
            # port that would not open.
            self._server.stop()
            self.log.emit(problem)
            self.state_changed.emit(False, problem)
            return problem
        self._subscribe()
        self.log.emit(
            f"Pit network screens on {bind}:{page_port} "
            f"(live data on {settings.socket_port(page_port)}) — "
            f"{', '.join(prefs['screens']) or 'no screens selected'}.")
        self.state_changed.emit(True, "")
        return ""

    def stop(self) -> None:
        self._sockets.stop()
        self._server.stop()
        self.state_changed.emit(False, "")

    def restart(self) -> str:
        self.stop()
        return self.start()

    def set_enabled(self, on: bool) -> str:
        self._prefs = settings.save(enabled=bool(on))
        problem = self.restart()
        self._announce_all()
        return problem

    def set_screen_published(self, screen_id: str, on: bool) -> None:
        """
        Publish or unpublish one screen, turning the feature on if it is off.

        **One entry point, one ordering.** The caller asked for this screen on
        the network and should not also have to know there is a master switch
        behind it, or — the part that actually bit once — that flipping the two
        in the wrong order leaves a published screen with no state engine.
        """
        prefs = settings.load()
        wanted = set(prefs["screens"])
        wanted.add(screen_id) if on else wanted.discard(screen_id)
        self._prefs = settings.save(
            screens=[s for s in settings.PUBLISHABLE if s in wanted],
            enabled=True if on else prefs["enabled"])
        if on and not self.listening:
            self.restart()
        self.published_changed.emit(screen_id, self.is_published(screen_id))
        if not on:
            self._sockets.broadcast(
                screen_id, "refused",
                {"reason": "This screen is no longer published."})

    def set_port(self, port: int) -> int:
        self._prefs = settings.save(port=int(port))
        if self.listening:
            self.restart()
        return self._prefs["port"]

    def _announce_all(self) -> None:
        for screen_id in settings.PUBLISHABLE:
            self.published_changed.emit(screen_id, self.is_published(screen_id))

    def push(self, screen_id: str | None = None) -> None:
        """Send current state now — what the control panel's Refresh does."""
        if screen_id is None:
            self._sockets.push_all()
        else:
            self._sockets.push_state(screen_id)

    # ── Pushing ──────────────────────────────────────────────────────────
    #
    # `push_state` is already a no-op when nobody is connected to that screen,
    # so none of this builds a payload for an audience that is not there.

    def _on_anything(self, *_args) -> None:
        self._sockets.push_all()

    def _on_screen_setting(self, screen_id: str, _key: str, _value) -> None:
        if screen_id in settings.PUBLISHABLE:
            self._sockets.push_state(screen_id)

    def _on_rotation_advance(self) -> None:
        # The screens advance their own index on this same signal. Qt runs
        # slots in connection order and this service connects *after* the
        # windows exist, but not dependably — so the push is queued to the
        # back of the event loop rather than racing them, or the page would be
        # sent the slide it is already showing with a fresh dwell clock.
        QTimer.singleShot(0, self._sockets.push_all)

    def _on_viewers_changed(self, _screen_id: str, _count: int) -> None:
        self.viewers_changed.emit(self._sockets.viewers())

    # ── For the panel ────────────────────────────────────────────────────

    def urls(self, host: str) -> dict[str, str]:
        return {s: f"http://{host}:{self.port}/screen/{s}"
                for s in self.published()}


webcast: _WebcastService = LazyProxy("webcast", "init_webcast")  # type: ignore[assignment]


def init_webcast() -> _WebcastService:
    real = _WebcastService()
    webcast._install(real)
    return real
