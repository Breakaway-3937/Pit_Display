"""
Manages placement of the four application windows across physical displays.

Screen assignment (configurable):
  Index 0 — Control screen      (operator's primary display)
  Index 1 — Presentation A
  Index 2 — Presentation B
  Index 3 — Project screen

When fewer than 4 screens are present (dev / single-monitor), all windows
open as normal resizable windows tiled across the primary display.
"""

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect


class WindowManager:

    SCREEN_MAP = {
        "control":        0,
        "presentation_a": 1,
        "presentation_b": 2,
        "project":        3,
    }

    def __init__(self, app: QApplication):
        self._app = app
        self._screens = app.screens()
        self._windows: dict[str, QMainWindow] = {}

    @property
    def screen_count(self) -> int:
        return len(self._screens)

    def register(self, role: str, window) -> None:
        self._windows[role] = window

    def place_all(self) -> None:
        multi = self.screen_count >= 4
        for role, window in self._windows.items():
            self._place(role, window, multi)

    def _place(self, role: str, window, fullscreen: bool) -> None:
        preferred_idx = self.SCREEN_MAP.get(role, 0)
        screen_idx = min(preferred_idx, self.screen_count - 1)
        screen = self._screens[screen_idx]

        if fullscreen and role != "control":
            window.windowHandle().setScreen(screen)
            window.setGeometry(screen.geometry())
            window.showFullScreen()
        else:
            window.setGeometry(self._dev_geometry(role))
            window.show()

    def _dev_geometry(self, role: str) -> QRect:
        """Tile 4 windows in a 2×2 grid on the primary screen."""
        geo = self._screens[0].availableGeometry()
        w, h = geo.width() // 2, geo.height() // 2
        offsets = {
            "control":        (0, 0),
            "presentation_a": (w, 0),
            "project":        (0, h),
            "presentation_b": (w, h),
        }
        dx, dy = offsets.get(role, (0, 0))
        return QRect(geo.x() + dx, geo.y() + dy, w, h)

    def refresh_screens(self) -> None:
        self._screens = self._app.screens()
