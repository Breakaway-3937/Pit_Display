"""
Central slide-rotation timer for standard mode.

Usage:
    from app.rotation import rotation
    rotation.advance.connect(my_slot)   # fired every IDLE_MS when mode == standard
    rotation.poke()                     # call after any manual navigation
"""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.config import config
from app.lazy_proxy import LazyProxy

IDLE_MS = 45_000


class RotationManager(QObject):
    """
    The one clock **and the one position** for both overhead screens.

    `app/program.py` pairs what A and B show at each stop; this moves the
    program on every `IDLE_MS` and writes the same `slide_index` to both
    screens, so they can't drift. Screens never advance themselves (two
    self-advancing lists of different lengths are how they drifted). A jump
    to a stop on either screen's picker is mirrored to the other.
    """

    advance = pyqtSignal()          # the dwell restarted (the program moved)
    program_changed = pyqtSignal()  # the list of stops changed: screens rebuild

    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_MS)
        self._timer.timeout.connect(self._step)
        self.index = 0
        self._keys: tuple[str, ...] = ()
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)
        config.logs_changed.connect(self.refresh)
        # Start immediately if already in standard mode
        if config.mode == "standard":
            self._timer.start()

    def poke(self):
        """Restart the countdown — call whenever the user manually navigates."""
        if self._timer.isActive():
            self._timer.start()

    def progress(self) -> float:
        """
        How far through the current dwell we are, 0 → 1.

        The presentation footer draws this as a rail, so a visitor can see that
        something is coming rather than only that something changed. Read from
        the live timer rather than tracked separately: two clocks for one dwell
        is exactly the kind of thing that drifts apart and nobody notices.
        """
        if not self._timer.isActive():
            return 0.0
        remaining = self._timer.remainingTime()
        if remaining < 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - remaining / IDLE_MS))

    def _on_mode_changed(self, mode: str):
        if mode == "standard":
            # Back from judges or lunch: both screens start the program over.
            self._set(0)
            self._timer.start()
        else:
            self._timer.stop()

    # ── The shared program ────────────────────────────────────────────────

    def watch(self) -> None:
        """Recheck the program when its data moves. Called once the services
        exist (main._boot); the rotation is built before them."""
        try:
            from app.db.sync.service import sync
            sync.facts_changed.connect(self.refresh)
            sync.datasets_changed.connect(self.refresh)
        except RuntimeError:
            pass
        try:
            from app.nexus import nexus
            nexus.event_key_changed.connect(self._on_event)
            nexus.status_changed.connect(self._on_event)
        except RuntimeError:
            pass
        self.refresh()

    def _on_event(self, *_args) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the screens only when the list of stops really changed."""
        from app import program
        keys = program.keys()
        if keys == self._keys:
            return
        self._keys = keys
        if self.index >= len(keys):
            self.index = 0
        self.program_changed.emit()
        self._set(self.index)

    def _step(self) -> None:
        from app import program
        n = len(program.keys())
        nxt = (self.index + 1) % max(1, n)
        if nxt == 0:
            # A pass is done: the next pass shows the next facts.
            program.next_facts()
            self.index = 0
            self.refresh()
            self._set(0)
        else:
            self._set(nxt)
        self.advance.emit()

    def _set(self, index: int) -> None:
        from app import program
        self.index = index
        for screen in program.SIDES:
            config.set(screen, "slide_index", index)

    def _on_setting_changed(self, screen: str, key: str, value) -> None:
        """A jump on either screen's picker moves both; a content change on
        either brings its partner to the matching half (app/overhead.py)."""
        from app import program
        if screen not in program.SIDES:
            return
        if key == "content":
            from app import overhead
            overhead.partner_fix(screen, str(value))
            return
        if key != "slide_index":
            return
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        if value != self.index:
            self._set(value)


rotation: RotationManager = LazyProxy("rotation", "init_rotation")  # type: ignore[assignment]


def init_rotation() -> RotationManager:
    """Call once in main(), after init_config()."""
    real = RotationManager()
    rotation._install(real)
    return real
