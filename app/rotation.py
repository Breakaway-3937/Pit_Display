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

    advance = pyqtSignal()  # time to move to the next slide

    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_MS)
        self._timer.timeout.connect(self.advance)
        config.mode_changed.connect(self._on_mode_changed)
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
            self._timer.start()
        else:
            self._timer.stop()


rotation: RotationManager = LazyProxy("rotation", "init_rotation")  # type: ignore[assignment]


def init_rotation() -> RotationManager:
    """Call once in main(), after init_config()."""
    real = RotationManager()
    rotation._install(real)
    return real
