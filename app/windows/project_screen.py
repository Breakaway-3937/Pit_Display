"""
Project Screen — the pit-front touch panel.

1080×1920 portrait at standing height. Two faces, chosen from the control
panel by the per-screen `content` setting:

  • `"board"` (default) — the interpretive panel, with the CAD **inside** it as
    its top band. This is the visitor-facing face: the robot, the law, the
    numbers, the programs and the sponsors on one surface, no tabs.
  • `"cad"` — the same viewer alone, filling the panel. Kept for judges, and
    for anyone who wants the robot and nothing else.

**There is one CAD viewer, and it moves between them.** A `QWebEngineView` is a
whole Chromium render process; running a second one so two pages can each own a
copy of the same robot is not a trade worth making. `InteractiveBoard.attach_cad()`
takes it, `detach_cad()` hands it back, and re-parenting is all Qt needs.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

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
        self._apply_content(config.get(SCREEN_ID, "content", "board"))

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._viewer = CADViewerWidget()
        self._cad_page = QWidget()
        page_lay = QVBoxLayout(self._cad_page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._cad_page)    # 0

        self._board = InteractiveBoard()
        self._stack.addWidget(self._board)       # 1

    def showEvent(self, event):
        super().showEvent(event)
        self._viewer.set_mode("interactive")

    def _apply_content(self, content: str):
        """Move the one viewer to whichever face is going on screen."""
        if content == "cad":
            self._board.detach_cad()
            self._cad_page.layout().addWidget(self._viewer)
            self._stack.setCurrentIndex(_PAGE_CAD)
        else:
            self._cad_page.layout().removeWidget(self._viewer)
            self._board.attach_cad(self._viewer)
            self._stack.setCurrentIndex(_PAGE_BOARD)

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
