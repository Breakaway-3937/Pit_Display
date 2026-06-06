"""
Central slide-rotation timer for standard mode.

Usage:
    from app.rotation import rotation
    rotation.advance.connect(my_slot)   # fired every IDLE_MS when mode == standard
    rotation.poke()                     # call after any manual navigation
"""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from app.config import config

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


# ── Lazy proxy (mirrors app.config pattern) ───────────────────────────────────

class _Proxy:
    _real: "RotationManager | None" = None

    def __getattr__(self, name: str):
        if self._real is None:
            raise RuntimeError(
                f"rotation.{name} accessed before init_rotation() was called."
            )
        return getattr(self._real, name)

    def __setattr__(self, name: str, value):
        if name == "_real":
            object.__setattr__(self, name, value)
        else:
            setattr(self._real, name, value)


rotation: RotationManager = _Proxy()  # type: ignore[assignment]


def init_rotation() -> RotationManager:
    """Call once in main(), after init_config()."""
    real = RotationManager()
    rotation._real = real  # type: ignore[attr-defined]
    return real
