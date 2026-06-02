"""Presentation Screen B — shell. Switches to lunch overlay when mode is Lunch."""

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QStackedWidget
from PyQt6.QtCore import Qt

from app.config import config
from app.widgets.lunch_overlay import LunchOverlay


class PresentationScreenB(QMainWindow):

    _PAGE_NORMAL = 0
    _PAGE_LUNCH  = 1

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Presentation B")
        self.setMinimumSize(1280, 720)
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.mode_changed.connect(self._on_mode_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)

    def _build_ui(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        # Page 0 — normal content (filled in later)
        normal = QWidget()
        layout = QVBoxLayout(normal)
        lbl = QLabel("Presentation Screen B")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setObjectName("screen_title")
        layout.addWidget(lbl)
        self._stack.addWidget(normal)

        # Page 1 — lunch overlay
        self._lunch = LunchOverlay()
        self._stack.addWidget(self._lunch)

        self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_mode_changed(self, mode: str):
        if mode == "lunch":
            self._stack.setCurrentIndex(self._PAGE_LUNCH)
            self._lunch.apply_fonts()
        else:
            self._stack.setCurrentIndex(self._PAGE_NORMAL)

    def _on_team_changed(self, team):
        pass

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != "presentation_b":
            return
