"""
Project Screen — interactive touch display.

Hosts two switchable views, chosen from the control panel:
  • CAD viewer   — 3D robot model, free orbit + tap-to-isolate subsystems
  • Impact board — tabbed outreach / awards kiosk board (wireframe)

The active view is driven by the per-screen "content" setting
("cad" | "board"). Both honor team + theme commands.
"""

from PyQt6.QtWidgets import QMainWindow, QStackedWidget

from app.cad_assets import cad_assets
from app.config import config
from app.theme import apply_theme
from app.widgets.cad_viewer import CADViewerWidget
from app.widgets.interactive_board import InteractiveBoard

SCREEN_ID = "project"

_PAGE_CAD   = 0
_PAGE_BOARD = 1


class ProjectScreen(QMainWindow):
    """Interactive touch screen: CAD viewer or Impact board. Theme + team aware."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Project")
        self.setMinimumSize(320, 240)
        self._build_ui()
        cad_assets.model_changed.connect(self._viewer.reload_model)
        config.team_changed.connect(self._on_team_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)

        theme = config.screen_theme(SCREEN_ID)
        # Seed the viewer with the current team + theme before it finishes
        # loading; CADViewerWidget replays these on loadFinished.
        self._viewer.set_theme(theme)
        self._viewer.set_accent(config.active_team.primary_color)
        self._board.apply_theme(theme)
        apply_theme(self, theme)
        self._apply_content(config.get(SCREEN_ID, "content", "cad"))

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._viewer = CADViewerWidget()
        self._stack.addWidget(self._viewer)     # 0

        self._board = InteractiveBoard()
        self._stack.addWidget(self._board)       # 1

    def showEvent(self, event):
        super().showEvent(event)
        self._viewer.set_mode("interactive")

    def _apply_content(self, content: str):
        self._stack.setCurrentIndex(
            _PAGE_BOARD if content == "board" else _PAGE_CAD
        )

    def _on_team_changed(self, team):
        self._viewer.set_accent(team.primary_color)
        # InteractiveBoard re-brands itself via its own team_changed hookup.

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != SCREEN_ID:
            return
        if key == "theme":
            apply_theme(self, value)
            self._viewer.set_theme(value)
            self._board.apply_theme(value)
        elif key == "content":
            self._apply_content(value)
