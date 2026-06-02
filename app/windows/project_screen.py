"""Project Screen — shell. Observes AppConfig for team/theme changes."""

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from app.config import config


class ProjectScreen(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pit Display — Project")
        self.setMinimumSize(1280, 720)
        self._build_ui()
        config.team_changed.connect(self._on_team_changed)
        config.screen_setting_changed.connect(self._on_setting_changed)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self._label = QLabel("Project Screen")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setObjectName("screen_title")
        layout.addWidget(self._label)

    def _on_team_changed(self, team):
        pass

    def _on_setting_changed(self, screen: str, key: str, value):
        if screen != "project":
            return
