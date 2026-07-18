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
